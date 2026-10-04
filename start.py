#!/usr/bin/env python3
"""Phone-friendly launcher for the Outreach Studio web UI.

Tap Run in Pydroid 3 (or any Python on Android). It checks what's installed,
tells you exactly what to pip-install if something is missing, then starts the
server on 127.0.0.1 and prints the address to open in Chrome.

If FastAPI / pydantic / uvicorn won't install on your device, use the CLI mode
instead - it needs far less and works on a phone:

    python3 -m app.cli leads.csv
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def say(*parts) -> None:
    """print + flush, so output shows up immediately in Pydroid."""
    print(*parts, flush=True)

REQUIRED = [
    ("fastapi", "fastapi", "web framework (needs pydantic)"),
    ("uvicorn", "uvicorn", "server"),
    ("httpx", "httpx", "crawling the stores"),
    ("bs4", "beautifulsoup4", "reading the HTML"),
    ("multipart", "python-multipart", "file uploads"),
]
OPTIONAL = [
    ("openpyxl", "openpyxl", "only needed for .xlsx lists"),
    ("lxml", "lxml", "faster HTML parsing - optional, needs a native build"),
]


def check() -> tuple[list[str], list[str]]:
    missing, missing_optional = [], []
    for mod, pkg, _why in REQUIRED:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    for mod, pkg, _why in OPTIONAL:
        try:
            __import__(mod)
        except ImportError:
            missing_optional.append(pkg)
    return missing, missing_optional


def free_port(preferred: int) -> int:
    for port in (preferred, 8849, 8850, 9000, 8080):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def main() -> int:
    say()
    say("  Outreach Studio")
    say("  " + "-" * 42)
    say(f"  python  : {sys.version.split()[0]}")
    say(f"  folder  : {HERE}")

    missing, missing_optional = check()
    if missing:
        say()
        say("  These packages are missing:")
        for pkg in missing:
            why = next(w for m, p, w in REQUIRED if p == pkg)
            say(f"      {pkg:18} {why}")
        say()
        say("  Install them in Pydroid 3:")
        say("      menu (top left)  ->  Pip  ->  type the name  ->  Install")
        say("      install these:  " + "  ".join(missing))
        say()
        say("  If a package refuses to install (pydantic or uvicorn often do,")
        say("  because they need native code), skip the web UI entirely and use:")
        say("      python3 -m app.cli leads.csv")
        say("  That mode needs only httpx and beautifulsoup4, and gives you a")
        say("  tappable page for sending. On a phone it's the better option anyway.")
        say()
        return 1

    if missing_optional:
        say(f"  optional: {' '.join(missing_optional)} not installed (fine)")

    port = free_port(int(os.environ.get("PORT", "8848")))
    os.environ["PORT"] = str(port)
    os.environ["HOST"] = "127.0.0.1"

    say()
    say("  " + "=" * 42)
    say(f"   RUNNING - open this in Chrome on this phone:")
    say(f"       http://127.0.0.1:{port}")
    say("  " + "=" * 42)
    say()
    say("  Keep Pydroid in the foreground while it crawls. Android may")
    say("  suspend the app if you switch away, which pauses the run.")
    say("  Stop it with the stop button in Pydroid.")
    say()

    from app.main import app  # noqa: PLC0415 - import late so the checks print first
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        say("\n  stopped")
