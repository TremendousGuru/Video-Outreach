"""Shopify outreach studio - local app package.

Loading .env here means it applies no matter which module is imported first.
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["main", "db", "ingest", "crawler", "compose", "pipeline"]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no extra dependency) so keys can live outside both
    the repo and the database. Real environment variables always win."""
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv(_PROJECT_ROOT / ".env")
