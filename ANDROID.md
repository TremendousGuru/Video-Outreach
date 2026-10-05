# Running this on Android with Pydroid 3

Short version: **Pydroid 3 can crawl the stores and write the messages. It cannot
push to GitHub, and the web UI may not install.** Use CLI mode — on a phone it's
the better interface anyway.

---

## What works and what doesn't

| | On Pydroid 3 | Notes |
|---|---|---|
| Crawling stores | ✅ Yes | `httpx` + `beautifulsoup4` are pure Python — install cleanly |
| Writing messages (templates) | ✅ Yes | No dependencies at all |
| Writing messages (AI) | ✅ Yes | Just an HTTPS call; set your key as an env var or in Settings |
| Tappable send page (`outbox.html`) | ✅ Yes | Opens in Chrome, `mailto:` buttons work |
| The web UI (`start.py`) | ⚠️ Maybe | Needs `fastapi`, `pydantic`, `uvicorn`, `python-multipart` |
| `run.sh` | ❌ No | Pydroid has no bash |
| `git push` | ❌ No | **Pydroid has no `git` binary** — this is a hard limit |

**On the web UI:** it's a coin flip. `fastapi` usually installs because prebuilt
wheels exist for most Android ABIs, but `pydantic` compiles a Rust extension and
`uvicorn`'s optional extras need native builds. `python-multipart` is small and
usually fine. Try it — `start.py` will tell you exactly what's missing rather
than crashing — but don't be surprised if it fails, and don't fight it. The CLI
does the same job and gives you a page that's actually designed for a thumb.

---

## Setup

**1. Get the files onto the phone.** Download `outreach-studio.zip`, then unzip
it. Any file manager works (Google Files, MiXplorer, ZArchiver). Put the folder
somewhere you can find, e.g. `/sdcard/Download/Video-Outreach`.

**2. Install the two packages.** In Pydroid 3: menu (top-left, three lines) →
**Pip** → type a name → **Install**. Install these:

```
httpx
beautifulsoup4
openpyxl          <- only if your list is .xlsx
```

If `openpyxl` won't install, save your list as **CSV** instead and skip it.

**3. Run it.** Open a *new file* in Pydroid, or use the terminal tab. From the
folder containing `app/`:

```
python3 -m app.cli leads.csv
```

Pydroid's "Run" button runs the file you have open, so the reliable path is:
open **`app/cli.py`** in the editor → menu → **Run**. If it complains it can't
find `app`, put the project folder at `/sdcard/Download/Video-Outreach` and run
from there, or tap the terminal icon and `cd` first.

---

## Your lead list on a phone

CSV is easiest. Make `leads.csv` in any text editor app, three columns, header
optional:

```csv
email,domain,store_name
hello@store1.com,store1.com,Store One
,store2.com
hello@store3.com
```

Rules that matter:

- A row needs an **email or a domain** — one is enough.
- Domain only is fine; the crawler finds the contact address on the site.
- Email only is fine; the domain is inferred when it isn't a gmail/yahoo type.

If you save it with a `.txt` extension that works too, one store per line, in any
order, separated by commas, tabs or pipes.

---

## What you get

```
outbox.html     the tappable page - this is what you actually use
messages.csv    a spreadsheet copy, for sending from anywhere else
outbox.json     run state - re-running skips stores already finished
```

Open `outbox.html` with Chrome. You'll see one card per store:

- **Subject** and the full **body**, already written from that store's real products
- **Open draft** — tap, your mail app opens with everything filled in, you hit send
- **Gmail web** — same but in the browser, if the app link misbehaves
- **Copy** — for pasting into Outlook or anything else
- **Sent ✓** — tap after you send. The page remembers it on this device, so you
  can close Chrome, come back tomorrow, and not email anyone twice.

Tapping either open button moves that card down into an **Opened** section — the
page remembers it on this device too. So the top of the page is always "what you
have not emailed yet", and you can close Chrome mid-list and pick up where you
stopped.

---

## Handling a long list

Phones don't like long-running foreground processes — if you switch apps, Android
may suspend Pydroid mid-run. Do it in chunks:

```
python3 -m app.cli leads.csv --limit 5      first 5, check the quality
python3 -m app.cli leads.csv                the rest
```

Re-running **never repeats work**: finished stores are skipped, and only the
failed ones get retried. State is written after *every* store, so if Android
kills the app you lose nothing.

Other flags:

```
--redo              re-crawl and rewrite stores already done
--no-ai             force templates even if you've set an API key
--concurrency 3     how many stores at once (default 3; drop to 1 if flaky)
```

Start with `--limit 5`. Read the five messages. Tune the wording in Settings
(or just re-run with `--redo`) before you spend an hour on the whole list.

---

## Using your AI key

Two options, and on Android the second is easier:

**Environment variable** — Pydroid has `os.environ` per-session, which is awkward
to set persistently across launches.

**In the database** — copy your key in by hand, once:

```python
import sqlite3, json
c = sqlite3.connect("outreach.db")
c.execute("INSERT OR REPLACE INTO settings (k,v) VALUES (?,?)", ("api_key", json.dumps("sk-your-key")))
c.commit()
```

Run that as a script in Pydroid from inside the project folder. After that, every
CLI run picks it up and the console line will say `AI writing : on (gpt-4o-mini)`.

Verify before trusting it on 50 stores — run with `--limit 2` and check that the
console says AI is on and the messages don't look templated.

---

## GitHub on Android — what to do instead

Pydroid has no git. Your options, best first:

**1. Termux** (a real Linux terminal for Android, free on F-Droid — install from
F-Droid, not Play, since the Play build is abandoned):

```
pkg install git
cd /sdcard/Download/Video-Outreach
./push-to-github.sh https://github.com/TremendousGuru/Video-Outreach.git
```

Storage permissions: run `termux-setup-storage` once first.

**2. GitHub's website uploader** — the repo page → **Add file** → **Upload
files** → drag files from your file manager → Commit. 16 files, doable but
tedious on a phone. GitHub's mobile web upload is unreliable with folders.

**3. Do it from a computer** when you next have one; the repo isn't urgent, and
your phone can run the tool regardless of whether the code is on GitHub.

**4. Give me a token** — I'll push from here. See `PUSH-INSTRUCTIONS.md`.

---

## Realistic expectations on a phone

- **Speed.** 30–60 stores is a reasonable run. Hundreds will get tedious —
  Android will background the app, and a phone is a phone.
- **Battery and data.** Each store is roughly 5–7 HTTP requests. A 50-store run
  is a few MB, not a problem.
- **Deliverability still applies.** Gmail caps ~500/day and far less for a new
  account. 20–40 taps in a sitting is plenty; more looks like a blast.
- **The `mailto:` buttons depend on a mail app being installed and set as
  default.** If "Open in Gmail" does nothing, use **Gmail web** or **Copy**.
