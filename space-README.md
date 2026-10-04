---
title: Video Outreach
emoji: 🎬
colorFrom: indigo
colorTo: purple
sdk: streamlit
app_file: streamlit_app.py
pinned: false
short_description: Crawl Shopify stores and write personalized outreach
---

# Video Outreach

Give it a list of Shopify stores. It reads each storefront — homepage, about page,
privacy policy, products — then writes a message that could only have been written
for that store owner. You review it, tap send, and it opens in your email app
pre-filled.

<!-- MODE-BLOCK:START -->
This file is the Space's configuration. The **frontmatter above is required** —
`sdk: streamlit` tells Hugging Face how to run the app, and `app_file` names the
script it starts. Without it the Space won't boot.

```yaml
sdk: streamlit
app_file: streamlit_app.py
```

> **Why Streamlit and not Docker?** In July 2026 Hugging Face started charging for
> the Docker and Gradio SDKs on `cpu-basic`; the Streamlit SDK is still free. The
> project also ships a FastAPI edition (`app/main.py`) with a Dockerfile, and
> `./deploy-space.sh --docker` publishes that instead — but it needs a paid Space.

### Signing in

Use the direct URL **`https://YOURNAME-NAME.hf.space`**. Unlike the FastAPI
edition — whose session cookie browsers block inside frames — the Streamlit
edition holds your sign-in in the browser session, so the embedded view on
huggingface.co works too. The direct URL is just the shorter one to bookmark.

### Coming from the Docker edition

If your Space already ran the FastAPI edition, redeploying with the script
(default Streamlit mode) rewrites the frontmatter and swaps the requirements
file, and the Space rebuilds as Streamlit. Two things to expect:

- The old `APP_PASSWORD` secret keeps working — same variable name.
- The database starts empty, because the rebuild wipes the disk. **Take a backup
  from the FastAPI edition first** (Backup button), then restore it in the
  Streamlit edition (tab 4, "Restore from a backup").
<!-- MODE-BLOCK:END -->

Full documentation lives in `DEPLOY-FREE.md` and `README.md`.

---

## Set the password before you use this

This Space is **public on the free tier**, meaning anyone can read the code and
reach the URL. The app itself is protected by a single password, which lives in a
Space **secret** — never in a file:

> **Settings** → **Variables and secrets** → **New secret**
> Name: `APP_PASSWORD`
> Value: your password

The Space restarts and picks it up automatically. Optional extras, added the same
way:

| Secret | Purpose |
|---|---|
| `OPENAI_API_KEY` | Write messages with an AI model instead of templates |
| `OUTREACH_DB` | Database path. Defaults to `outreach.db` in the project folder |

**Do not put the password in this repository.** Everything committed here is
served publicly.

---

## Free tier notes

- **Sleeps after 48 hours idle**, then takes 30–90 seconds to wake on the next visit.
- **Storage is ephemeral.** The disk is wiped on every rebuild, so a redeploy
  loses your leads and messages. Use the **Backup** button (tab 4) before you
  push changes, and **Restore** after.
- **Sign-in works in the embedded view too.** The Streamlit edition keeps your
  signed-in state in the browser session rather than a cookie, so the frame on
  huggingface.co does not block it. The direct URL is still the shorter thing to
  bookmark.
