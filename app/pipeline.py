"""Batch orchestrator: crawl -> compose -> ready. With a live SSE event stream."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import defaultdict
from typing import Any

import httpx

from . import compose as composer
from . import crawler, db

RUNNING: dict[str, "Run"] = {}
SUBSCRIBERS: dict[str, list[asyncio.Queue]] = defaultdict(list)


class Run:
    def __init__(self, run_id: str, ids: list[int], settings: dict):
        self.id = run_id
        self.ids = ids
        self.settings = settings
        self.cancel = False
        self.done_ids: set[int] = set()
        self.counts: dict[str, int] = defaultdict(int)
        self.counts["total"] = len(ids)
        self.started = time.time()
        self.finished = False
        self.task: asyncio.Task | None = None

    def snapshot(self) -> dict:
        return {
            "run_id": self.id,
            "state": "finished" if self.finished else "running",
            "total": self.counts["total"],
            "processed": len(self.done_ids),
            "ok": self.counts["ok"],
            "failed": self.counts["failed"],
            "ready": self.counts["ready"],
            "elapsed": round(time.time() - self.started, 1),
        }


def bus_publish(run_id: str, event: dict) -> None:
    for q in list(SUBSCRIBERS.get(run_id, [])):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


def subscribe(run_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    SUBSCRIBERS[run_id].append(q)
    return q


def unsubscribe(run_id: str, q: asyncio.Queue) -> None:
    if q in SUBSCRIBERS.get(run_id, []):
        SUBSCRIBERS[run_id].remove(q)


async def _process_one(lead_id: int, fetcher: crawler.Fetcher, settings: dict, run_id: str) -> dict:
    lead = db.get_lead(lead_id)
    if not lead:
        return {"id": lead_id, "status": "failed", "error": "lead vanished"}

    t0 = time.time()
    db.update_lead(lead_id, status="crawling", stage="fetching site", error="")
    bus_publish(run_id, {"type": "stage", "id": lead_id, "status": "crawling", "stage": "fetching site"})

    # If we already have a fact sheet we only re-run the writing step.
    facts = {}
    if lead.get("has_facts"):
        full = db.get_lead(lead_id, full=True)
        facts = full.get("facts") or {}

    if not facts:
        result = await crawler.crawl_store(fetcher, lead, settings)
        if not result["ok"]:
            db.update_lead(lead_id, status="failed", stage="", error=result["error"], flags=result.get("flags") or [])
            bus_publish(run_id, {"type": "update", "id": lead_id, "status": "failed", "error": result["error"]})
            return {"id": lead_id, "status": "failed"}
        facts = result["facts"]
        facts["signal_phrases"] = crawler.signal_phrases(facts.get("signals") or [])
        facts["strong_signals"] = crawler.strong_signal_phrases(facts.get("signals") or [])
        patch: dict[str, Any] = {"facts": facts, "flags": result["flags"]}
        if result.get("store_name_found"):
            patch["store_name"] = result["store_name_found"]
        if result.get("email_found") and not lead.get("email"):
            patch["email"] = result["email_found"]
            result["flags"] = [
                f for f in result["flags"]
                if not f.startswith("no email yet")
            ] + [f"email auto-found on the site: {result['email_found']}"]
            patch["flags"] = result["flags"]
        db.update_lead(lead_id, status="crawled", stage="crawling done", **patch)
        bus_publish(run_id, {
            "type": "update", "id": lead_id, "status": "crawled",
            "store_name": patch.get("store_name", lead.get("store_name", "")),
            "note": result.get("note", ""), "flags": result["flags"],
        })

    if not settings.get("auto_compose", True):
        bus_publish(run_id, {"type": "update", "id": lead_id, "status": "crawled", "done": True})
        return {"id": lead_id, "status": "crawled"}

    db.update_lead(lead_id, status="composing", stage="writing message")
    bus_publish(run_id, {"type": "stage", "id": lead_id, "stage": "writing message", "status": "composing"})

    store_hint = facts.get("store_name") or lead.get("store_name") or "there"
    out = await composer.compose(facts, settings, store_hint=store_hint)
    subjects = out.get("subjects") or [composer.local_subject_for(facts, settings)]
    body = out.get("body") or ""

    if not body.strip():
        db.update_lead(lead_id, status="failed", error="composer returned an empty body")
        bus_publish(run_id, {"type": "update", "id": lead_id, "status": "failed", "error": "empty body"})
        return {"id": lead_id, "status": "failed"}

    db.update_lead(
        lead_id,
        status="ready",
        stage="",
        subjects=subjects,
        subject=subjects[0],
        body=body,
        engine=out.get("engine", ""),
        notes=(out.get("hook") or "")[:300],
    )
    ev = {
        "type": "update", "id": lead_id, "status": "ready",
        "subjects": subjects, "subject": subjects[0], "body": body,
        "engine": out.get("engine", ""), "elapsed": round(time.time() - t0, 1),
        "hook": out.get("hook", ""),
    }
    if out.get("fallback_reason"):
        ev["warning"] = f"AI failed, used template: {out['fallback_reason']}"
    bus_publish(run_id, ev)
    return {"id": lead_id, "status": "ready"}


async def run_batch(run_id: str, ids: list[int], settings: dict) -> None:
    run = RUNNING.get(run_id)
    if run is None:
        return
    concurrency = max(1, min(10, int(settings.get("concurrency", 4) or 4)))
    delay = float(settings.get("request_delay", 0.5) or 0)
    sem = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)
    headers = dict(crawler.HEADERS)

    async with httpx.AsyncClient(
        headers=headers, limits=limits, follow_redirects=True, verify=False,
        timeout=httpx.Timeout(25.0, connect=12.0),
    ) as client:
        fetcher = crawler.Fetcher(client, delay=delay, respect_robots=bool(settings.get("respect_robots", True)))

        async def guarded(lid: int):
            async with sem:
                if run.cancel:
                    return
                try:
                    res = await _process_one(lid, fetcher, settings, run_id)
                except Exception as e:  # noqa: BLE001
                    db.update_lead(lid, status="failed", stage="", error=str(e)[:200])
                    bus_publish(run_id, {"type": "update", "id": lid, "status": "failed", "error": str(e)[:200]})
                    res = {"id": lid, "status": "failed"}
                run.done_ids.add(lid)
                run.counts["ok" if res["status"] != "failed" else "failed"] += 1
                if res["status"] in ("ready", "crawled"):
                    run.counts["ready"] += 1
                bus_publish(run_id, {"type": "progress", **run.snapshot()})
                await asyncio.sleep(0.05)

        await asyncio.gather(*(guarded(lid) for lid in ids))

    run.finished = True
    bus_publish(run_id, {"type": "done", **run.snapshot()})


def start_run(ids: list[int], settings: dict) -> Run:
    run_id = f"run-{int(time.time())}"
    run = Run(run_id, ids, settings)
    for lid in ids:
        db.update_lead(lid, status="pending", stage="queued", error="")
    RUNNING[run_id] = run
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        raise RuntimeError("start_run must be called from the event loop")
    run.task = loop.create_task(run_batch(run_id, ids, settings))
    return run
