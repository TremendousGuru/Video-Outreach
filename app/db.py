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


def _ensure_parent_dir(path: str) -> None:
    """A mounted volume (e.g. Render's /var/data) exists but nested paths may not.
    sqlite3 will not create the directory for us."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            pass

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

DEFAULTS: dict[str, Any] = {
    "sender_name": "",
    "sender_company": "",
    # No sender_email: nothing reads it. The app never sends mail itself - it
    # hands a draft to your own mail app, which already knows your address.
    "offer": "I make short product videos for Shopify stores",
    "tone": "friendly, direct, no fluff, sounds like a real person",
    "subject_style": "mix",
    "optout": True,
    "optout_line": "Not interested? Just reply \"no\" and I won't follow up.",
    # NOTE: no "api_key" here, deliberately. save_settings() only persists keys
    # that appear in DEFAULTS, so leaving it out makes the database physically
    # unable to store a key. Keys come from the environment and nowhere else -
    # see env_api_key().
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
    sent_at TEXT NOT NULL DEFAULT '',
    opened_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
"""

# Columns added after the first release. CREATE TABLE IF NOT EXISTS never alters
# an existing file, so databases made by an older build get them here instead.
_MIGRATIONS: tuple[tuple[str, str], ...] = (
    # When the draft was opened in a mail app. Distinct from sent_at on purpose:
    # tapping "open the draft" is not the same as sending it, and pretending
    # otherwise is what made the list impossible to read.
    ("opened_at", "opened_at TEXT NOT NULL DEFAULT ''"),
)


def _migrate(c: sqlite3.Connection) -> None:
    have = {r["name"] for r in c.execute("PRAGMA table_info(leads)")}
    for name, ddl in _MIGRATIONS:
        if name not in have:
            c.execute(f"ALTER TABLE leads ADD COLUMN {ddl}")
    c.commit()


def state_of(lead: dict[str, Any]) -> str:
    """Which section of the UI this row belongs to.

    A row can be several of these things at once, so the order matters: a lead
    that was opened and then marked sent is "sent", not "opened".

        sent     you pressed send (or marked it)
        opened   the draft was opened but never confirmed - the "did I already
                 email this one?" bucket
        failed   crawl or compose blew up
        to_send  a message is written and waiting
        working  still in the queue / crawling / composing
    """
    if lead.get("status") == "sent" or (lead.get("sent_at") or ""):
        return "sent"
    if lead.get("opened_at"):
        return "opened"
    if lead.get("status") == "failed":
        return "failed"
    if lead.get("status") in ("ready", "crawled"):
        return "to_send"
    return "working"


def mark_opened(lead_id: int) -> None:
    """Record that the draft for this store was opened in a mail app."""
    update_lead(lead_id, opened_at=now())


def unmark_opened(lead_id: int) -> None:
    """Put an opened row back in the to-send list."""
    update_lead(lead_id, opened_at="")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def env_api_key() -> str:
    """The one and only source of the API key.

    Set OPENAI_API_KEY in the host's environment (Streamlit secrets, a shell
    export, Render/space env vars - the name stays OPENAI_API_KEY even when the
    key is Groq's or Gemini's, because it is the OpenAI-compatible slot). The app
    can report whether a key is present; it can never change it. That asymmetry
    is the point: a key typed into a web form would be written to a database that
    free hosting wipes and a breach exposes, and would then outrank the key the
    operator actually configured.
    """
    return (os.environ.get("OPENAI_API_KEY") or "").strip()


def mask_key(key: str) -> str:
    """Enough of a key to recognise which one it is, not enough to use it."""
    if not key:
        return ""
    if len(key) <= 12:
        return "*" * len(key)
    return f"{key[:4]}...{key[-4:]}"


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _ensure_parent_dir(DB_PATH)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
            _migrate(_conn)
            _conn.commit()
            # Older versions let a key be saved from the settings form. Wipe any
            # such row on open, so a stale key cannot survive in the database,
            # ride along in a backup file, or silently outrank the configured one.
            _conn.execute("DELETE FROM settings WHERE k='api_key'")
            _conn.commit()
            # Anything mid-flight when the server died goes back to the queue.
            _conn.execute("UPDATE leads SET status='crawled' WHERE status='crawling' AND facts != ''")
            _conn.execute("UPDATE leads SET status='pending' WHERE status IN ('crawling','composing')")
            _conn.commit()
        return _conn


def load_settings() -> dict[str, Any]:
    s = dict(DEFAULTS)
    # The key is never read from the database - only from the environment. Any
    # saved row is ignored here even if one somehow exists.
    s["api_key"] = env_api_key()
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
        "opened_at",
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
    d["state"] = state_of(d)
    return d


def list_leads() -> list[dict[str, Any]]:
    rows = conn().execute(
        """SELECT id, email, domain, store_name, source_name, status, stage, error, flags,
                  subjects, subject, length(body) AS body_len, notes, engine, created_at, sent_at,
                  opened_at, (facts != '') AS has_facts
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
        d["state"] = state_of(d)
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


# --------------------------------------------------------------- backup/restore
# Free hosting tiers have no persistent disk, so the database can vanish on a
# deploy or an idle restart. These two functions make that recoverable: download
# everything as a single JSON file, upload it later to get exactly back to where
# you were.
#
# The API key is deliberately NOT included. A backup file is the kind of thing
# that ends up in a Downloads folder or a chat message, and it already contains
# real email addresses - no need to add a live credential to that.

BACKUP_VERSION = 1
LEAD_COLUMNS = (
    "email", "domain", "store_name", "source_name", "status", "stage", "error",
    "flags", "facts", "subjects", "subject", "body", "notes", "engine",
    "created_at", "updated_at", "sent_at", "opened_at",
)


def export_all(include_api_key: bool = False) -> dict[str, Any]:
    c = conn()
    with _lock:
        leads = [dict(r) for r in c.execute(f"SELECT {', '.join(LEAD_COLUMNS)} FROM leads ORDER BY id")]
        settings_rows = {r["k"]: r["v"] for r in c.execute("SELECT k, v FROM settings")}
    if not include_api_key:
        settings_rows.pop("api_key", None)
    return {
        "format": "outreach-studio-backup",
        "version": BACKUP_VERSION,
        "exported_at": now(),
        "lead_count": len(leads),
        "settings": settings_rows,
        "leads": leads,
    }


def import_all(payload: dict, mode: str = "replace") -> dict[str, int]:
    """Restore a backup. mode='replace' wipes leads first; mode='merge' keeps
    existing rows and skips duplicates."""
    if not isinstance(payload, dict) or payload.get("format") != "outreach-studio-backup":
        raise ValueError("That file is not an Outreach Studio backup.")
    try:
        version = int(payload.get("version", 0))
    except (TypeError, ValueError):
        version = 0
    if version > BACKUP_VERSION:
        raise ValueError(
            f"Backup was made by a newer version (v{version}). Update the app first."
        )

    leads = payload.get("leads") or []
    if not isinstance(leads, list):
        raise ValueError("Backup file is malformed: 'leads' is not a list.")

    c = conn()
    added = skipped = 0
    ts = now()
    with _lock:
        if mode != "merge":
            c.execute("DELETE FROM leads")

        existing = {
            ((r["email"] or "").lower(), (r["domain"] or "").lower())
            for r in c.execute("SELECT email, domain FROM leads")
        }
        for row in leads:
            if not isinstance(row, dict):
                skipped += 1
                continue
            email = (row.get("email") or "").strip().lower()
            domain = (row.get("domain") or "").strip().lower()
            if not email and not domain:
                skipped += 1
                continue
            if (email, domain) in existing:
                skipped += 1
                continue
            existing.add((email, domain))

            def val(key: str, default: str = "") -> str:
                v = row.get(key, default)
                if v is None:
                    return default
                if isinstance(v, (list, dict)):
                    return json.dumps(v)
                return str(v)

            c.execute(
                f"""INSERT INTO leads ({', '.join(LEAD_COLUMNS)})
                    VALUES ({', '.join('?' * len(LEAD_COLUMNS))})""",
                (
                    val("email"), val("domain"), val("store_name"), val("source_name"),
                    val("status", "pending") or "pending", val("stage"), val("error"),
                    val("flags", "[]") or "[]", val("facts"), val("subjects", "[]") or "[]",
                    val("subject"), val("body"), val("notes"), val("engine"),
                    val("created_at", ts) or ts, val("updated_at", ts) or ts, val("sent_at"),
                    val("opened_at"),
                ),
            )
            added += 1

        settings = payload.get("settings") or {}
        if isinstance(settings, dict):
            for k, v in settings.items():
                if k in DEFAULTS and k != "api_key":
                    c.execute(
                        "INSERT INTO settings (k, v) VALUES (?, ?) "
                        "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                        (k, v if isinstance(v, str) else json.dumps(v)),
                    )
        c.commit()
    return {"added": added, "skipped": skipped, "leads_in_file": len(leads)}
