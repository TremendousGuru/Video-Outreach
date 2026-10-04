# Deploying to Render

Ten minutes, and about $7/month if you want your data to survive a deploy.

---

## Read this first: what changes when it's on the internet

The app was built as a local tool, where "no authentication" is fine because
nothing else can reach it. On a public URL that becomes the whole problem:

- **Anyone who finds the URL can drive the crawler.** That traffic comes from
  *your* server's IP. If someone points it at a few thousand stores, the abuse
  complaints land on your account, not theirs.
- **They'd see your lead list and every message you generated.** That's your
  pipeline, readable by a stranger.
- Render scans new services and search engines index `onrender.com` subdomains,
  so "nobody will find it" is not a security plan.

So the app now has a login. Set `APP_PASSWORD` and it's protected; leave it
unset and it runs open, exactly as it does on your laptop. The `/health`
endpoint reports which mode you're in:

```json
{"ok": true, "auth": "enabled", "hosted": true, "leads": 0}
```

A crawl also refuses to fetch private/internal addresses now, so a lead list
containing `169.254.169.254` or an internal hostname can't make your server
probe its own network.

---

## Option A - blueprint (recommended)

1. Push this repo to GitHub (already done).
2. Render dashboard → **New** → **Blueprint**.
3. Pick the repo. Render reads `render.yaml` and shows what it will create.
4. It will ask for **APP_PASSWORD** - type a long random one. Use a password
   manager; this is the only thing between your data and the internet.
5. Optionally paste **OPENAI_API_KEY**. Skip it and the app writes from
   templates.
6. **Apply**. First build takes 2-4 minutes.

When it's live, open the URL - you'll get the login page. Bookmark it.

## Option B - by hand

**New** → **Web Service** → your repo, then:

| Setting | Value |
|---|---|
| Runtime | Python |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1 --proxy-headers --forwarded-allow-ips="*"` |
| Health check path | `/health` |
| Instance type | Starter (free works for a test - see below) |

Environment variables:

| Key | Value |
|---|---|
| `APP_PASSWORD` | a long random password |
| `OUTREACH_DB` | `/var/data/outreach.db` |
| `OUTREACH_HOSTED` | `1` |
| `OPENAI_API_KEY` | optional |
| `PYTHON_VERSION` | `3.12.6` |

Then **Disks** → **Add disk**: name `outreach-data`, mount path `/var/data`,
1 GB. Attach it and save; the service restarts.

---

## The free plan, honestly

It works for kicking the tyres, with two caveats that will bite you:

**1. No persistent disk on free.** Render only allows disks on paid instances.
Without one, `outreach.db` lives in the container's ephemeral filesystem, so
**every deploy and every restart wipes your leads and your generated messages.**
The app detects this and warns you in the header.

If you want to try free anyway: **export your CSV before every deploy**, and
expect to re-upload your list each time.

**2. It sleeps after 15 minutes idle.** The next request takes ~30 seconds to
wake it. Worse for this app specifically: **a crawl running when it sleeps gets
killed mid-run.** The database handles that gracefully (interrupted rows go back
to the queue and you re-run), but a 50-store crawl will not finish on free.

Free is for testing the deployment. Starter is for actually using it.

---

## Sizing

| Instance | RAM / CPU | Comfortable for |
|---|---|---|
| Free | 512 MB / 0.1 | testing, a handful of stores |
| Starter | 512 MB / 0.5 | steady use, batches of 20-50 |
| Standard | 2 GB / 1.0 | long batches, AI writing on every store |

Concurrency is yours to tune in **Settings → Stores at once**. On a Starter
instance, 3 or 4 is right. The default of 4 is already sensible; raising it
mostly gets you rate-limited by the stores, not faster results.

**Keep it at one instance.** Crawl runs and their live progress live in the
process's memory. Scale to two and a run started on one is invisible to the
other - the UI would show you half a batch. `render.yaml` pins `numInstances: 1`.

---

## After it's live

1. **Sign in** with your password.
2. **Settings** → set your name, your one-line pitch, the subject style. Do this
   before your first real run; an unsigned email with no offer reads like spam.
3. If you set an API key, hit **Test** and confirm it returns a sample subject.
4. Upload a list of **5 stores** and run it. Read the five messages.
5. Happy? Run the rest.

`--limit`-style chunking from CLI mode has a web equivalent: paste a small list,
crawl it, then upload the next chunk. Uploads append, and exact duplicates are
skipped, so re-uploading the same file is harmless.

---

## Things that will confuse you

**"Not signed in" on a stale tab.** The cookie lasts 30 days, but a redeploy
restarts the container; if you changed `APP_PASSWORD`, old cookies stop working.
Just reload and log in.

**Progress bar frozen.** The live progress uses Server-Sent Events. Some mobile
networks buffer streaming responses. The crawl is still running server-side -
reload the page and it reattaches to the run in progress via `/api/run/current`.

**A run vanished after a restart.** Restarts kill in-flight crawls. Rows that
were mid-crawl return to the queue; press **Crawl & compose** again and it
resumes where it stopped.

**Data looks reset.** You're on the free plan, or `OUTREACH_DB` isn't inside the
disk mount path. Check `/health` and the warning banner in the app header.

**Login page loops.** The cookie is marked `Secure`, and it only sticks over
HTTPS. Render gives you HTTPS automatically, but if you're hitting the service
over plain HTTP through some proxy, that's the cause.

---

## Alternative: keep it local, expose it only when needed

If the point of hosting is "use it from my phone", you can skip Render entirely
and run it on your laptop with a tunnel:

```bash
cloudflared tunnel --url http://localhost:8848
```

Cloudflare gives you a temporary public HTTPS URL. **Set `APP_PASSWORD` before
doing this** - the URL is public while the tunnel is up, even though it's
unlisted. Stop the tunnel and the surface is gone. No monthly bill, no data
leaving your machine.
