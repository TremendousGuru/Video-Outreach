# Deploy it — the short version

Your code is on GitHub. This is the rest, start to finish.

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
| "The password was changed, so you were signed out." | You changed the secret. Sign in with the new one. |
| Messages gone after a redeploy | Expected on free hosting — restore your backup. |
| Changed something and the site looks stale | **Manage app** → **Reboot**. |

---

## Where to read more

- `README.md` — what the tool does and how the writing works
- `DEPLOY-FREE.md` — every free host compared, and the streamlit-vs-docker story
- `ANDROID.md` — running it on the phone with no server at all
