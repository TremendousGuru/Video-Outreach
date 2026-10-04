# Outreach Studio

Upload a list of Shopify stores → the app crawls each one → writes a message that could only
have been written for that store owner → you review it → one click opens your email client with
the subject and body already filled in.

Everything runs on your machine. Nothing is sent without you pressing send.

---

## Run it

```bash
cd shopify-outreach
python3 -m pip install -r requirements.txt
python3 -m app.main
```

Then open **http://localhost:8848**.

Or use the launcher, which creates a virtualenv for you:

```bash
./run.sh
```

If you get `Permission denied`, some zip/transfer tools drop the executable bit:

```bash
chmod +x run.sh push-to-github.sh
```

`bash run.sh` works too and needs no permission bit.

---

## The workflow

### 1. Add your list
Drag in a **CSV, XLSX or TXT** file, or paste rows straight into the box. Column order doesn't
matter and headers are optional — it reads the values and works out which column is which.

| Accepted | Example |
|---|---|
| `email, domain, store_name` | `hq@deathwishcoffee.com, deathwishcoffee.com, Death Wish Coffee` |
| domain only | `browngirljane.com` |
| email only (domain inferred) | `hello@allbirds.com` |
| any order, any separator | `hello@ogg.com \| ogg.com \| OGG` |

If a row has no email, the crawler looks for one on the site (homepage, contact and policy pages)
and fills it in. Missing store names are replaced with the store's real name from its own site.

### 2. Crawl & compose
Press **Crawl & compose**. Per store it reads:

- **Homepage** — tagline, categories, what they sell
- **`/pages/about`** — story, founder name, founding year, brand values (this is where the best hooks live)
- **Privacy policy, shipping, returns, contact** — proves a real visit and often surfaces details worth referencing
- **`/products.json` + `/meta.json`** when the site is Shopify — real product names, prices, currency, collections, city

It then writes one message per store from those facts: a subject line plus three options, an opener
that references something specific and checkable, who you are, the video offer, and a CTA.

Progress streams live. You can close the tab; the work continues in the background and rows update
when you come back.

### 3. Review and send
Click **Open** on any row. You get three tabs:

- **Message** — pick one of three subjects, edit the body freely, **Rewrite this one**
- **What we found** — the exact facts the message was built from (products, founder, signals, socials)
- **Sources** — every URL that was read and its HTTP status, so you can verify the personalization

Then **Open in my email app** (or **Open in Gmail web**) — subject and body land pre-filled and you
press send. The row flips to `sent` so you don't double-email anyone.

---

## How the writing works

**With an API key** (Settings → API key): each store's fact sheet goes to an LLM with strict rules —
never invent facts, never claim to have bought the product, no markdown, no "I hope this email finds
you well". It returns a subject set plus the body as JSON.

Works with any OpenAI-compatible endpoint — change **Base URL** + **Model** for OpenRouter, Groq,
Together, or a Gemini compatibility endpoint. Use **Test** to confirm before a big run.

**Without a key**: smart templates write from the same facts (real product name, real price point,
real brand signal). Slower to get stale than you'd think, and free. Any key failure (bad key, rate
limit, network) silently falls back to templates and tells you why in a toast.

---

## Settings that matter most

| Setting | Why |
|---|---|
| **Your name / company** | Used in the sign-off. Without it your emails end without a sender. |
| **What you do** | The one-line pitch, e.g. "I make short product videos for Shopify stores". |
| **How you pitch the video** | Since you don't have the video yet, the default teases it: "I already put together a short one for [product] to show you what I mean". Change it once you have a link. |
| **Subject 1 idea** | Drives the blunt first subject, default `is this store active`. |
| **Opt-out line** | Keeps you on the right side of spam law and improves replies. Leave it on. |
| **Stores at once** | 4 is polite. Above 6 invites rate-limiting. |
| **Delay between requests** | 0.5s is fine for most hosts. Raise it if a store starts refusing connections. |
| **Respect robots.txt** | On by default. Leave it on. |

---

## Deliverability, honestly

This is the part that decides whether the tool works, not the code.

- **Volume kills.** Gmail allows ~500 recipients/day and far less for a new account. 20–40/day from a
  normal address beats 300 from a fresh one that gets throttled or flagged.
- **Warm up.** Send a handful a day for the first week, reply to your own test sends, then scale.
- **Plain text wins.** These messages have no images, no tracking pixels, no HTML — that's deliberate.
- **Personalization is your best filter-avoider.** A message that names the actual product is not the
  pattern spam filters are trained on.
- **Reply or stop.** If someone replies "no", stop. The app tracks `sent` rows so you don't hit them twice.
- **Rules vary.** Cold B2B email is legal in the US under CAN-SPAM (with a real identity and opt-out).
  In the EU/UK, GDPR/PECR generally require a legitimate-interest basis and a clear opt-out — plus the
  subject should be relevant to their business role. In Canada, CASL wants consent or a real
  existing-relationship basis. Use the opt-out line and keep volumes sane.

---

## When a store fails

Rows are marked `failed` with the reason, and a **Re-crawl site** button appears in the drawer.

| Message | What it means |
|---|---|
| `homepage unreachable (HTTP 403)` | Store is blocking bots. Retry later or raise the delay. |
| `robots.txt disallows crawling` | Their robots.txt forbids it. Respect it — that's why it's checked. |
| `no products found` | Not Shopify, or a password-protected/coming-soon store. |
| `no real about page prose found` | The about URL exists but is only navigation. The message uses homepage facts instead. |
| Row shows `no email yet` | Nothing findable on the site — add it to your sheet manually. |
| `AI failed, used template` | The toast names the API error (bad key, quota, model name). Templates filled in meanwhile. |

Flags on each row tell you what's soft: `not detected as Shopify`, `site may be password-protected`,
`generic mailbox (gmail/yahoo) - not a brand domain` (that last one is a real deliverability signal).

---

## Export

- **Export CSV** — `email, store_name, domain, subject, body, status, engine, hook, sent_at`
- **Export JSON** — the same plus the full fact sheet per store, for use in your own tooling

Useful if you'd rather load the finished messages into a sending platform instead of clicking through
your mail client.

---

## Deploying it (Render)

Built as a local tool, so two things change when it goes on a public URL — see
**[DEPLOY-RENDER.md](DEPLOY-RENDER.md)** for the full walkthrough:

1. **Set `APP_PASSWORD`.** With it set you get a login page and every route is
   protected. Leave it unset and the app runs open, like it does on your laptop.
   `/health` tells you which mode you're in.
2. **Mount a disk.** Render's free plan can't attach one, so `outreach.db` is
   wiped on every deploy. Point `OUTREACH_DB` inside a mounted disk
   (`/var/data/outreach.db`), or the app shows a warning banner in the header.

A blueprint is included:

```bash
# Render dashboard -> New -> Blueprint -> pick this repo
# it reads render.yaml, then asks you for APP_PASSWORD
```

It also refuses to crawl private/internal addresses, so a lead list containing
`169.254.169.254` or an internal hostname can't make the server probe its own
network.

## No web server? Use CLI mode

If the web UI won't run — on a phone, a cheap laptop, a locked-down machine —
there's a command-line mode that needs only `httpx` and `beautifulsoup4`:

```bash
python3 -m app.cli leads.csv --limit 5
```

It crawls, writes, and produces **`outbox.html`** — a tappable page where each
store is a card with its subject, body and an "Open in Gmail" button that opens
your mail app pre-filled. Re-running skips stores already finished, so you can
work through a list in chunks.

```
--limit 5        only the first 5 stores (always start here)
--redo           re-crawl and rewrite ones already done
--no-ai          force templates even if a key is set
--concurrency 3  how many stores at once
```

Outputs `outbox.html`, `messages.csv` and `outbox.json` (run state). All three are
gitignored, since they contain real email addresses.

**On Android with Pydroid 3:** see **[ANDROID.md](ANDROID.md)** — CLI mode is the
recommended path there, and it covers what Pydroid can and can't do (it has no
`git`, so pushing your code needs Termux, GitHub's web uploader, or a computer).

## Environment variables (optional)

Instead of typing your key into the app, you can keep it in a `.env` file — which is **gitignored**, so
it never ends up in the repo or in `outreach.db`:

```bash
cp .env.example .env      # then edit .env
```

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Same as pasting a key in Settings. |
| `OPENAI_BASE_URL` | Point at OpenRouter, Groq, Together, a Gemini compatibility endpoint. |
| `OPENAI_MODEL` | e.g. `openai/gpt-4o-mini`, `llama-3.3-70b-versatile`. |
| `HOST` / `PORT` | Where the app listens. Default `0.0.0.0:8848`. |

Real environment variables win over `.env`, and anything you save in the Settings panel wins over both
(clear it with the **Forget** button next to the key field).

## Files

```
shopify-outreach/
├── run.sh                 one-command launcher (creates .venv, installs, runs)
├── push-to-github.sh      push this project to an empty GitHub repo
├── requirements.txt
├── .gitignore             keeps your lead data and keys out of git
├── .env.example           copy to .env for key/base-url/model config
├── outreach.db            your data (SQLite) - gitignored, delete to start over
└── app/
    ├── __init__.py        loads .env on any import
    ├── main.py            FastAPI routes + export endpoints
    ├── ingest.py          CSV/XLSX/TXT parsing, column detection
    ├── crawler.py         Shopify JSON + HTML crawling, fact extraction
    ├── compose.py         LLM prompt + template writer
    ├── pipeline.py        batch queue, concurrency, live event stream
    ├── db.py              SQLite schema and queries
    └── static/            the interface
```

## What not to commit

`outreach.db` holds real email addresses, store names and your notes. Publishing it would expose
other people's contact details, so it's gitignored — along with `.env`, exported lead lists and any
CSV you dropped in. If you ever need to check what git is about to include:

```bash
git status --short          # what's staged/changed
git check-ignore -v outreach.db    # confirm something is ignored
```

`.gitignore` is not a security boundary, though — if a file was ever committed, ignoring it later
doesn't remove it from history. If you think you pushed something private, treat the file as
compromised and rotate whatever was in it.

**Keep the repo private.** A tool whose purpose is cold-emailing Shopify stores, with the crawler
code visible, is not something you want indexed and searchable.

Re-crawling a lead refreshes its facts and rewrites the message. **Rewrite this one** keeps the facts
and writes a fresh message from them — that's how you get variety when two stores in a batch are similar.
