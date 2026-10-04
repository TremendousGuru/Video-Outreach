# Deploying for free (verified October 2026)

The short version: **Render's free tier still exists and works, but Hugging Face
Spaces is the better free host for this particular app.** Everything else died.

---

## The 2026 landscape, checked not remembered

| Host | Free tier status | Verdict for this app |
|---|---|---|
| **Render** | Free web service, 750 hrs/mo, no card | Works. Sleeps after **15 min**, **no disk** |
| **Hugging Face Spaces** | Free CPU: **2 vCPU / 16 GB RAM**, ~50 GB disk | **Best free option.** Sleeps after **48 hrs** |
| Railway | No permanent free tier — one-off $5 trial, then ~$1/mo credit | Not viable |
| Fly.io | No free tier for new users — ~2 hour trial | Not viable |
| Heroku | Free plans removed in 2022 | Not viable |
| Koyeb | Free tier moved behind paid; the leftover free service scales to zero | Not viable |
| Vercel / Netlify / Cloudflare Pages | Free, always on — but static/serverless only | Can't run a crawl |

Sources: [Render's own comparison](https://render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026),
[a 2026 free-tier survey](https://livemy.app/blog/free-hosting-that-doesnt-sleep).

**The honest conclusion:** no free host gives you a persistent disk. Every free
option wipes your database eventually, so the real answer is *make the data
survivable* — which is what the Backup button in this app is for.

---

## Why Hugging Face Spaces wins here

| | Render free | HF Spaces free |
|---|---|---|
| RAM | 512 MB | **16 GB** |
| CPU | 0.1 vCPU | **2 vCPU** |
| Sleeps after | 15 minutes | **48 hours** |
| Disk | None | ~50 GB (survives sleep, wiped on rebuild) |

For a crawler that holds several storefronts open at once, 16 GB vs 512 MB is not
a small difference — Render free will rate-limit you sooner because it can't run
much in parallel. And 48 hours of idle tolerance means you can use it in bursts
across a working week without paying a cold-start tax every time.

Two trade-offs to know: **free Spaces are public** (anyone can see the Space and
its code — your login still protects the data), and the disk is **wiped on every
rebuild**, though it survives sleep cycles.

---

## Option 1 — Hugging Face Spaces (recommended)

1. Create an account at [huggingface.co](https://huggingface.co) — no card needed.
2. **New** → **Space**.
   - Name: `video-outreach`
   - **SDK: Docker** (not Gradio or Streamlit)
   - Hardware: **CPU basic** (free)
   - Visibility: Public (free Spaces can't be private)
3. **Settings → Repository** — you can push this repo's files up via the web UI,
   or use Git:
   ```bash
   git remote add hf https://huggingface.co/spaces/YOURNAME/video-outreach
   git push hf main
   ```
   Spaces deploy from a Git push, so the Dockerfile in this repo is all it needs.
4. **Settings → Variables and secrets** — add these **as Secrets**:
   | Name | Value |
   |---|---|
   | `APP_PASSWORD` | a long password you choose |
   | `OPENAI_API_KEY` | optional |
5. The Space builds. First build takes 3–5 minutes. Your URL is
   `https://YOURNAME-video-outreach.hf.space`.

The Dockerfile already handles the one non-obvious requirement: **Spaces requires
the app to listen on port 7860**, and it's configured to do exactly that.

---

## Option 2 — Render free tier

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

## Option 3 — keep it local, tunnel when needed

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

## Option 4 — the durable fix (needs a small code change)

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
| Want the most capacity free, data loss acceptable | **HF Spaces** |
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
| "Could not sign in" on a tab left open | Password changed, or container restarted | Reload and log in again |
| Backup download does nothing | Not signed in — the backup holds real emails, so it's auth-gated | Log in first |

---

## Do this before you go live

1. **Set `APP_PASSWORD`.** Free hosting makes your app publicly reachable the
   moment it deploys. Unset `APP_PASSWORD` means no login at all.
2. **Make the GitHub repo private** if you haven't. A public Space publishes the
   code too.
3. **Take a backup** once you have a real lead list worth keeping.
4. **Start with 5 stores**, read the messages, tune the wording, then run the rest.
