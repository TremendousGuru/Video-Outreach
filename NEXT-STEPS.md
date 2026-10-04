# Deploy it — the short version

Your code is on GitHub. This is the rest, start to finish.

**Where this deploys to:** **Streamlit Community Cloud**
(https://share.streamlit.io) — a free host that connects to your GitHub repo.
Nothing is live anywhere yet.

**Not Hugging Face.** We tried it: the Space took the upload, then refused to
start with `Quota exceeded for flavor cpu-basic ... limit=0`. Hugging Face no
longer gives new free accounts any compute — not for Streamlit, not for Gradio,
not for Docker. Your Space is back to its original placeholder; nothing of yours
is broken there.

> The word "Streamlit" is used for two different things and they are easy to mix
> up. It is the name of the *app framework* this project uses, **and** the name of
> this *hosting company*. Hugging Face Spaces has a Space type also called
> "Streamlit", which is a different thing again. Both hosts run the same app; this
> guide uses Streamlit Community Cloud because it needs no shell — it just
> connects to your GitHub repo.

---

## 0. Optional but recommended: make the repo private first

The repo is **public** right now. Anyone can read the crawler and see the tool.

> Repo page → **Settings** → bottom → **Danger Zone** → **Change repository
> visibility** → **Private**

You can still deploy to Streamlit Community Cloud with a private repo — it
connects through GitHub's login, not by scraping public pages.

---

## 1. Deploy the app

1. Go to **https://share.streamlit.io** and sign in with GitHub.
2. **Create app** → **Deploy a public app from GitHub**.
3. Fill in exactly this:

   | Field | Value |
   |---|---|
   | Repository | `TremendousGuru/Video-Outreach` |
   | Branch | `main` |
   | Main file path | `streamlit_app.py` |

4. Open **Advanced settings** → **Secrets** and paste this, with your own
   password in the quotes:

   ```toml
   APP_PASSWORD = "your password here"
   ```

   This is the only place your password should ever live. Not in a file, not in
   the repo — the repo is readable by anyone who finds it.

5. **Deploy.** First build takes a couple of minutes.

Your URL will be `https://something.streamlit.app`.

---

## 1b. Optional: turn on the AI writing

The app works without this — you get template-written messages instead. With a
key, each message is written by a model from the facts it found on the store.

### There is no file to edit

Your key goes in the **Secrets box**, which is a web page, not a file:

> Streamlit Cloud → your app → **Manage app** (bottom right) → **Settings** →
> **Secrets**

Paste it there and press Save. The app restarts and picks it up.

**GitHub must never contain your key or password.** Everything in your repo is
readable by anyone who finds it — a key committed there is a key given away.

(If you ever run the app on a computer instead of the web, the equivalent file is
`.env` in the project folder, or `.streamlit/secrets.toml`. Both are already
gitignored here. On Streamlit Cloud you don't touch either.)

### Using Google Gemini (your setup)

**Step 1 — get the key.** At
[**aistudio.google.com/apikey**](https://aistudio.google.com/apikey) (your
`video-outreach-510605` project is fine — the project name is not needed
anywhere in this app) → **Create API key** → copy it. It starts with `AIza`.

> That page is where the key is *issued*. It is not where the app is hosted, and
> nothing you do there makes the app run — the key is just a password for
> Google's model.

**Step 2 — paste it into Secrets**, with the other two lines:

```toml
APP_PASSWORD = "your password"
OPENAI_API_KEY = "AIza..."
OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
OPENAI_MODEL = "gemini-3.8-flash"
```

The `OPENAI_`-prefixed names are just what the app calls those settings — the
value is your Google key and it is sent to Google, nowhere else.

You can also set the provider inside the app: sidebar → **Writing** →
**Provider** → **Google Gemini**, which fills in the address and a model. The key
itself still only ever lives in Secrets.

**Step 3 — press Test the key.** It confirms the key and the model name in a
second, and if the model name is wrong it prints the ones your key can use.

### Which key do you have? AQ. versus AIza (both fine)

Google is mid-migration between two key types, and it changes nothing about how
you configure the app:

| | Standard key | **Authorization (auth) key** |
|---|---|---|
| Looks like | `AIza...` | **`AQ.Ab8...`** |
| Bound to | just a Google Cloud project | a project *and* a service account |
| Restrictions | must be restricted by hand | **restricted to Gemini API by default** |
| Status | being retired | **every new AI Studio key since 28 May 2026** |

So a key starting `AQ.Ab8` is not a mistake or the wrong thing copied — it is the
current format, and it works. Three details from Google's
[key documentation](https://aistudio.google.com/docs/api-key) worth knowing:

- **Restriction matters.** Since 19 June 2026 the API **rejects unrestricted
  standard keys**. Auth keys (yours) are restricted from birth, so this is not
  your problem — but if you have an older `AIza` key lying around, look for the
  **Unrestricted** label in AI Studio and click **Add restrictions**, or it will
  be refused.
- **Dormant keys get blocked.** Since 7 May 2026, an unrestricted key that sits
  unused for a long time gets a **Blocked** tag. Generate a fresh key if you see
  that.
- **Where to look:** [aistudio.google.com/api-keys](https://aistudio.google.com/api-keys)
  has a **Key Type** column that tells you which sort each key is.

**Nothing in the app needs to change for either type.** Verified against the live
endpoint: the OpenAI-compatible address accepts both formats and *requires* the
`Authorization: Bearer` header — which is exactly what this app sends. (Some
tools wrongly send `AQ.` keys to Google's Vertex `aiplatform` address, which
rejects them; this app does not do that.)

### Gemini's free tier, and what it means for crawling

| | Free tier |
|---|---|
| Models | **Flash and Flash-Lite only** — Pro models moved behind billing in April 2026 |
| Requests/minute | ~10 for Flash (15 for Flash-Lite) |
| Requests/day | ~1,000–1,500, resets midnight Pacific |

Every store in your list is one request. Two practical consequences:

- **Keep "Stores at once" at 2–3.** At 10 requests a minute, 8 at once just
  queues up 429 errors. Slow and steady finishes the batch sooner.
- **Run in batches of 20–40**, not 200. That fits inside the daily allowance and
  is better for deliverability anyway.

If the app does hit a rate limit it now **waits and retries** (honouring Google's
own `Retry-After`) rather than throwing the AI message away. If it still fails
after three tries, that one store falls back to a template and the log says so —
your other stores are unaffected.

**If you need more throughput**, switch `OPENAI_MODEL` to
`gemini-3.1-flash-lite` — 15 requests a minute instead of 10, and cheaper.

### Then check it, once

Sidebar → it should say **API key: set**, and show the address it will send to.
Press **Test the key**. It sends one tiny request and answers in a second:

| What it says | What it means |
|---|---|
| `Working - ai:gemini-3.8-flash` and a sample subject | Done. Nothing else to do. |
| `API 400: Please pass a valid API key` | Google rejected the key — wrong or revoked |
| **"The key works, but it cannot see the model …"** plus a list | The key is fine; the model name is not. Copy one from the list it prints and paste it into **Model** |
| `timeout` / connection error | Wrong `OPENAI_BASE_URL` |

That middle row is the one to expect: model names change, and the app asks Google
what your key can actually use rather than leaving you guessing.

### If Google denies your project (403 "denied access")

If Test the key reports:

```
API 403: Your project has been denied access. Please contact support.
```

then **your key is fine** — Google has flagged the *project*, not the key. You can
tell because the AI Studio playground and model listings still work while
generation is refused. Creating a new key, or a new project on the same account,
usually fails the same way, because the flag sits on the account.

**It is not your country.** Nigeria is on
[Google's supported list](https://ai.google.dev/gemini-api/docs/available-regions),
and this error has been reported all through 2026 by people in India, Brazil,
Morocco and the US, on brand-new projects with no usage history. It is an
automated abuse filter firing on innocent accounts.

Google's own advice is to **enable billing**, which clears the flag for most
people — but that moves you to the paid tier, so it's not a free option. The
other route is a manual review request on
[discuss.ai.google.dev](https://discuss.ai.google.dev) — slow, and many of those
threads are still unanswered.

**Check this first, it costs nothing.** Open your
[API keys page](https://aistudio.google.com/api-keys) and look at the project's
**Billing Tier**. If it reads **"Unavailable"** rather than "Free tier", that is
the flag — Google has disabled API access for that project, and no amount of
key-swapping will fix it.

**Two free things to try, in this order:**

1. **A new project, then a new key.** AI Studio → **Projects** → create one, then
   create a key in it. Reports say this usually fails the same way, because the
   flag sits on the account — but it takes two minutes and costs nothing.
2. **A different Google account.** This is the workaround people report actually
   working: the flag is per-account, so a brand-new Google account issues working
   keys. Free, but you'd be starting that account's Gemini access from scratch.

**Then the practical fix: a different free provider.** The app speaks the OpenAI
format, so switching is three lines in Secrets and nothing else changes. Easiest
of all: sidebar → **Writing** → **Provider**, pick one, then put its key in
Secrets.

> **First, mind the names — these are two different companies:**
>
> | | What it is | Free API? |
> |---|---|---|
> | **Grok** | xAI's chatbot (Musk). Spelling: one `o`. | **No.** Only ~$25 of trial credits, then pay-per-token |
> | **Groq** | An inference company running open models on its own LPU chips. Spelling: two `o`s. | Free tier, by their docs |
>
> If you were reading about **Grok**, you are right — its API has no free tier,
> and it was never one of my suggestions. My suggestion was **Groq**.

Free tiers move constantly, and I am quoting secondhand numbers, so **check before
you invest time**: the app's **Test the key** button is the real answer, and each
provider's console shows your own live limits.

| Provider | Free tier | Confidence | Where to get a key |
|---|---|---|---|
| **GitHub Models** | 10–15 req/min, 50–150/day, uses the GitHub account you already have | Verified in GitHub's own docs | [github.com/settings/tokens](https://github.com/settings/tokens) |
| **Groq** | Makes a free tier available, no card — *but their rate-limit page labels its table "base limits for the Developer plan"*, so the exact free numbers are unclear | **Unverified — check their console** | [console.groq.com/keys](https://console.groq.com/keys) |
| OpenRouter | `:free` models, ~20/min, 50/day | Reported, unverified | [openrouter.ai/keys](https://openrouter.ai/keys) |

**GitHub Models carries a real caveat:** its free tier terms restrict use to
*prototyping and experimentation*. Running a business tool on it is a grey area,
so treat it as a way to try the AI writing, not a long-term home for it.

For Groq, paste this into Secrets instead of the Gemini lines:

```toml
OPENAI_API_KEY = "gsk_..."
OPENAI_BASE_URL = "https://api.groq.com/openai/v1"
OPENAI_MODEL = "openai/gpt-oss-120b"
```

For GitHub Models, the key is a GitHub token with the **Models: read** permission,
and the model name includes its publisher:

```toml
OPENAI_API_KEY = "github_pat_..."
OPENAI_BASE_URL = "https://models.github.ai/inference"
OPENAI_MODEL = "openai/gpt-4o-mini"
```

**The one option that cannot be taken away: no key at all.** Without any API
key the app still reads every store and still writes each message - from its
templates instead of a model. It is guaranteed free, needs no signup, and cannot
be rate-limited or flagged. The messages are more formulaic, but they are built
from the real facts found on each store. Run your list that way, and turn a model
on later, once one works for you.

Whichever you pick, press **Test the key** again. The app also prints the models
your key can actually use if the one you named isn't available.

> **Nothing is blocked while you sort this out.** Stores still get messages — the
> app writes them from its templates instead of the model, and the crawl log
> flags which ones. You can run your whole list today and switch the AI on later.

### Other providers

| Service | `OPENAI_BASE_URL` | Example model |
|---|---|---|
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-3.8-flash` |
| Groq | `https://api.groq.com/openai/v1` | `openai/gpt-oss-120b` |
| GitHub Models | `https://models.github.ai/inference` | `openai/gpt-4o-mini` |
| OpenRouter | `https://openrouter.ai/api/v1` | `qwen/qwen3.8-27b:free` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| Together | `https://api.together.xyz/v1` | `meta-llama/Llama-3.3-70B-Instruct-Turbo` |

### If a key stops working mid-crawl

Nothing breaks. That store's message is written from templates instead, and the
progress line says so — `⚠️ AI failed, used templates: ...`. The other stores
carry on.

---

## 2. First five minutes on the live app

1. Open the URL, type the password, sign in.
2. **Tab 1 · Add list** — upload a CSV/XLSX/TXT, or paste a few rows. Start with
   5 stores, not 500.
3. **Tab 2 · Crawl & compose** — press **Crawl & compose**. Watch it work through
   the stores one at a time. Pressing it again later only does the ones not
   finished yet.
4. **Tab 3 · Review & send** — read each message, edit anything that reads
   wrong. **Open in my email app** fills in the subject and body; you press send.
5. Once the wording feels right, add the rest of your list and run it in batches
   of 20–40.

---

## 3. Two habits that save you

**Back up before every redeploy.** Tab 4 → **Backup everything**. Free hosts wipe
the disk on rebuild, and Streamlit Cloud also reboots after 12 hours idle.

**Restore after.** Tab 4 → **Restore from a backup** → choose the file → **Restore
now**. Everything comes back: leads, messages, and your settings.

---

## Troubleshooting

| What you see | What it means |
|---|---|
| "Oh no. Something went wrong." on first load | The build is still finishing. Wait a minute, reload. |
| App is slow the first time you open it | It was asleep. Normal on free. |
| Sidebar says "API key: not set" after you added one | The secret name is misspelled, or you saved it in Variables instead of Secrets |
| Messages read generic and repetitive | No key set, or the key failed — the crawl log says which |
| "The password was changed, so you were signed out." | You changed the secret. Sign in with the new one. |
| Messages gone after a redeploy | Expected on free hosting — restore your backup. |
| Changed something and the site looks stale | **Manage app** → **Reboot**. |

---

## Where to read more

- `README.md` — what the tool does and how the writing works
- `DEPLOY-FREE.md` — every free host compared, and the streamlit-vs-docker story
- `ANDROID.md` — running it on the phone with no server at all
