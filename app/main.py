"""Local outreach studio: upload -> crawl -> compose -> send."""
from __future__ import annotations

import asyncio
import csv
import html
import io
import json
import os
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from . import auth
from . import compose as composer
from . import crawler, db, ingest, pipeline

BASE = Path(__file__).resolve().parent
app = FastAPI(title="Shopify Outreach Studio", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

# Paths reachable without signing in. Static files carry no secrets, /health is
# for the host's health checker, and the login endpoints are how you get in.
PUBLIC_PATHS = ("/health", "/login", "/logout", "/favicon.ico")


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    if not auth.auth_enabled():
        return await call_next(request)
    path = request.url.path
    if path.startswith("/static/") or path in PUBLIC_PATHS:
        return await call_next(request)
    if auth.token_is_valid(request.cookies.get(auth.COOKIE_NAME)):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Not signed in. Reload the page and log in."}, status_code=401)
    return RedirectResponse("/login", status_code=302)


def _login_page(error: str = "") -> str:
    e = html.escape
    msg = f'<p class="err">{e(error)}</p>' if error else ""

    # Hugging Face serves Spaces inside an iframe on huggingface.co, and browsers
    # refuse to store or send our session cookie in that cross-site context. The
    # sign-in appears to do nothing. Point people at the direct URL instead.
    tip = ""
    space_host = (os.environ.get("SPACE_HOST") or "").strip()
    if space_host:
        tip = (
            '<p class="tip">Tip: if you opened this inside huggingface.co, sign-in '
            "won't stick there. Use the direct address instead:<br>"
            f'<a href="https://{e(space_host)}">https://{e(space_host)}</a></p>'
        )
    elif os.environ.get("RENDER"):
        tip = ""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Outreach Studio - sign in</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:#0b0d10; color:#e6e9ee; font:16px/1.5 -apple-system,Roboto,Helvetica,Arial,sans-serif; }}
  form {{ background:#12151a; border:1px solid #232932; border-radius:14px; padding:26px;
          width:min(360px,90vw); }}
  h1 {{ font-size:17px; margin:0 0 4px; }}
  p.sub {{ color:#8b95a5; font-size:13px; margin:0 0 18px; }}
  input {{ width:100%; box-sizing:border-box; padding:12px; border-radius:10px; border:1px solid #2e3641;
           background:#0e1116; color:#e6e9ee; font-size:16px; margin-bottom:12px; }}
  input:focus {{ outline:none; border-color:#5eead4; }}
  button {{ width:100%; padding:13px; border:0; border-radius:10px; background:#5eead4; color:#052e2b;
            font-weight:700; font-size:15px; cursor:pointer; }}
  .err {{ background:#2a1010; border:1px solid #7f1d1d; color:#fca5a5; padding:10px; border-radius:9px;
          font-size:13px; margin:0 0 14px; }}
  .tip {{ color:#8b95a5; font-size:12px; margin:14px 0 0; line-height:1.5; word-break:break-all; }}
  .tip a {{ color:#5eead4; }}
</style></head>
<body>
  <form method="post" action="/login">
    <h1>Outreach Studio</h1>
    <p class="sub">Enter the password you set for this deployment.</p>
    {msg}
    <input type="password" name="password" placeholder="Password" autofocus autocomplete="current-password">
    <button type="submit">Sign in</button>
    {tip}
  </form>
</body></html>"""


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if not auth.auth_enabled() or auth.token_is_valid(request.cookies.get(auth.COOKIE_NAME)):
        return RedirectResponse("/", status_code=302)
    return HTMLResponse(_login_page())


@app.post("/login")
async def login_submit(request: Request, password: str = Form(default="")):
    if not auth.auth_enabled():
        return RedirectResponse("/", status_code=302)
    ip = (request.client.host if request.client else "?") or "?"
    if auth.too_many_attempts(ip):
        return HTMLResponse(_login_page("Too many attempts. Wait five minutes."), status_code=429)
    if not auth.password_is_correct(password):
        auth.record_attempt(ip)
        return HTMLResponse(_login_page("Wrong password."), status_code=401)
    auth.clear_attempts(ip)
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(
        auth.COOKIE_NAME, auth.session_token(), httponly=True, samesite="lax",
        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https",
        max_age=60 * 60 * 24 * 30, path="/",
    )
    return resp


@app.get("/logout")
async def logout(request: Request):
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return resp


@app.get("/", response_class=HTMLResponse)
async def index():
    return (BASE / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/health")
async def health():
    return {
        "ok": True,
        "auth": "enabled" if auth.auth_enabled() else "open",
        "hosted": auth.hosted_mode(),
        "leads": len(db.list_leads()),
    }


@app.get("/api/session")
async def session_info(request: Request):
    """What the UI needs to know about its own session."""
    return {
        "auth_required": auth.auth_enabled(),
        "signed_in": auth.token_is_valid(request.cookies.get(auth.COOKIE_NAME)) if auth.auth_enabled() else True,
        "hosted": auth.hosted_mode(),
        "db_path": str(db.DB_PATH),
        "warning": "" if not auth.hosted_mode() else persistence_warning(),
    }


def persistence_warning() -> str:
    """Warn when the database looks like it's on the container's ephemeral disk.

    Render only attaches a disk if you mount one; without it every deploy wipes
    the database. We can't detect a mount with certainty, so: trust an explicit
    OUTREACH_PERSISTENT=1, otherwise assume persistence for the usual mount
    paths, and warn for anything else that isn't a local dev directory.
    """
    if os.environ.get("OUTREACH_PERSISTENT") == "1":
        return ""
    path = os.path.abspath(str(db.DB_PATH))
    if path.startswith(("/var/data/", "/data/", "/mnt/", "/opt/render/project/data/")):
        return ""
    return (
        f"The database is at {path}, which is not a mounted disk. "
        "Every deploy or restart will wipe your leads and messages."
    )


# ------------------------------------------------------------------ settings

def _key_report() -> dict[str, Any]:
    """Everything the interface is allowed to know about the key: whether one
    exists, which one it looks like, and where it came from. Never the key."""
    key = db.env_api_key()
    return {
        "has_api_key": bool(key),
        "key_hint": db.mask_key(key),
        "key_source": "environment" if key else "none",
    }


@app.get("/api/settings")
async def get_settings():
    s = db.load_settings()
    s.pop("api_key", None)
    s.update(_key_report())
    return s


@app.post("/api/settings")
async def post_settings(patch: dict[str, Any] = Body(...)):
    """Save settings. Key fields are accepted and dropped, not stored.

    Rejecting them outright would break older clients for no benefit; ignoring
    them quietly while telling the caller keeps the contract honest. There is
    deliberately no path from this endpoint to the key.
    """
    ignored = any(patch.pop(f, None) is not None for f in ("api_key", "clear_api_key"))
    s = db.save_settings(patch)
    s.pop("api_key", None)
    s.update(_key_report())
    if ignored:
        s["api_key_ignored"] = True
    return s


@app.post("/api/test-key")
async def test_key():
    s = db.load_settings()
    if not (s.get("api_key") or "").strip():
        raise HTTPException(
            400,
            "No API key found. Set OPENAI_API_KEY in the server's environment "
            "(the host's secrets or env settings) and restart. Keys cannot be "
            "entered or changed from the web interface.",
        )
    facts = {
        "store_name": "Test Goods Co", "domain": "testgoods.com", "platform": "shopify",
        "tagline": "Small-batch candles poured in Portland",
        "products": [{"title": "Cedar & Smoke Candle", "price": "28.0", "type": "Candles"}],
        "signals": ["small-batch"], "signal_phrases": ["small-batch production"],
    }
    try:
        out = await composer.compose_ai(facts, s, store_hint="Test Goods Co")
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=200)
    return {"ok": True, "engine": out.get("engine"), "sample_subject": (out.get("subjects") or [""])[0],
            "sample_body": out.get("body", "")[:400]}


# ------------------------------------------------------------------ ingest

@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(default=[]), paste: str = Form(default="")):
    rows: list[dict] = []
    reports: list[dict] = []
    for f in files or []:
        data = await f.read()
        if not data:
            continue
        try:
            parsed = ingest.parse_file(f.filename or "upload.csv", data)
        except Exception as e:  # noqa: BLE001
            reports.append({"file": f.filename, "error": str(e)[:200], "added": 0})
            continue
        rows.extend(parsed)
        reports.append({"file": f.filename, "added": len(parsed), "error": ""})
    if (paste or "").strip():
        parsed = ingest.parse_pasted(paste)
        rows.extend(parsed)
        reports.append({"file": "pasted text", "added": len(parsed), "error": ""})

    if not rows:
        return {"added": 0, "rows": [], "reports": reports,
                "message": "Nothing usable found. Each row needs an email address and/or a store domain."}

    inserted = db.insert_leads(rows)
    return {"added": len(inserted), "reports": reports, "rows": rows[:40]}


@app.get("/api/template.csv")
async def template_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["email", "domain", "store_name"])
    w.writerow(["hello@deathwishcoffee.com", "deathwishcoffee.com", "Death Wish Coffee"])
    w.writerow(["", "browngirljane.com", "BROWN GIRL Jane"])
    w.writerow(["team@allbirds.com", "allbirds.com", ""])
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="leads-template.csv"'},
    )


# ------------------------------------------------------------------ leads

@app.get("/api/leads")
async def get_leads():
    return {"leads": db.list_leads()}


@app.get("/api/leads/{lead_id}")
async def get_lead(lead_id: int):
    lead = db.get_lead(lead_id, full=True)
    if not lead:
        raise HTTPException(404, "No such lead")
    return lead


@app.patch("/api/leads/{lead_id}")
async def patch_lead(lead_id: int, patch: dict[str, Any] = Body(...)):
    if not db.get_lead(lead_id):
        raise HTTPException(404, "No such lead")
    allowed = {k: v for k, v in patch.items() if k in {"email", "subject", "body", "status", "notes", "domain", "store_name"}}
    if allowed.get("status") == "sent" and not allowed.get("sent_at"):
        allowed["sent_at"] = db.now()
    db.update_lead(lead_id, **allowed)
    return db.get_lead(lead_id)


@app.delete("/api/leads")
async def delete_leads(keep: str = "all"):
    db.clear_leads(keep=None if keep == "all" else keep)
    return {"ok": True}


@app.post("/api/leads/{lead_id}/recrawl")
async def recrawl_lead(lead_id: int):
    lead = db.get_lead(lead_id, full=True)
    if not lead:
        raise HTTPException(404, "No such lead")
    db.update_lead(lead_id, facts="", status="pending")
    run = pipeline.start_run([lead_id], db.load_settings())
    return {"run_id": run.id}


@app.post("/api/leads/{lead_id}/compose")
async def compose_lead(lead_id: int):
    lead = db.get_lead(lead_id, full=True)
    if not lead:
        raise HTTPException(404, "No such lead")
    if not lead.get("facts"):
        run = pipeline.start_run([lead_id], db.load_settings())
        return {"run_id": run.id, "note": "no facts yet - crawling first"}
    run_id = f"compose-{db.now()}"
    pipeline.SUBSCRIBERS.setdefault(run_id, [])
    settings = db.load_settings()

    async def go():
        lead2 = db.get_lead(lead_id, full=True)
        facts = lead2.get("facts") or {}
        if facts and "signal_phrases" not in facts:
            facts["signal_phrases"] = crawler.signal_phrases(facts.get("signals") or [])
        if facts and "strong_signals" not in facts:
            facts["strong_signals"] = crawler.strong_signal_phrases(facts.get("signals") or [])
        db.update_lead(lead_id, status="composing")
        pipeline.bus_publish(run_id, {"type": "update", "id": lead_id, "status": "composing"})
        out = await composer.compose(facts, settings, store_hint=facts.get("store_name") or "there")
        subs = out.get("subjects") or [composer.local_subject_for(facts, settings)]
        db.update_lead(lead_id, status="ready", subjects=subs, subject=subs[0], body=out.get("body", ""),
                       engine=out.get("engine", ""), notes=(out.get("hook") or "")[:300], error="")
        pipeline.bus_publish(run_id, {"type": "update", "id": lead_id, "status": "ready", "subjects": subs,
                                      "subject": subs[0], "body": out.get("body", ""), "engine": out.get("engine", "")})
        pipeline.bus_publish(run_id, {"type": "done"})

    asyncio.get_running_loop().create_task(go())
    return {"run_id": run_id}


# ------------------------------------------------------------------ crawl runs

@app.post("/api/crawl")
async def start_crawl(payload: dict[str, Any] = Body(default={})):
    ids = payload.get("ids") or None
    statuses = payload.get("statuses")
    if ids:
        ids = [int(i) for i in ids]
    else:
        ids = db.lead_ids(statuses=statuses)
        if not payload.get("redownload"):
            ids = [i for i in ids if db.get_lead(i, full=True).get("status") not in ("ready",)]
    if not ids:
        raise HTTPException(400, "Nothing to crawl. Upload a list first.")
    missing = [i for i in ids if not db.get_lead(i, full=True)]
    if missing:
        raise HTTPException(400, "Some rows no longer exist. Refresh the page.")
    run = pipeline.start_run(ids, db.load_settings())
    return run.snapshot()


# NOTE: declared before /api/run/{run_id} on purpose - FastAPI matches routes in
# declaration order, so the catch-all would otherwise swallow "current".
@app.get("/api/run/current")
async def run_current():
    """Lets a page that refreshed mid-run reattach instead of making you guess."""
    live = [r for r in pipeline.RUNNING.values() if not r.finished]
    if not live:
        return {"state": "idle"}
    run = live[-1]
    return {"state": "running", **run.snapshot()}


@app.get("/api/run/{run_id}")
async def run_status(run_id: str):
    run = pipeline.RUNNING.get(run_id)
    if not run:
        return {"state": "unknown"}
    return run.snapshot()


@app.get("/api/events/{run_id}")
async def events(run_id: str, request: Request):
    q = pipeline.subscribe(run_id)

    async def gen():
        try:
            run = pipeline.RUNNING.get(run_id)
            yield f"data: {json.dumps({'type': 'hello', **(run.snapshot() if run else {})})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    r2 = pipeline.RUNNING.get(run_id)
                    if r2 and r2.finished and q.empty():
                        break
                    continue
                yield f"data: {json.dumps(ev)}\n\n"
                if ev.get("type") == "done":
                    break
        finally:
            pipeline.unsubscribe(run_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive",
    })


@app.post("/api/run/{run_id}/cancel")
async def cancel_run(run_id: str):
    run = pipeline.RUNNING.get(run_id)
    if run:
        run.cancel = True
    return {"ok": True}


# ------------------------------------------------------------------ export

def _export_rows(ids: list[int] | None) -> list[dict]:
    out = []
    for lead in db.list_leads():
        if ids and lead["id"] not in ids:
            continue
        full = db.get_lead(lead["id"], full=True)
        if not full:
            continue
        out.append(full)
    return out


@app.get("/api/export.csv")
async def export_csv(scope: str = "ready"):
    leads = _export_rows(None)
    if scope == "ready":
        leads = [l for l in leads if l.get("status") in ("ready", "sent")]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["email", "store_name", "domain", "subject", "body", "status", "engine", "hook", "sent_at"])
    for l in leads:
        body = l.get("body") or ""
        w.writerow([
            l.get("email", ""), l.get("store_name", ""), l.get("domain", ""),
            l.get("subject", ""), body.replace("\n", "\\n"),
            l.get("status", ""), l.get("engine", ""), l.get("notes", ""), l.get("sent_at", ""),
        ])
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="personalized-messages.csv"'},
    )


@app.get("/api/backup")
async def backup_download():
    """Everything, as one file. Free hosts have no persistent disk, so this is
    what saves you when a redeploy wipes the database."""
    import time as _time

    payload = db.export_all()
    stamp = _time.strftime("%Y%m%d-%H%M")
    return Response(
        content=json.dumps(payload, indent=1, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="outreach-backup-{stamp}.json"'},
    )


@app.post("/api/restore")
async def restore_upload(file: UploadFile = File(...), mode: str = Form(default="replace")):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "That file is empty.")
    if len(raw) > 40 * 1024 * 1024:
        raise HTTPException(400, "That file is larger than 40 MB - probably not a backup.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(400, "Could not read that as JSON. Pick the file you downloaded from Backup.")
    try:
        result = db.import_all(payload, mode="merge" if mode == "merge" else "replace")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@app.get("/api/export.json")
async def export_json(scope: str = "ready"):
    leads = _export_rows(None)
    if scope == "ready":
        leads = [l for l in leads if l.get("status") in ("ready", "sent")]
    pack = [{
        "to": l.get("email", ""), "store_name": l.get("store_name", ""), "domain": l.get("domain", ""),
        "subject": l.get("subject", ""), "body": l.get("body", ""),
        "subjects": l.get("subjects", []), "engine": l.get("engine", ""),
        "hook": l.get("notes", ""), "status": l.get("status", ""),
        "facts": l.get("facts", {}),
    } for l in leads]
    return Response(
        content=json.dumps(pack, indent=2), media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="personalized-messages.json"'},
    )


def run(host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    host = host or os.environ.get("HOST", "0.0.0.0")
    port = int(port or os.environ.get("PORT", "8848"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()
