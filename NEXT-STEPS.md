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

### Using Google Gemini

Paste this, with your own values:

```toml
APP_PASSWORD = "your password"
OPENAI_API_KEY = "AIza..."
OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
OPENAI_MODEL = "gemini-2.5-flash"
```

The `OPENAI_`-prefixed names are just what the app calls them — the value is your
Google AI Studio key, and it is sent to Google.

You can also pick the provider inside the app: sidebar → **Writing** →
**Provider** → **Google Gemini**. That sets the address for you. The key itself
still only ever lives in Secrets.

### Then check it, once

Sidebar → it should say **API key: set**, and show the address it will send to.
Press **Test the key**. It sends one tiny request and answers in a second:

| What it says | What it means |
|---|---|
| `Working - ai:gemini-2.5-flash` and a sample subject | Done. Nothing else to do. |
| `API 400: Please pass a valid API key` | Google rejected the key — wrong or revoked |
| **"The key works, but it cannot see the model …"** plus a list | The key is fine; the model name is not. Copy one from the list it prints and paste it into **Model** |
| `timeout` / connection error | Wrong `OPENAI_BASE_URL` |

That middle row is the one to expect: model names change, and the app asks Google
what your key can actually use rather than leaving you guessing.

### Other providers

| Service | `OPENAI_BASE_URL` | Example model |
|---|---|---|
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.5-flash` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| OpenRouter | `https://openrouter.ai/api/v1` | `openai/gpt-4o-mini` |
| Groq | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |
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
