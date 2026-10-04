#!/usr/bin/env bash
# Launch Outreach Studio. Creates a virtualenv on first run, then just starts the app.
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3 not found. Install it first (python.org, or: sudo apt install python3 python3-venv)" >&2
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "==> First run: creating virtualenv in .venv"
  "$PYTHON" -m venv .venv || {
    echo "Could not create a virtualenv. On Debian/Ubuntu try: sudo apt install python3-venv" >&2
    exit 1
  }
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if [ ! -f ".venv/.deps-ok" ] || [ requirements.txt -nt ".venv/.deps-ok" ]; then
  echo "==> Installing dependencies"
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
  touch .venv/.deps-ok
fi

PORT="${PORT:-8848}"
echo
echo "  Outreach Studio  ->  http://localhost:${PORT}"
echo "  (Ctrl+C to stop. Your data lives in outreach.db)"
echo
exec python3 -m app.main
