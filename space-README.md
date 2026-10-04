---
title: Video Outreach
emoji: 🎬
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
short_description: Crawl Shopify stores and write personalized outreach
---

# Video Outreach

Give it a list of Shopify stores. It reads each storefront — homepage, about page,
privacy policy, products — then writes a message that could only have been written
for that store owner. You review it, tap send, and it opens in your email app
pre-filled.

This file is the Space's configuration. The **frontmatter above is required** —
`sdk: docker` tells Hugging Face how to build, and `app_port: 7860` is the port
the container listens on. Without it the Space won't start.

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
| `OUTREACH_DB` | Database path. Defaults to `/data/outreach.db` |

**Do not put the password in this repository.** Everything committed here is
served publicly.

---

## Free tier notes

- **Sleeps after 48 hours idle**, then takes 30–90 seconds to wake on the next visit.
- **Storage is ephemeral.** The disk is wiped on every rebuild, so a redeploy
  loses your leads and messages. Use the **Backup** button before you push
  changes, and **Restore** after.
- Use the direct URL rather than the embedded view on huggingface.co — browsers
  block sign-in cookies inside embedded frames.
