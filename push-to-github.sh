#!/usr/bin/env bash
# Push this project to a GitHub repo.
#
#   ./push-to-github.sh git@github.com:you/outreach-studio.git
#   ./push-to-github.sh https://github.com/you/outreach-studio.git
#
# Safe to run more than once - it just commits and pushes again.
# Set FORCE=1 if you need to overwrite whatever is already in the repo.
set -euo pipefail

cd "$(dirname "$0")"

REMOTE="${1:-${GITHUB_REPO_URL:-}}"
if [ -z "$REMOTE" ]; then
  cat >&2 <<'USAGE'
Usage: ./push-to-github.sh <repo-url>

  git@github.com:yourname/your-repo.git
  https://github.com/yourname/your-repo.git

Create the repo on GitHub first (empty - no README, no .gitignore, no licence),
then run this with its URL.
USAGE
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "git is not installed." >&2
  exit 1
fi

echo "==> Checking nothing private is about to be committed"
if [ -f outreach.db ] && ! git check-ignore -q outreach.db 2>/dev/null; then
  echo "ERROR: outreach.db is not ignored and would be committed." >&2
  echo "That file contains real email addresses. Fix .gitignore before continuing." >&2
  exit 1
fi
for f in .env; do
  if [ -f "$f" ] && ! git check-ignore -q "$f" 2>/dev/null; then
    echo "ERROR: $f is not ignored - it may contain an API key." >&2
    exit 1
  fi
done
echo "    ok"

if [ ! -d .git ]; then
  echo "==> Initialising repository"
  git init -q
  git symbolic-ref HEAD refs/heads/main 2>/dev/null || git branch -M main
fi

# Identity is only needed if this machine has none configured.
if ! git config user.email >/dev/null 2>&1; then
  echo "==> No git identity found; setting a local placeholder (change it if you like:"
  echo "    git -C . commit --amend --reset-author)"
  git config user.name "Outreach Studio"
  git config user.email "outreach@localhost"
fi

echo "==> Staging files"
git add -A

if git diff --cached --quiet; then
  echo "    nothing new to commit"
else
  git commit -q -m "${COMMIT_MSG:-Outreach Studio: crawl + personalize + send}"
  echo "    committed"
fi

git remote remove origin 2>/dev/null || true
git remote add origin "$REMOTE"

echo "==> Pushing to $REMOTE"
if [ "${FORCE:-0}" = "1" ]; then
  git push -u --force origin main
else
  if ! git push -u origin main 2>/tmp/push-err.$$; then
    if grep -qiE "rejected|non-fast-forward|fetch first" /tmp/push-err.$$; then
      cat >&2 <<'HINT'

Push rejected: the GitHub repo already has commits (probably a README or licence
you ticked when creating it). Pick one:

  # keep the remote's files, merge them in, push again
  git pull --rebase origin main && git push -u origin main

  # or overwrite the remote completely
  FORCE=1 ./push-to-github.sh <repo-url>

HINT
    else
      cat /tmp/push-err.$$ >&2
    fi
    rm -f /tmp/push-err.$$
    exit 1
  fi
fi
rm -f /tmp/push-err.$$

echo
echo "Done. A few things worth checking on GitHub:"
echo "  - the repo is set to Private"
echo "  - outreach.db and .env are NOT in the file list"
echo "  - the commit author is you (git commit --amend --reset-author)"
