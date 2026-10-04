#!/usr/bin/env bash
# Publish this project to a Hugging Face Space.
#
#   ./deploy-space.sh https://huggingface.co/spaces/YOURNAME/YOUR-SPACE
#
# Auth uses the HF_TOKEN environment variable (a Hugging Face access token with
# WRITE access). It is never written into .git/config.
#
#   HF_TOKEN=hf_xxx ./deploy-space.sh https://huggingface.co/spaces/you/space
#
# The Space must already exist (create it in the HF UI with SDK = Docker).
# This script syncs the project into it and preserves the Space's own settings
# (title, emoji, colours) unless you pass --reset-frontmatter.
#
# It does NOT set your app password - that is a Space secret, set in the UI or
# with the API, precisely so it never lands in a git repo that the world can read.
set -euo pipefail

cd "$(dirname "$0")"
SRC="$(pwd)"

SPACE_URL="${1:-}"
shift || true
RESET_FRONTMATTER=0
for arg in "$@"; do
  case "$arg" in
    --reset-frontmatter) RESET_FRONTMATTER=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

if [ -z "$SPACE_URL" ]; then
  cat >&2 <<'USAGE'
Usage: ./deploy-space.sh <space-url> [--reset-frontmatter]

  ./deploy-space.sh https://huggingface.co/spaces/yourname/video-outreach

Auth: export HF_TOKEN=hf_xxx   (token with WRITE access, from
      https://huggingface.co/settings/tokens)

The Space must exist first. Create it at https://huggingface.co/new-space with
SDK = Docker and hardware = CPU basic (free).
USAGE
  exit 1
fi

# ------------------------------------------------------------------ preflight
if [ ! -f Dockerfile ]; then
  echo "ERROR: no Dockerfile here. Run this from the project root." >&2
  exit 1
fi
if [ ! -f space-README.md ]; then
  echo "ERROR: space-README.md is missing - it carries the Space config." >&2
  exit 1
fi
if [ -n "$(git status --porcelain 2>/dev/null || true)" ]; then
  echo "WARNING: you have uncommitted changes. They are included here (the script"
  echo "         copies the working tree), but consider committing first so the"
  echo "         GitHub copy and the Space stay in step."
  echo
fi

# Refuse to publish secrets. Only files that would ACTUALLY be uploaded matter:
# tracked files when this is a git repo (we publish via git archive), or the
# tree minus the usual junk otherwise. A local outreach.db sitting in the folder
# is normal and harmless - it is never copied.
if git rev-parse --git-dir >/dev/null 2>&1; then
  PUBLISHED=$(git ls-files)
  SCOPE="tracked files"
else
  PUBLISHED=$(find . -type f \
    -not -path './.git/*' -not -path './.venv/*' -not -path '*/__pycache__/*' \
    -not -name '*.pyc' -not -name 'outreach.db*' -not -name '.env' \
    -not -name '*.zip' -not -name 'outbox*' -not -name 'messages.csv')
  SCOPE="files in the folder"
fi

TRACKED_SECRETS=$(echo "$PUBLISHED" \
  | grep -E '(^|/)(\.env|\.env\..*|outreach\.db.*|credentials\.json|secrets\.json)$' \
  | grep -vE '(^|/)\.env\.example$' || true)
if [ -n "$TRACKED_SECRETS" ]; then
  echo "ERROR: these would be published and should not be:" >&2
  echo "$TRACKED_SECRETS" | sed 's/^/   /' >&2
  echo >&2
  echo "Fix: remove them from git (they are in .gitignore for a reason):" >&2
  echo "   git rm --cached <file>" >&2
  exit 1
fi

# Look for a real key pasted into a file. .env.example is the documented pattern,
# so anything with an obviously fake placeholder is fine.
LEAKS=$(echo "$PUBLISHED" | grep -E '\.(md|py|ya?ml|sh|json|txt)$' | while read -r f; do
  [ -f "$f" ] || continue
  if grep -qE "(OPENAI_API_KEY|APP_PASSWORD)[\"']?[[:space:]]*[:=][[:space:]]*[\"']?(sk-|hf_|github_pat_|ghp_)" "$f" 2>/dev/null; then
    echo "$f"
  fi
done)
if [ -n "$LEAKS" ]; then
  echo "ERROR: a real-looking credential is written into these $SCOPE:" >&2
  echo "$LEAKS" | sed 's/^/   /' >&2
  echo "Space repositories are public on the free tier. Remove it before publishing." >&2
  exit 1
fi

# --------------------------------------------------------------------- clone
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

AUTH_URL="$(SPACE_URL="$SPACE_URL" python3 - <<'PY'
import os, urllib.parse
raw = os.environ["SPACE_URL"].rstrip("/")
p = urllib.parse.urlparse(raw)
tok = (os.environ.get("HF_TOKEN") or "").strip()
user = (os.environ.get("HF_USER") or "hf").strip()
if p.scheme in ("http", "https") and p.netloc:
    if tok:
        print(f"https://{urllib.parse.quote(user)}:{urllib.parse.quote(tok)}@{p.netloc}{p.path}")
    else:
        print(f"https://{p.netloc}{p.path}")
else:
    print(raw)
PY
)"
USER_URL="https://$(echo "$SPACE_URL" | sed -E 's#^https?://##; s#/$##')"

echo "==> Cloning the Space"
if ! GIT_TERMINAL_PROMPT=0 git clone --quiet "$AUTH_URL" "$WORK/space" 2>"$WORK/clone.err"; then
  echo "Could not clone $SPACE_URL" >&2
  sed 's/hf_[A-Za-z0-9_]*/[REDACTED]/g' "$WORK/clone.err" >&2
  echo >&2
  echo "Common causes:" >&2
  echo "  - HF_TOKEN is not set, or lacks WRITE access" >&2
  echo "  - the Space does not exist yet (create it with SDK = Docker)" >&2
  echo "  - the URL is wrong: https://huggingface.co/spaces/USER/SPACE" >&2
  exit 1
fi

# Remember the Space's existing frontmatter so its look survives.
FRONT=""
if [ -f "$WORK/space/README.md" ] && head -1 "$WORK/space/README.md" | grep -q '^---$'; then
  FRONT="$WORK/existing-frontmatter.md"
  awk 'NR==1 && /^---$/{f=1; next} f && /^---$/{exit} f' "$WORK/space/README.md" > "$FRONT"
fi

echo "==> Replacing Space contents with this project"
cd "$WORK/space"
find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +

if git -C "$SRC" rev-parse --git-dir >/dev/null 2>&1; then
  # git archive gives exactly the tracked files - no caches, databases or zips.
  git -C "$SRC" archive HEAD | tar -x -C "$WORK/space"
else
  echo "    (not a git repo - copying the working tree instead)"
  tar -C "$SRC" --exclude=.git --exclude=.venv --exclude=__pycache__ \
      --exclude='*.pyc' --exclude='outreach.db*' --exclude='.env' \
      --exclude='*.zip' --exclude='outbox*' --exclude='messages.csv' \
      -cf - . | tar -xf - -C "$WORK/space"
fi

# ------------------------------------------------------------------- README
echo "==> Writing the Space README (frontmatter is what makes it build)"
if [ "$RESET_FRONTMATTER" = "1" ] || [ ! -s "${FRONT:-/nonexistent}" ]; then
  cp "$SRC/space-README.md" "$WORK/space/README.md"
else
  # Keep their title/emoji/colours, force the keys the build depends on.
  {
    echo "---"
    FRONT="$FRONT" python3 - <<'PY'
import os, re
keep = {}
for line in open(os.environ["FRONT"]):
    if ":" in line:
        k, v = line.split(":", 1)
        keep[k.strip()] = v.strip()
keep.pop("sdk", None)
keep.pop("dockerfile", None)
for k, v in (("sdk", "docker"), ("app_port", "7860")):
    keep[k] = v
order = ["title", "emoji", "colorFrom", "colorTo", "sdk", "app_port", "pinned", "short_description"]
seen = set()
for k in order:
    if k in keep:
        print(f"{k}: {keep[k]}")
        seen.add(k)
for k, v in keep.items():
    if k not in seen:
        print(f"{k}: {v}")
PY
    echo "---"
    tail -n +2 "$SRC/space-README.md" | awk 'BEGIN{f=0} /^---$/{f++; next} f>=1{print}'
  } > "$WORK/space/README.md"
fi

# ------------------------------------------------------------------- verify
python3 - "$WORK/space/README.md" <<'PY' || { echo "ERROR: generated README frontmatter is invalid." >&2; exit 1; }
import sys, re
text = open(sys.argv[1]).read()
m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
if not m:
    sys.exit(1)
body = m.group(1)
try:
    import yaml
    cfg = yaml.safe_load(body) or {}
except ImportError:
    cfg = dict(
        (k.strip(), v.strip())
        for k, _, v in (line.partition(":") for line in body.splitlines() if ":" in line)
    )
assert cfg.get("sdk") == "docker", "sdk must be docker"
assert str(cfg.get("app_port")) == "7860", "app_port must be 7860"
print(f"    ok - sdk={cfg['sdk']} app_port={cfg['app_port']} title={cfg.get('title','?')}")
PY

# --------------------------------------------------------------------- push
cd "$WORK/space"
git config user.email "deploy@localhost"
git config user.name "Space deploy"
git add -A
if git diff --cached --quiet; then
  echo "==> Space already up to date - nothing to push"
else
  COUNT=$(git diff --cached --name-only | wc -l | tr -d ' ')
  git commit --quiet -m "Deploy from GitHub ($COUNT files)"
  echo "==> Pushing $COUNT files to the Space"
  if ! GIT_TERMINAL_PROMPT=0 git push --quiet origin HEAD:main 2>"$WORK/push.err"; then
    echo "Push failed:" >&2
    sed 's/hf_[A-Za-z0-9_]*/[REDACTED]/g' "$WORK/push.err" >&2
    exit 1
  fi
fi

echo
echo "Done. The Space is rebuilding now - first build takes 3-5 minutes."
echo
echo "  Space page : $SPACE_URL"
echo "  Direct URL : https://$(echo "$SPACE_URL" | sed -E 's#^https?://huggingface.co/spaces/##; s#/#-#g').hf.space"
echo
echo "Still to do, once, in the Space settings:"
echo "  Settings -> Variables and secrets -> New secret"
echo "      APP_PASSWORD = <your password>        required - otherwise it is open to anyone"
echo "      OPENAI_API_KEY = <optional>           write messages with a model, not templates"
echo
echo "Use the direct .hf.space URL for signing in - browsers block the session"
echo "cookie inside the embedded frame on huggingface.co."
