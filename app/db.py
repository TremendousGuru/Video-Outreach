"""SQLite persistence. Everything lives in one local file: outreach.db"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

DB_PATH = os.environ.get(
    "OUTREACH_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outreach.db")
)

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

DEFAULTS: dict[str, Any] = {
    "sender_name": "",
    "sender_company": "",
    "sender_email": "",
    "offer": "I make short product videos for Shopify stores",
    "tone": "friendly, direct, no fluff, sounds like a real person",
    "subject_style": "mix",
    "optout": True,
    "optout_line": "Not interested? Just reply \"no\" and I won't follow up.",
    "api_key": "",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "use_ai": True,
    "concurrency": 4,
    "request_delay": 0.5,
    "max_products": 25,
    "max_pages": 5,
    "auto_compose": True,
    "respect_robots": True,
    "find_missing_emails": True,
    "subject_hint": "is this store active",
    "video_line": "I already put together a short video for one of your products",
    "cta": "Want me to send it over?",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL DEFAULT '',
    domain TEXT NOT NULL DEFAULT '',
    store_name TEXT NOT NULL DEFAULT '',
    source_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    stage TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    flags TEXT NOT NULL DEFAULT '[]',
    facts TEXT NOT NULL DEFAULT '',
    subjects TEXT NOT NULL DEFAULT '[]',
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    engine TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
            _conn.commit()
            # Anything mid-flight when the server died goes back to the queue.
            _conn.execute("UPDATE leads SET status='crawled' WHERE status='crawling' AND facts != ''")
            _conn.execute("UPDATE leads SET status='pending' WHERE status IN ('crawling','composing')")
            _conn.commit()
        return _conn


def load_settings() -> dict[str, Any]:
    s = dict(DEFAULTS)
    # Environment first, so an explicit choice saved in the app still wins.
    env_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_key:
        s["api_key"] = env_key
    for env_name, key in (("OPENAI_BASE_URL", "base_url"), ("OPENAI_MODEL", "model")):
        v = os.environ.get(env_name, "").strip()
        if v:
            s[key] = v
    for row in conn().execute("SELECT k, v FROM settings"):
        try:
            s[row["k"]] = json.loads(row["v"])
        except (json.JSONDecodeError, TypeError):
            s[row["k"]] = row["v"]
    return s


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    c = conn()
    with _lock:
        for k, v in patch.items():
            if k not in DEFAULTS:
                continue
            c.execute(
                "INSERT INTO settings (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (k, json.dumps(v)),
            )
        c.commit()
    return load_settings()


def insert_leads(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows: {email, domain, store_name, flags}. Skips exact (email, domain) duplicates."""
    c = conn()
    out: list[dict[str, Any]] = []
    ts = now()
    with _lock:
        for r in rows:
            email = (r.get("email") or "").strip().lower()
            domain = (r.get("domain") or "").strip().lower()
            if not email and not domain:
                continue
            dup = c.execute(
                "SELECT id FROM leads WHERE email=? AND domain=? LIMIT 1", (email, domain)
            ).fetchone()
            if dup:
                continue
            cur = c.execute(
                """INSERT INTO leads (email, domain, store_name, source_name, status, flags, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)""",
                (
                    email,
                    domain,
                    (r.get("store_name") or "").strip(),
                    (r.get("store_name") or "").strip(),
                    json.dumps(r.get("flags") or []),
                    ts,
                    ts,
                ),
            )
            out.append({"id": cur.lastrowid})
        c.commit()
    return out


def update_lead(lead_id: int, **fields: Any) -> None:
    allowed = {
        "email", "domain", "store_name", "status", "stage", "error", "flags", "facts",
        "subjects", "subject", "body", "notes", "engine", "sent_at", "source_name",
    }
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in {"flags", "facts", "subjects"} and not isinstance(v, str):
            v = json.dumps(v)
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return
    sets.append("updated_at=?")
    vals.append(now())
    vals.append(lead_id)
    c = conn()
    with _lock:
        c.execute(f"UPDATE leads SET {', '.join(sets)} WHERE id=?", vals)
        c.commit()


def get_lead(lead_id: int, full: bool = False) -> dict[str, Any] | None:
    row = conn().execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    for k in ("flags", "subjects"):
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except (json.JSONDecodeError, TypeError):
            d[k] = []
    if full:
        try:
            d["facts"] = json.loads(d.get("facts") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["facts"] = {}
    else:
        d["has_facts"] = bool(d.pop("facts", ""))
    return d


def list_leads() -> list[dict[str, Any]]:
    rows = conn().execute(
        """SELECT id, email, domain, store_name, source_name, status, stage, error, flags,
                  subjects, subject, length(body) AS body_len, notes, engine, created_at, sent_at,
                  (facts != '') AS has_facts
           FROM leads ORDER BY id"""
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("flags", "subjects"):
            try:
                d[k] = json.loads(d.get(k) or "[]")
            except (json.JSONDecodeError, TypeError):
                d[k] = []
        d["has_facts"] = bool(d.get("has_facts"))
        out.append(d)
    return out


def lead_ids(scope: str = "all", ids: list[int] | None = None, statuses: list[str] | None = None) -> list[int]:
    if ids:
        return ids
    q = "SELECT id FROM leads"
    params: list[Any] = []
    if statuses:
        q += f" WHERE status IN ({','.join('?' * len(statuses))})"
        params += statuses
    q += " ORDER BY id"
    return [r["id"] for r in conn().execute(q, params).fetchall()]


def clear_leads(keep: str | None = None) -> None:
    c = conn()
    with _lock:
        if keep == "ready":
            c.execute("DELETE FROM leads WHERE status NOT IN ('ready','sent')")
        else:
            c.execute("DELETE FROM leads")
        c.commit()
