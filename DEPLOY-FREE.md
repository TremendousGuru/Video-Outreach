# Deploying for free (verified October 2026)

The short version: **Hugging Face Spaces is still the roomiest free host, but only
through its Streamlit SDK** — as of July 2026 the Docker and Gradio SDKs ask for a
PRO subscription at Space creation. **Streamlit Community Cloud** is the other
strong free option and is the easiest to redeploy. Both are covered below.

---

## The 2026 landscape, checked not remembered

| Host | Free tier status | Verdict for this app |
|---|---|---|
| **Hugging Face Spaces** | Free CPU: **2 vCPU / 16 GB RAM**, ~50 GB disk — but **Streamlit SDK only**; Docker and Gradio now require PRO | **Still the best free capacity.** Sleeps after **48 hrs** |
| **Streamlit Community Cloud** | Free public apps, ~1 GB RAM | **Easiest to redeploy** — builds straight from your GitHub repo. Sleeps after **12 hrs** |
| **Render** | Free web service, 750 hrs/mo, no card | Works. Sleeps after **15 min**, **no disk** |
| Railway | No permanent free tier — one-off $5 trial, then ~$1/mo credit | Not viable |
| Fly.io | No free tier for new users — ~2 hour trial | Not viable |
| Heroku | Free plans removed in 2022 | Not viable |
| Koyeb | Free tier moved behind paid; the leftover free service scales to zero | Not viable |
| Vercel / Netlify / Cloudflare Pages | Free, always on — but static/serverless only | Can't run a crawl |

Sources: [Render's own comparison](https://render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026),
[a 2026 free-tier survey](https://livemy.app/blog/free-hosting-that-doesnt-sleep),
[Hugging Face's paywall discussion](https://discuss.huggingface.co/t/docker-sdk-now-marked-as-paid-when-creating-a-new-space/177580).

### The July 2026 Hugging Face change

Without an announcement, Hugging Face moved Docker and Gradio Spaces behind a
subscription. Creating one now fails with:

> Static Spaces are free for everyone, but hosting Gradio and Docker Spaces on
> free cpu-basic requires a PRO subscription.

Spaces created **before** the change kept running, but new free accounts get
Streamlit (and static) only. This project therefore ships **two editions**:

| Edition | File | Publishing it |
|---|---|---|
| **Streamlit** (free, recommended) | `streamlit_app.py` | `./deploy-space.sh` — the default |
| FastAPI + Docker (needs a paid Space) | `app/main.py` | `./deploy-space.sh --docker` |

Both run the same crawler and the same composer. Only the interface differs.

**The honest conclusion:** no free host gives you a persistent disk. Every free
option wipes your database eventually, so the real answer is *make the data
survivable* — which is what the Backup button in this app is for.

---

## Which of the two free hosts, and why

Both run the same `streamlit_app.py`. The difference is how code gets there, and
how much machine you get.

| | Streamlit Community Cloud | HF Spaces |
|---|---|---|
| RAM | ~1 GB | **16 GB** |
| CPU | shared | **2 vCPU** |
| Sleeps after | 12 hours idle | 48 hours idle |
| How you deploy | **connects to your GitHub repo — click and done** | push files with `git` from a shell |
| Redeploys when you push | **automatic** | only via the optional GitHub Action |
| Private app | one allowed | free Spaces are public |

**The deciding factor here is deployment, not power.** Publishing to a Hugging
Face Space means running a shell script (`./deploy-space.sh`) that clones the
Space repo, copies the files and pushes them. That needs bash and git. If you are
working from a phone, you don't have either — so a Space means uploading 33 files
one at a time through a web form, and doing it again on every change.

Streamlit Community Cloud connects to the GitHub repo you already have. You pick
the branch and the file once; after that every `git push` redeploys itself. That
is why it is Option 1 below.

Pick a Space instead only if you need the 16 GB (a very large batch crawl) or want
the app to stay awake for two days rather than twelve hours.

---

## Option 1 — Streamlit Community Cloud (recommended)

Same app, same repo, and **no build script at all**: it connects to your GitHub
repo and runs a file you point it at. Every `git push` redeploys automatically.

| | HF Spaces free | Streamlit Cloud free |
|---|---|---|
| RAM | 16 GB | ~1 GB |
| Sleeps after | 48 hours | 12 hours |
| Deploy | push files to the Space | **connects to your GitHub repo** |
| Private apps | no (free Spaces are public) | one private app allowed |

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
2. **New app** → **Deploy a public app from GitHub**:
   - Repository: `TremendousGuru/Video-Outreach`
   - Branch: `main`
   - **Main file path: `streamlit_app.py`**
3. **Advanced settings → Secrets**, and paste:
   ```toml
   APP_PASSWORD = "your password"
   ```
   This is the equivalent of a Space secret. It is never written into the repo.
4. Deploy. Your URL is `https://SOME-NAME.streamlit.app`.

Notes specific to this host:

- **No edits needed in the repo.** Streamlit Cloud installs the root
  `requirements.txt`, which already lists everything both editions need. Just
  pick the branch and the file.
- `st.secrets` is mapped to the app's environment variables automatically, so
  `APP_PASSWORD` and `OPENAI_API_KEY` work exactly as described above.
- A **private** app still asks visitors to sign in with Google/GitHub via
  Streamlit's own gate — put your `APP_PASSWORD` in Secrets as well and you get
  both layers.
- **Rebooting is the fastest way to see an update.** Pushing to `main` triggers a
  rebuild automatically, but if a change doesn't show up, use **Manage app →
  Reboot**. A rebuild resets the database, so take a backup first.

---

## Option 2 — Hugging Face Spaces (also free, needs a shell)

The bigger machine, but the more awkward one to publish to from a phone: this
path needs bash and git to run `deploy-space.sh`. Covered in full because it is
still free and still works, and it is the better host if you ever want to run
very large batches.

### Where the password goes — read this first

**Your app password belongs in a Space secret, never in a file.** On the free
tier a Space is **public**: anyone can read every file in it. So:

- A password written into a file = published to the world = no protection at all.
- A password stored as a **secret** = injected as an environment variable at
  runtime, invisible to visitors.

> Space → **Settings** → **Variables and secrets** → **New secret**
> Name `APP_PASSWORD`, Value your password → **Save**

The Space restarts and picks it up. Same place for `OPENAI_API_KEY` if you want
model-written messages instead of templates — and it does not have to be an
OpenAI key. Any OpenAI-compatible provider works; for Google Gemini add your
Google AI Studio key plus `OPENAI_BASE_URL = https://generativelanguage.googleapis.com/v1beta/openai`
and `OPENAI_MODEL = gemini-2.5-flash`. The app's sidebar **Provider** dropdown
sets the address for you, and **Test the key** confirms the key and the model
name before you run a batch.

### Setting it up

1. Create an account at [huggingface.co](https://huggingface.co) — no card needed.
2. **New** → **Space**:
   - Name: `video-outreach`
   - **SDK: Streamlit** — Docker and Gradio now ask for payment
   - Hardware: **CPU basic** (free)
   - Visibility: Public (free Spaces can't be private)
3. Publish this project into the Space. Either:

   **With the script** (Termux, a computer, or any shell):
   ```bash
   export HF_TOKEN=hf_xxx      # token with WRITE access
   ./deploy-space.sh https://huggingface.co/spaces/YOURNAME/video-outreach
   ```
   Streamlit is the default. It swaps `requirements-streamlit.txt` in as the
   Space's `requirements.txt`, drops the now-unusable `Dockerfile`, writes the
   Space's `README.md` with `sdk: streamlit` + `app_file: streamlit_app.py`, and
   preserves the Space's own title, emoji and colours. If the Space was created
   as Docker back when that was free, this **migrates it in place**.

   **Or by hand** via the Space's **Files → Add file → Upload files**. Upload the
   project except `.git`, `outreach.db` and `.env` — and make sure
   `space-README.md` is uploaded **as `README.md`**, because that file's
   frontmatter is what tells Hugging Face how to run it. Also rename
   `requirements-streamlit.txt` to `requirements.txt` in the Space.

4. **Add `APP_PASSWORD` as a secret** (see above).
5. First build takes 2–4 minutes. Your URL is
   `https://YOURNAME-video-outreach.hf.space`.

### Signing in

Use the direct URL **`https://YOURNAME-NAME.hf.space`**. Unlike the FastAPI
edition — whose session cookie browsers block inside frames — the Streamlit
edition holds your sign-in in the browser session, so the embedded view on
huggingface.co works too. The direct URL is just the shorter one to bookmark.

### Coming from the Docker edition

If your Space already runs the FastAPI edition, redeploying with the script
(default Streamlit mode) rewrites the frontmatter and swaps the requirements
file, and the Space rebuilds as Streamlit. Two things to expect:

- The old `APP_PASSWORD` secret keeps working — same variable name.
- The database starts empty, because the rebuild wipes the disk. **Take a backup
  from the FastAPI edition first** (Backup button), then restore it in the
  Streamlit edition (tab 4, "Restore from a backup").

### Automatic deploys (optional)

A GitHub Action is included that republishes the Space on every push to `main`.
It stays a silent no-op until you add two things in GitHub → **Settings →
Secrets and variables → Actions**:

| Type | Name | Value |
|---|---|---|
| Secret | `HF_TOKEN` | a Hugging Face token with write access |
| Variable | `SPACE_REPO` | `https://huggingface.co/spaces/YOURNAME/video-outreach` |

Then `git push` → GitHub → Space rebuilds. Nothing to remember.

---

## Option 3 — Render free tier

**Use `render-free.yaml`, not `render.yaml`.** The paid blueprint asks for a disk,
and Render rejects a blueprinted disk on a free instance, so it would fail to
apply. The free one omits it.

Render → **New** → **Blueprint** → pick the repo → set the **Blueprint file path**
to `render-free.yaml` → Apply. It will prompt you for `APP_PASSWORD`.

Or by hand: **New → Web Service**, build `pip install -r requirements.txt`, start
`uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1 --proxy-headers`,
health check `/health`, and set `APP_PASSWORD` plus `OUTREACH_HOSTED=1`.

**What you're signing up for on free Render:** sleeps after 15 minutes idle
(30–60s to wake), and your database is wiped on every deploy and restart. The app
now warns you about this with a banner when it detects it.

**Working within it:** because the wipe happens on deploy — something *you*
trigger — the backup workflow is fully in your control:

> Before you push code: **Backup**. After it redeploys: **Restore**.

Two clicks, and you're exactly where you were. Multi-store crawls are also worth
chunking on free, since a 15-minute idle window can kill a long run.

---

## Option 4 — keep it local, tunnel when needed

If hosting was really about "use it from my phone", skip the cloud entirely:

```bash
./run.sh                                        # terminal 1
cloudflared tunnel --url http://localhost:8848  # terminal 2
```

Cloudflare hands you a temporary public HTTPS URL. Your laptop does the crawling,
**the database never leaves your machine**, and there is no free-tier storage
problem at all. Costs nothing.

Set `APP_PASSWORD` before you open the tunnel — that URL is public while the
tunnel is up. Close the tunnel and the surface is gone.

The catch: your laptop must be awake and online. For a tool you use in bursts,
that's usually fine.

---

## Option 5 — the durable fix (needs a small code change)

Point the database at a **free external Postgres** and the ephemeral-disk problem
disappears completely — the host can wipe its disk as often as it likes.

| Provider | Free tier |
|---|---|
| **Neon** | **1 GB** storage/project, 100 compute-hours/mo, permanent, no card |
| Supabase | 500 MB database, 2 projects, pauses after ~1 week idle |

Both are genuinely free forever, and 1 GB is enormous for this app — thousands of
leads with full message bodies and fact sheets fit comfortably in a fraction of it.

This repo uses SQLite, so it would need a Postgres path added to `app/db.py`
(swap the driver, `?` → `%s` placeholders, `AUTOINCREMENT` → identity columns,
drop the WAL pragma, read `DATABASE_URL`). Maybe an hour's work, and it's the one
change that makes *any* free host safe. Say the word if you want it.

---

## Which should you pick?

| Your situation | Do this |
|---|---|
| Working from a phone, want it live with the least fiddling | **Streamlit Community Cloud** |
| Want the most capacity free, data loss acceptable | **HF Spaces** (Streamlit SDK) |
| Want redeploys to be automatic on every `git push` | **Streamlit Community Cloud** |
| Already have Render open, want it live in 5 minutes | **Render free** + Backup/Restore before deploys |
| Want zero data-loss risk and zero cost | **Local + Cloudflare tunnel** |
| Using it seriously, data must never vanish | Neon free Postgres, then any free host |

---

## Free-tier failure modes, and what they look like

| Symptom | Cause | Fix |
|---|---|---|
| App takes 30–60s to respond, then works | Cold start after sleep | Normal, nothing broken |
| Leads and messages gone after a deploy | Ephemeral disk on free | **Restore** your backup |
| Crawl stopped partway, page shows older state | The instance slept or redeployed mid-run | Rows return to the queue; press **Crawl & compose** again |
| Signed out with "the password was changed" | The `APP_PASSWORD` secret changed, so old sessions stopped working | Expected — sign in with the new password |
| "Could not sign in" on a tab left open | The app restarted (sleep or redeploy) | Reload and log in again |
| Backup download does nothing | Not signed in — the backup holds real emails, so it's auth-gated | Log in first |

---

## Do this before you go live

1. **Set `APP_PASSWORD` as a secret, not in a file.** Free hosting makes your app
   publicly reachable the moment it deploys, and an unset password means no login
   at all. Never commit it — a public Space serves every file to the world.
2. **Take a backup** once you have a real lead list worth keeping.
3. **Start with 5 stores**, read the messages, tune the wording, then run the rest.

## Changing your password later

Update the `APP_PASSWORD` secret and the app restarts and picks up the new value.

**Changing the password signs everyone out**, in both editions. The FastAPI
edition signs its session cookie with the password; the Streamlit edition keeps a
fingerprint of the password in the session and compares it on every interaction.
Either way, a stale session stops working the moment the secret changes — so if
you ever suspect someone got in, rotate the password and they are out.

Both accept a long password — you type it only once per device. In the FastAPI
edition a sign-in lasts 30 days; in the Streamlit edition it lasts until the tab
closes or the app restarts (in practice, the next sleep cycle).
