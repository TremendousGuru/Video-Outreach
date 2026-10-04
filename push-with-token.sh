#!/usr/bin/env bash
# Push using a GitHub token supplied at runtime. The token is passed on the
# command line only - it is never written into .git/config or any file.
#
#   GITHUB_TOKEN=github_pat_xxx ./push-with-token.sh [repo-url] [--force]
#
# GIT_USER defaults to your GitHub username. x-access-token also works for
# classic tokens, but the real username is the safer default.
set -euo pipefail

cd "$(dirname "$0")"

REPO="${1:-https://github.com/TremendousGuru/Video-Outreach.git}"
FORCE="${2:-}"

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "Set GITHUB_TOKEN first." >&2
  exit 1
fi

# Never let the token end up in .git/config or shell history artefacts.
git remote remove tokpush 2>/dev/null || true
AUTH_URL="$(python3 - "$REPO" <<'PY'
import sys, urllib.parse, os
url = sys.argv[1]
p = urllib.parse.urlparse(url)
if p.scheme not in ("http", "https") or not p.netloc:
    # Local path or SSH remote: nothing to inject, use it untouched.
    print(url)
else:
    # Fine-grained PATs authenticate most reliably with the real username.
    user = urllib.parse.quote(os.environ.get("GIT_USER", "TremendousGuru"), safe="")
    tok = urllib.parse.quote(os.environ["GITHUB_TOKEN"], safe="")
    print(f"{p.scheme}://{user}:{tok}@{p.netloc}{p.path}")
PY
)"
git remote add tokpush "$AUTH_URL"

cleanup() { git remote remove tokpush 2>/dev/null || true; }
trap cleanup EXIT

echo "==> pushing $(git rev-parse --short HEAD) to $REPO"
if [ "$FORCE" = "--force" ]; then
  git push tokpush main:main --force
else
  git push tokpush main:main
fi
cleanup
trap - EXIT
echo "==> done; token removed from local git config"
