"""Single-user session auth. No external dependencies.

Design notes, deliberately simple:
  - One shared password (APP_PASSWORD). This is a personal tool, not a
    multi-tenant app, so there are no user accounts to manage.
  - The session cookie is an HMAC of the password, not a random per-login
    token. That means no server-side session store to lose on restart - which
    matters on a host that recycles containers.
  - If APP_PASSWORD is empty the app runs wide open, which is what you want
    locally. On a public host it MUST be set; the app refuses to look healthy
    without it when running in hosted mode.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections import defaultdict

COOKIE_NAME = "outreach_session"
_SALT = b"outreach-studio/session/v1"


def configured_password() -> str:
    return (os.environ.get("APP_PASSWORD") or "").strip()


def auth_enabled() -> bool:
    return bool(configured_password())


def session_token(password: str | None = None) -> str:
    pw = password if password is not None else configured_password()
    return hmac.new(pw.encode("utf-8"), _SALT, hashlib.sha256).hexdigest()


def token_is_valid(candidate: str | None) -> bool:
    pw = configured_password()
    if not pw or not candidate:
        return False
    return hmac.compare_digest(candidate, session_token(pw))


def password_is_correct(candidate: str) -> bool:
    pw = configured_password()
    if not pw:
        return True
    return hmac.compare_digest((candidate or "").encode("utf-8"), pw.encode("utf-8"))


# --- crude login throttling -------------------------------------------------
# In-memory, resets on restart. Enough to stop a script hammering the form;
# not a real defence, and it doesn't need to be for a single-user tool.
_attempts: dict[str, list[float]] = defaultdict(list)
MAX_ATTEMPTS = 8
WINDOW_SECONDS = 300


def too_many_attempts(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _attempts.get(ip, []) if now - t < WINDOW_SECONDS]
    _attempts[ip] = recent
    return len(recent) >= MAX_ATTEMPTS


def record_attempt(ip: str) -> None:
    _attempts[ip].append(time.time())


def clear_attempts(ip: str) -> None:
    _attempts.pop(ip, None)


def hosted_mode() -> bool:
    """True when we look like we're running on a public host rather than a laptop."""
    return bool(
        os.environ.get("RENDER")
        or os.environ.get("RENDER_EXTERNAL_URL")
        or os.environ.get("OUTREACH_HOSTED") == "1"
    )
