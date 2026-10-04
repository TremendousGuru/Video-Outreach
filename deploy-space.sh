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
# TWO MODES
#
#   --streamlit   (default) publishes streamlit_app.py, and the Space's README
#                 frontmatter gets sdk: streamlit. This is the free path: as of
#                 July 2026 Hugging Face charges for the Docker and Gradio SDKs
#                 on cpu-basic, while the Streamlit SDK is free.
#
#   --docker      publishes the FastAPI edition (app/main.py) and forces
#                 sdk: docker + app_port: 7860. Only works on a PAID Space now,
#                 so it is kept for anyone who already has one or moves hosts.
#
# The Space must already exist. Create it in the HF UI with the matching SDK.
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
MODE="streamlit"
for arg in "$@"; do
  case "$arg" in
    --reset-frontmatter) RESET_FRONTMATTER=1 ;;
    --streamlit) MODE="streamlit" ;;
    --docker) MODE="docker" ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

if [ -z "$SPACE_URL" ]; then
  cat >&2 <<'USAGE'
Usage: ./deploy-space.sh <space-url> [--streamlit | --docker] [--reset-frontmatter]

  ./deploy-space.sh https://huggingface.co/spaces/yourname/video-outreach

  --streamlit   (default, free) publishes streamlit_app.py
  --docker      publishes the FastAPI edition - needs a PAID Space since July 2026

Auth: export HF_TOKEN=hf_xxx   (token with WRITE access, from
      https://huggingface.co/settings/tokens)

The Space must exist first. Create it at https://huggingface.co/new-space with
SDK = Streamlit and hardware = CPU basic (free).
USAGE
  exit 1
fi

# ------------------------------------------------------------------ preflight
if [ "$MODE" = "streamlit" ]; then
  for f in streamlit_app.py requirements-streamlit.txt space-README.md; do
    if [ ! -f "$f" ]; then
      echo "ERROR: $f is missing. Run this from the project root." >&2
      exit 1
    fi
  done
elif [ ! -f Dockerfile ]; then
  echo "ERROR: no Dockerfile here. Run this from the project root." >&2
  exit 1
fi
if [ ! -f space-README.md ]; then
  echo "ERROR: space-README.md is missing - it carries the Space config." >&2
  exit 1
fi

if [ "$MODE" = "streamlit" ]; then
  echo "==> Mode: streamlit (free) - publishing streamlit_app.py"
else
  echo "==> Mode: docker (paid Space required since July 2026) - publishing app/main.py"
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

# Free hosts install from requirements.txt, so in Streamlit mode that file has to
# be the Streamlit one. Keeping fastapi/uvicorn out of the build also makes it
# noticeably quicker, which matters on a free CPU.
if [ "$MODE" = "streamlit" ]; then
  echo "==> Using requirements-streamlit.txt as requirements.txt"
  cp "$WORK/space/requirements-streamlit.txt" "$WORK/space/requirements.txt"
  # A Dockerfile left in the tree makes the build ambiguous - drop it.
  rm -f "$WORK/space/Dockerfile"
fi

# ------------------------------------------------------------------- README
echo "==> Writing the Space README (frontmatter is what makes it build)"
if [ "$RESET_FRONTMATTER" = "1" ] || [ ! -s "${FRONT:-/nonexistent}" ]; then
  cp "$SRC/space-README.md" "$WORK/space/README.md"
else
  # Keep their title/emoji/colours, force the keys the build depends on.
  {
    echo "---"
    FRONT="$FRONT" SPACE_MODE="$MODE" python3 - <<'PY'
import os, re
keep = {}
for line in open(os.environ["FRONT"]):
    if ":" in line:
        k, v = line.split(":", 1)
        keep[k.strip()] = v.strip()
# Never inherit the other mode's keys - `app_port` under the Streamlit SDK or a
# stale `app_file` pointing at a Docker entrypoint both stop the Space booting.
for k in ("sdk", "dockerfile", "app_port", "app_file"):
    keep.pop(k, None)
if os.environ.get("SPACE_MODE") == "streamlit":
    keep["sdk"] = "streamlit"
    keep["app_file"] = "streamlit_app.py"
    order = ["title", "emoji", "colorFrom", "colorTo", "sdk", "app_file",
             "pinned", "short_description"]
else:
    keep["sdk"] = "docker"
    keep["app_port"] = "7860"
    order = ["title", "emoji", "colorFrom", "colorTo", "sdk", "app_port",
             "pinned", "short_description"]
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
python3 - "$WORK/space/README.md" "$MODE" <<'PY' || { echo "ERROR: generated README frontmatter is invalid." >&2; exit 1; }
import sys, re
text = open(sys.argv[1]).read()
mode = sys.argv[2]
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

if mode == "streamlit":
    assert cfg.get("sdk") == "streamlit", f"sdk must be streamlit, got {cfg.get('sdk')!r}"
    assert cfg.get("app_file") == "streamlit_app.py", "app_file must be streamlit_app.py"
    assert "app_port" not in cfg, "app_port belongs to the docker SDK and confuses Streamlit"
    print(f"    ok - sdk=streamlit app_file={cfg['app_file']} title={cfg.get('title','?')}")
else:
    assert cfg.get("sdk") == "docker", f"sdk must be docker, got {cfg.get('sdk')!r}"
    assert str(cfg.get("app_port")) == "7860", "app_port must be 7860"
    print(f"    ok - sdk=docker app_port={cfg['app_port']} title={cfg.get('title','?')}")
PY

# The README body explains the current mode. Swap the marked block so a Docker
# Space does not carry a "sdk: streamlit" explanation next to docker frontmatter.
SPACE_MODE="$MODE" python3 - "$WORK/space/README.md" <<'PY' || { echo "ERROR: could not rewrite the README mode block." >&2; exit 1; }
import os, sys

path = sys.argv[1]
text = open(path).read()
start, end = "<!-- MODE-BLOCK:START -->", "<!-- MODE-BLOCK:END -->"
if start not in text or end not in text:
    sys.exit(0)  # nothing marked (e.g. --reset-frontmatter with an older README)

if os.environ.get("SPACE_MODE") == "streamlit":
    sys.exit(0)  # the block is already written for Streamlit

docker = """This file is the Space's configuration. The **frontmatter above is required** —
`sdk: docker` tells Hugging Face to build the Dockerfile, and `app_port` is the
port the container listens on. Without it the Space won't start.

```yaml
sdk: docker
app_port: 7860
```

### Signing in

Use the direct URL **`https://YOURNAME-NAME.hf.space`**, not the page on
huggingface.co that frames it. Browsers block session cookies inside embedded
frames, so logging in on the framed view appears to do nothing. The login page
shows the direct address if you land there by mistake.

> This edition needs a **paid** Space: since July 2026 the Docker SDK is not part
> of the free tier. `./deploy-space.sh` (no flag) publishes the free Streamlit
> edition instead."""

head, rest = text.split(start, 1)
_, tail = rest.split(end, 1)
open(path, "w").write(f"{head}{start}\n{docker}\n{end}{tail}")
print("    ok - README body rewritten for docker mode")
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
if [ "$MODE" = "streamlit" ]; then
  echo "Done. The Space is rebuilding now - first build takes 2-4 minutes."
else
  echo "Done. The Space is rebuilding now - first build takes 3-5 minutes."
fi
echo
echo "  Space page : $SPACE_URL"
echo "  Direct URL : https://$(echo "$SPACE_URL" | sed -E 's#^https?://huggingface.co/spaces/##; s#/#-#g').hf.space"
echo
echo "Still to do, once, in the Space settings:"
echo "  Settings -> Variables and secrets -> New secret"
echo "      APP_PASSWORD = <your password>        required - otherwise it is open to anyone"
echo "      OPENAI_API_KEY = <optional>           write messages with a model, not templates"
echo
if [ "$MODE" = "docker" ]; then
  echo "Use the direct .hf.space URL for signing in - browsers block the session"
  echo "cookie inside the embedded frame on huggingface.co."
else
  echo "Remember: a free Space sleeps after 48h idle, and its disk is wiped on every"
  echo "rebuild. Download a backup (tab 4) before you redeploy, restore it after."
fi
