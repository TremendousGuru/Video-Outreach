"""Video Outreach - Streamlit edition.

Same engine as the FastAPI app (crawler, composer, ingest, db) with a Streamlit
UI instead of the hand-written one. This exists because Hugging Face paywalled
Docker Spaces in July 2026, so the Dockerfile route is no longer free - but the
Streamlit SDK still is, and so is Streamlit Community Cloud.

Everything here is UI. The actual work lives in app/crawler.py, app/compose.py,
app/ingest.py and app/db.py, unchanged.

    streamlit run streamlit_app.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import streamlit as st

# ---------------------------------------------------------------------------
# Page config must be the first Streamlit call in the script.
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Video Outreach",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("OUTREACH_HF_UI", "1")


def _bridge_secrets() -> None:
    """Copy Streamlit secrets into the environment so the existing modules pick
    them up unchanged. st.secrets raises if no secrets file exists, hence the
    try/except - a missing file is normal when running locally."""
    keys = (
        "APP_PASSWORD", "OPENAI_API_KEY", "OUTREACH_DB", "OUTREACH_HOSTED", "SPACE_HOST",
        # The endpoint and model matter as much as the key: without these, someone
        # using OpenRouter, Groq or a Gemini compatibility URL would set a secret
        # that the app silently ignored and quietly fall back to templates.
        "OPENAI_BASE_URL", "OPENAI_MODEL",
    )
    try:
        available = dict(st.secrets)
    except Exception:
        available = {}
    for k in keys:
        if available.get(k) and not os.environ.get(k):
            os.environ[k] = str(available[k])
    # A Space is a hosted environment whether or not it says so.
    if os.environ.get("SPACE_HOST"):
        os.environ.setdefault("OUTREACH_HOSTED", "1")


_bridge_secrets()


# ---------------------------------------------------------------------------
# Streamlit Cloud pulls a push into the running container but does not restart
# the process. This main script is re-executed from disk on the next rerun, so
# it is the NEW code - while app/*.py is still the cached module objects from
# the build the process started with. The result is an AttributeError on a
# function that plainly exists in the repo, and it only clears when someone
# presses Reboot. So: re-execute any app module whose file changed after this
# process started. reload() writes into the existing module object, so the
# `from . import db` references held by app/pipeline.py and friends keep
# pointing at the refreshed code.
def _refresh_app_modules() -> list[str]:
    import importlib
    import sys

    import app as pkg

    boot = getattr(pkg, "_boot_time", None)
    if boot is None:
        boot = pkg._boot_time = time.time()

    stale = []
    for name, mod in list(sys.modules.items()):
        if not name.startswith("app."):
            continue
        path = getattr(mod, "__file__", "") or ""
        if not path or not os.path.isfile(path):
            continue
        try:
            # +1s of slack: a fresh clone is written a moment before the process
            # starts, and this must not read as "changed since boot".
            if os.path.getmtime(path) > boot + 1:
                stale.append(name)
        except OSError:
            continue

    for name in stale:
        try:
            importlib.reload(sys.modules[name])
        except Exception:  # noqa: BLE001 - a stale module beats no module
            pass
    return stale


_refresh_app_modules()

from app import compose as composer  # noqa: E402
from app import crawler, db, ingest  # noqa: E402
from app.cli import build_outbox_html, gmail_url, mailto_url  # noqa: E402

# Shown in the sidebar so "is the new build actually running?" has an answer
# you can check on a phone instead of guessing from behaviour.
BUILD = "2026-10-05 · opened-draft sections"


# Every one of these speaks the OpenAI chat-completions shape, so the only thing
# that changes is the address and the model name. Keeping them here means the
# endpoint is chosen from a list instead of typed.
PROVIDERS: dict[str, dict[str, str]] = {
    "OpenAI": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "Google Gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                      "model": "gemini-3.8-flash"},
    # Groq's free tier retired the Llama models during 2026; these are what it
    # serves free now, at 30 requests/minute and 1,000/day.
    "Groq": {"base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b"},
    "GitHub Models": {"base_url": "https://models.github.ai/inference",
                      "model": "openai/gpt-4o-mini"},
    "OpenRouter": {"base_url": "https://openrouter.ai/api/v1",
                   "model": "qwen/qwen3.8-27b:free"},
    "Together": {"base_url": "https://api.together.xyz/v1",
                 "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo"},
    "Custom": {"base_url": "", "model": ""},
}

STATUS_LABEL = {
    "pending": "queued", "crawling": "crawling", "crawled": "crawled",
    "composing": "writing", "ready": "ready", "sent": "sent", "failed": "failed",
}

# Review & send is a list of sections, not one long list. A store whose draft you
# already opened must not look identical to one you have never touched - that is
# the whole "did I already email this one?" problem. Order is the order they are
# rendered in.
REVIEW_SECTIONS = (
    ("to_send", "📤 To send", "Message written, draft never opened."),
    ("opened", "👁 Opened", "You tapped these already. Check whether the send actually went."),
    ("sent", "✅ Sent", "Confirmed sent."),
    ("failed", "❌ Failed", "Crawl or writing did not finish."),
    ("working", "⏳ In progress", "Queued, crawling or being written."),
)

# The two "open the draft" links are plain anchors rather than st.link_button,
# because a link button's click never reaches Python - and if Python cannot see
# the click, it cannot move the row out of the way. The anchor carries the lead
# id, and a tiny script (see _OPEN_TRACKER_JS) reports the click back.
_MAIL_CSS = """<style>
a.mailopen{display:flex;align-items:center;justify-content:center;gap:.3rem;width:100%;
  height:2.5rem;padding:0 .5rem;border-radius:.5rem;text-decoration:none;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;font-size:.875rem;font-weight:600;
  background:__PRIMARY__;color:#fff}
a.mailopen.secondary{background:transparent;color:inherit;font-weight:400;
  border:1px solid rgba(151,166,195,.45)}
a.mailopen:hover{filter:brightness(1.12)}
/* The tracker's checkboxes are plumbing, not controls. */
div[data-testid="stCheckbox"][aria-label^="__open_"]{display:none}
</style>"""

# Runs in a zero-height component iframe. Streamlit sandboxes those with
# allow-scripts + allow-same-origin, so the script can reach the page's own DOM;
# clicking a hidden Streamlit checkbox is how the click gets back to Python.
# If any of it fails, nothing breaks: the anchor still opens the mail app, and
# the "⋯" menu has a manual "Mark as opened" as a fallback.
_OPEN_TRACKER_JS = """<script>
(function () {
  function doc() {
    try { return window.parent.document; } catch (e) { return null; }
  }
  function report(id) {
    var d = doc();
    if (!d) return;
    var input = d.querySelector(
      'div[data-testid="stCheckbox"][aria-label="__open_' + id + '__"] input');
    if (input && !input.checked) input.click();
  }
  function arm() {
    var d = doc();
    if (!d) return;
    var links = d.querySelectorAll('a[data-lead-open]');
    for (var i = 0; i < links.length; i++) {
      var a = links[i];
      if (a.getAttribute('data-armed')) continue;
      a.setAttribute('data-armed', '1');
      (function (a) {
        a.addEventListener('click', function () {
          try { report(a.getAttribute('data-lead-open')); } catch (e) {}
        });
      })(a);
    }
  }
  try {
    arm();
    var timer = null;
    new MutationObserver(function () {
      if (timer) return;
      timer = setTimeout(function () { timer = null; arm(); }, 200);
    }).observe(doc().body, { childList: true, subtree: true });
  } catch (e) {}
})();
</script>"""


# ---------------------------------------------------------------------------
# Auth. Streamlit keeps this in session_state rather than a cookie, which
# sidesteps the blocked-cookie problem that embedded frames cause.
# ---------------------------------------------------------------------------
def _pw_token(password: str) -> str:
    """A fingerprint of the current password.

    Stored in the session instead of a bare "signed in" flag so that changing
    APP_PASSWORD invalidates every existing session on its next interaction -
    the same guarantee the FastAPI edition gets from signing its cookie with the
    password. Without this, a tab that was already open would stay signed in
    even after you rotated the password.
    """
    import hashlib

    return hashlib.sha256(password.encode()).hexdigest()


def require_login() -> bool:
    password = (os.environ.get("APP_PASSWORD") or "").strip()
    if not password:
        return True

    token = _pw_token(password)
    if st.session_state.get("auth_token") == token:
        return True
    if st.session_state.get("auth_token"):
        # The password changed under us - drop the stale session and re-ask.
        st.session_state.auth_token = None
        st.warning("The password was changed, so you were signed out. Sign in again.")

    st.markdown("## Video Outreach")
    st.caption("Enter the app password to continue.")
    with st.form("login"):
        entered = st.text_input("Password", type="password", autocomplete="current-password")
        if st.form_submit_button("Sign in", use_container_width=True):
            import hmac

            if hmac.compare_digest(entered.encode(), password.encode()):
                st.session_state.auth_token = token
                st.rerun()
            else:
                st.error("Wrong password.")
    return False


# ---------------------------------------------------------------------------
# Async bridge. Streamlit runs this script in its own thread, so a fresh loop
# per call is the reliable pattern.
# ---------------------------------------------------------------------------
def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()


async def _process_one(lead_row: dict, fetcher: crawler.Fetcher, settings: dict) -> dict:
    """Crawl + compose one store. Returns data only - never touches the UI, so
    the caller can render results one at a time without concurrency problems."""
    out = {
        "id": lead_row["id"],
        "email": lead_row.get("email") or "",
        "domain": lead_row.get("domain") or "",
        "store_name": lead_row.get("store_name") or "",
        "status": "failed", "error": "", "engine": "", "subject": "",
        "subjects": [], "body": "", "hook": "", "note": "", "flags": [],
    }
    try:
        result = await crawler.crawl_store(fetcher, lead_row, settings)
        if not result["ok"]:
            db.update_lead(lead_row["id"], status="failed", error=result["error"],
                           flags=result.get("flags") or [])
            out["error"] = result["error"]
            return out

        facts = result["facts"]
        facts["signal_phrases"] = crawler.signal_phrases(facts.get("signals") or [])
        facts["strong_signals"] = crawler.strong_signal_phrases(facts.get("signals") or [])

        patch = {"facts": facts, "flags": result["flags"]}
        if result.get("store_name_found"):
            patch["store_name"] = result["store_name_found"]
            out["store_name"] = result["store_name_found"]
        if result.get("email_found") and not out["email"]:
            patch["email"] = result["email_found"]
            out["email"] = result["email_found"]

        if not settings.get("auto_compose", True):
            db.update_lead(lead_row["id"], status="crawled", **patch)
            out["status"] = "crawled"
            out["note"] = result.get("note", "")
            return out

        composed = await composer.compose(
            facts, settings, store_hint=facts.get("store_name") or out["store_name"] or "there"
        )
        subjects = composed.get("subjects") or [composer.local_subject_for(facts, settings)]
        body = (composed.get("body") or "").strip()
        if not body:
            db.update_lead(lead_row["id"], status="failed", error="composer returned nothing")
            out["error"] = "composer returned nothing"
            return out

        db.update_lead(
            lead_row["id"], status="ready", subjects=subjects, subject=subjects[0], body=body,
            engine=composed.get("engine", ""), notes=(composed.get("hook") or "")[:300], **patch,
        )
        out.update(status="ready", subjects=subjects, subject=subjects[0], body=body,
                   engine=composed.get("engine", ""), hook=composed.get("hook", ""),
                   note=result.get("note", ""))
        if composed.get("fallback_reason"):
            out["note"] = f"AI failed, used templates: {composed['fallback_reason'][:120]}"
    except Exception as e:  # noqa: BLE001 - one bad store must not kill the batch
        db.update_lead(lead_row["id"], status="failed", error=f"{type(e).__name__}: {e}"[:200])
        out["error"] = f"{type(e).__name__}: {e}"[:200]
    return out


async def crawl_batch(leads: list[dict], settings: dict, concurrency: int = 3):
    """Async generator: yields one result dict per store as it finishes."""
    import httpx

    limits = httpx.Limits(max_connections=max(2, concurrency * 2))
    async with httpx.AsyncClient(
        headers=dict(crawler.HEADERS), limits=limits, follow_redirects=True,
        verify=False, timeout=httpx.Timeout(30.0, connect=15.0),
    ) as client:
        fetcher = crawler.Fetcher(
            client,
            delay=float(settings.get("request_delay", 0.5) or 0),
            respect_robots=bool(settings.get("respect_robots", True)),
        )
        sem = asyncio.Semaphore(max(1, concurrency))

        async def guarded(lead):
            async with sem:
                return await _process_one(lead, fetcher, settings)

        tasks = [asyncio.create_task(guarded(l)) for l in leads]
        for finished in asyncio.as_completed(tasks):
            yield await finished


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def leads_as_rows() -> list[dict]:
    return [db.get_lead(l["id"], full=True) for l in db.list_leads()]


def lead_to_outbox_entry(lead: dict) -> dict:
    facts = lead.get("facts") or {}
    return {
        "email": lead.get("email", ""), "domain": lead.get("domain", ""),
        "store_name": lead.get("store_name", ""), "status": lead.get("status", ""),
        "error": lead.get("error", ""), "engine": lead.get("engine", ""),
        "hook": lead.get("notes", ""), "subjects": lead.get("subjects", []),
        "subject": lead.get("subject", ""), "body": lead.get("body", ""),
        "pages": [
            {"label": p.get("label", ""), "url": p.get("url", ""), "status": p.get("status", 0)}
            for p in facts.get("pages_crawled", [])
        ],
        "found": {
            "founder": facts.get("founder_name", ""), "location": facts.get("location", ""),
            "price_range": facts.get("price_range", ""), "signals": facts.get("signals", []),
            "products": [p.get("title", "") for p in (facts.get("products") or [])[:5]],
        },
        "flags": lead.get("flags", []),
    }


def mark_sent(lead_id: int) -> None:
    db.update_lead(lead_id, status="sent",
                   sent_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))


# ---------------------------------------------------------------------------
# Sidebar: settings
# ---------------------------------------------------------------------------
def _list_models(settings: dict) -> list[str]:
    """Ask the endpoint which models this key can actually use.

    Worth the extra call: model names differ per provider and change often, so
    guessing produces a 404 that looks like a broken key. Asking the API turns
    "wrong model name" into a fixable list.
    """
    base = (settings.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    key = (settings.get("api_key") or "").strip()
    if not key:
        return []

    async def fetch() -> list[str]:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{base}/models",
                                 headers={"Authorization": f"Bearer {key}"})
            if r.status_code >= 400:
                return []
            data = r.json().get("data") or []
        ids = [str(m.get("id", "")) for m in data if isinstance(m, dict)]
        return sorted(i for i in ids if i)

    try:
        return run_async(fetch())
    except Exception:  # noqa: BLE001 - this is a helpful extra, never fatal
        return []


def _test_key(settings: dict) -> None:
    """Make one tiny call so a bad key is discovered here, not mid-crawl.

    compose_ai() raises with the API's own message ("API 401: Incorrect API
    key", "API 404: model not found"), which is the part worth showing - it says
    which of the three fields to fix.
    """
    probe = {
        "store_name": "Test Store",
        "domain": "test-store.example",
        "platform": "Shopify",
        "product_types": ["candles"],
        "products": [{"title": "Vanilla Candle", "price": "$24"}],
    }
    with st.spinner("Asking the model for one short message..."):
        try:
            result = run_async(composer.compose_ai(probe, settings, "Test Store"))
        except Exception as e:  # noqa: BLE001 - the message IS the useful part
            st.error(f"The model call failed: {e}")
            # Classify by wording, not just status code: Gemini answers a bad key
            # with HTTP 400 "Please pass a valid API key", not the 401 OpenAI uses.
            text = str(e).lower()
            # Google also flags whole projects ("Your project has been denied
            # access") while the key itself is fine. Saying "check your key" there
            # sends people chasing the one thing that is not wrong.
            project_denied = "denied access" in text or (
                "permission_denied" in text and "project" in text
            )
            auth_problem = any(
                s in text for s in (
                    "401", "403", "valid api key", "api key not valid", "incorrect api key",
                    "invalid api key", "unauthorized", "invalid_argument", "permission_denied",
                    "authentication",
                )
            )
            if project_denied:
                st.warning(
                    "**The key is fine - Google has flagged the project, not the key.** "
                    "This exact error has been hitting many Google accounts since 2026, "
                    "including brand-new ones, and it affects the whole account rather "
                    "than the key: a fresh key or a new project under the same account "
                    "usually fails the same way."
                )
                st.caption(
                    "Your options: request a manual review at "
                    "discuss.ai.google.dev (slow, no guarantee), or switch to another "
                    "free provider - the app works with any of them. See the "
                    "**If Google denies your project** section in NEXT-STEPS.md. "
                    "Meanwhile the app still works: stores fall back to template "
                    "messages, and the crawl log says so."
                )
            elif auth_problem:
                st.caption("The endpoint rejected the key. Check `OPENAI_API_KEY` in "
                           "this app's secrets - and that the key belongs to the "
                           "provider selected above.")
            else:
                # Only claim a model-name problem when the error actually says so.
                # "model returned no body" contains the word "model" but has
                # nothing to do with the model's name, and answering it with a
                # list of models the key can already see (the model being used
                # among them) is worse than saying nothing.
                model_problem = any(s in text for s in (
                    "does not exist", "not found", "no such model", "unknown model",
                    "model_not_found", "decommissioned", "deprecated",
                    "unsupported model", "model not supported",
                ))
                budget_problem = any(s in text for s in (
                    "token budget", "token limit", "empty message", "finish_reason",
                ))
                if budget_problem:
                    st.warning(
                        "**The key and the model name are both fine - the model ran out "
                        "of tokens while thinking.** Reasoning models such as Groq's "
                        "`gpt-oss` generate their reasoning as tokens and spend them from "
                        "the same budget as the message, so a small limit can be used up "
                        "before a single word of the email is written."
                    )
                    st.caption(
                        "Already raised to 3000 tokens with reasoning set to low and hidden. "
                        "If you still see this, choose a non-reasoning model - or raise the "
                        "budget in `app/compose.py`."
                    )
                elif model_problem:
                    with st.spinner("Asking the endpoint what models it offers..."):
                        models = _list_models(settings)
                    if models:
                        st.warning(f"**The key works, but the endpoint does not recognise "
                                   f"the model “{settings.get('model')}”.** "
                                   "These are what it offers:")
                        st.code("\n".join(models[:40]), language=None)
                    else:
                        st.caption("The model name was rejected and the endpoint would not "
                                   "list its models. Check the Model field and the Base URL.")
                else:
                    st.caption(
                        "The request reached the provider, so the key is fine, but the "
                        "reply could not be used. The message above is the provider's own "
                        "wording. Nothing else in the app is affected: stores fall back to "
                        "template messages and the crawl log says which ones."
                    )
        else:
            subject = (result.get("subjects") or ["(no subject)"])[0]
            st.success(f"Working - {result.get('engine')}")
            st.caption(f"Sample subject it wrote: “{subject}”")


def sidebar_settings() -> dict:
    settings = db.load_settings()
    st.sidebar.markdown("### Your message")
    with st.sidebar:
        sender = st.text_input("Your name", value=settings.get("sender_name", ""),
                               help="Used in the sign-off.")
        offer = st.text_input("What you do", value=settings.get("offer", ""))
        video_line = st.text_input("How you pitch the video", value=settings.get("video_line", ""))
        cta = st.text_input("Call to action", value=settings.get("cta", ""))
        subject_hint = st.text_input("Subject line idea", value=settings.get("subject_hint", ""))

        st.markdown("### Crawler")
        concurrency = st.slider("Stores at once", 1, 8, int(settings.get("concurrency", 3) or 3))
        limit_hint = st.caption("On a free host, 2-3 is safer.")

        st.markdown("### Writing")
        use_ai = st.checkbox("Use the AI model when a key is set",
                             value=bool(settings.get("use_ai", True)))

        # Picking a provider by name keeps the endpoint out of the user's hands -
        # the usual mistake is a URL missing its /openai suffix, which returns a
        # 404 that looks like a bad key.
        current_url = (settings.get("base_url") or "").rstrip("/")
        labels = list(PROVIDERS)
        known = [l for l in labels if PROVIDERS[l]["base_url"]
                 and PROVIDERS[l]["base_url"].rstrip("/") == current_url]
        picked = st.selectbox("Provider", labels,
                              index=labels.index(known[0]) if known else labels.index("Custom"),
                              disabled=not use_ai,
                              help="Where your key comes from. Gemini works through "
                                   "Google's OpenAI-compatible endpoint.")
        if use_ai and PROVIDERS[picked]["base_url"] and picked != (known[0] if known else "Custom"):
            db.save_settings({"base_url": PROVIDERS[picked]["base_url"],
                              "model": PROVIDERS[picked]["model"]})
            st.rerun()

        model = st.text_input("Model", value=settings.get("model", "gpt-4o-mini"),
                              disabled=not use_ai)

        endpoint = (settings.get("base_url") or "").strip()
        # The key is read-only here: it comes from this app's secrets (or the
        # environment) and there is no field, anywhere in the UI, that writes one.
        # This block reports presence only.
        _key = db.env_api_key()
        if _key:
            st.success(f"Key found: {db.mask_key(_key)} — from this app's secrets. "
                       "Read-only here.", icon="🔑")
        else:
            st.info("No key found. Add `OPENAI_API_KEY` in this app's **Secrets** "
                    "(or the environment) and restart. Templates are used until then.",
                    icon="✍️")
        st.caption(f"Requests go to {endpoint or '(no endpoint set)'} · keys cannot be "
                   "entered, viewed or changed from this interface.")
        st.caption(f"build {BUILD}")
        if _key and st.button(
            "Test the key", use_container_width=True,
            help="Sends one small request, so you find out now instead of halfway "
                 "through a crawl.",
        ):
            # Save the model before testing. Otherwise you test the value you just
            # replaced - and the crawl keeps using it too, which looks exactly like
            # the new model being ignored.
            db.save_settings({"model": model, "base_url": settings.get("base_url", "")})
            _test_key(db.load_settings())

        st.markdown("### Safety")
        optout = st.checkbox("Add an opt-out line", value=bool(settings.get("optout", True)))
        robots = st.checkbox("Respect robots.txt", value=bool(settings.get("respect_robots", True)))

        if st.button("Save settings", use_container_width=True):
            db.save_settings({
                "sender_name": sender, "offer": offer, "video_line": video_line,
                "cta": cta, "subject_hint": subject_hint, "concurrency": concurrency,
                "use_ai": use_ai, "model": model, "optout": optout,
                "respect_robots": robots,
            })
            st.success("Saved")

    settings = db.load_settings()  # re-read so a save takes effect immediately
    return settings


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
def tab_add_list() -> None:
    st.markdown("#### Add your list")
    st.caption("A CSV, Excel or text file. Each row needs an email address, a store "
               "domain, or both. Column order doesn't matter and headers are optional.")

    col1, col2 = st.columns([1, 1])
    with col1:
        uploaded = st.file_uploader("Upload a file", type=["csv", "txt", "xlsx", "xlsm", "tsv"])
        if uploaded is not None:
            if st.button("Add these rows", type="primary", key="add_file"):
                try:
                    rows = ingest.parse_file(uploaded.name, uploaded.getvalue())
                except Exception as e:  # noqa: BLE001
                    st.error(f"Could not read that file: {e}")
                else:
                    if not rows:
                        st.warning("No usable rows found. Each row needs an email or a domain.")
                    else:
                        added = db.insert_leads(rows)
                        st.success(f"Added {len(added)} store{'' if len(added) == 1 else 's'}"
                                   f" ({len(rows) - len(added)} duplicates skipped).")
                        st.rerun()
    with col2:
        pasted = st.text_area(
            "Or paste rows here", height=170,
            placeholder="hello@store.com, store.com, Store Name\nstore2.com\nteam@store3.com | store3.com",
        )
        if st.button("Add pasted rows", key="add_paste"):
            rows = ingest.parse_pasted(pasted or "")
            if not rows:
                st.warning("Nothing usable in that text.")
            else:
                added = db.insert_leads(rows)
                st.success(f"Added {len(added)} store{'' if len(added) == 1 else 's'}.")
                st.rerun()


def tab_crawl(settings: dict) -> None:
    leads = db.list_leads()
    if not leads:
        st.info("Add a list first - see the **Add list** tab.")
        return

    todo = [l for l in leads if l["status"] not in ("ready", "sent")]
    st.markdown("#### Crawl & compose")
    st.caption(f"{len(leads)} stores in the list. {len(todo)} still to do.")

    scope = st.radio(
        "Which stores?", ["Not done yet", "Failed only", "Everything (rewrite finished ones)"],
        horizontal=True, label_visibility="collapsed",
    )
    if scope.startswith("Not done"):
        targets = [l for l in leads if l["status"] not in ("ready", "sent")]
    elif scope.startswith("Failed"):
        targets = [l for l in leads if l["status"] == "failed"]
    else:
        targets = list(leads)

    st.caption(f"{len(targets)} selected. Crawling reads each store's homepage, about page, "
               "privacy policy and product data, then writes its message.")

    if st.button("Crawl & compose", type="primary", disabled=not targets, key="go"):
        if not targets:
            return
        rows = [db.get_lead(t["id"], full=True) for t in targets]
        for r in rows:
            db.update_lead(r["id"], status="pending", error="")

        progress = st.progress(0.0, text="Starting...")
        log = st.container()
        ok = failed = 0
        started = time.time()

        async def drain():
            nonlocal ok, failed
            done = 0
            async for res in crawl_batch(rows, settings, concurrency=int(settings.get("concurrency", 3) or 3)):
                done += 1
                name = res["store_name"] or res["domain"] or f"row {res['id']}"
                if res["status"] == "failed":
                    failed += 1
                    log.write(f"❌ **{name}** - {res['error']}")
                else:
                    ok += 1
                    log.write(f"✅ **{name}** - {res['subject']}")
                    # Say so when the AI failed and a template filled in, rather
                    # than letting a generic message look like a model output.
                    if res.get("note"):
                        log.caption(f"⚠️ {name}: {res['note']}")
                progress.progress(done / len(rows), text=f"{done} of {len(rows)} - {name}")

        run_async(drain())
        progress.progress(1.0, text="Done")
        st.success(f"Finished in {round(time.time() - started, 1)}s - "
                   f"{ok} ready, {failed} failed. Open **Review & send**.")
        st.balloons() if ok and not failed else None


def _ago(iso: str) -> str:
    """'3m ago' - the only part of a timestamp worth putting on a card."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = max(0, (datetime.now(timezone.utc) - dt).total_seconds())
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def _state_badge(lead: dict) -> str:
    state = db.state_of(lead)
    if state == "opened":
        return f"opened {_ago(lead.get('opened_at', ''))}".strip()
    if state == "sent":
        return f"sent {_ago(lead.get('sent_at', ''))}".strip()
    if state == "failed":
        return "failed"
    if state == "working":
        return STATUS_LABEL.get(lead.get("status", ""), "queued")
    return "to send"


def _mail_anchor(lead_id: int, url: str, label: str, primary: bool = False) -> str:
    """A link that opens the draft, tagged so the click can be reported back."""
    import html as _html

    cls = "mailopen" + ("" if primary else " secondary")
    target = ' target="_blank" rel="noopener"' if url.startswith("http") else ""
    return (f'<a class="{cls}" href="{_html.escape(url, quote=True)}"{target} '
            f'data-lead-open="{lead_id}">{label}</a>')


def _on_draft_opened(lead_id: int) -> None:
    """Widget callback: the browser just reported a draft was opened.

    Saves the edits at the same time. The link that was tapped was built from
    whatever was on screen, so if you retyped the subject and tapped straight
    away, the stored version would otherwise disagree with what you sent.
    """
    patch: dict = {"opened_at": db.now()}
    subject = st.session_state.get(f"subj_{lead_id}")
    body = st.session_state.get(f"body_{lead_id}")
    if subject is not None:
        patch["subject"] = subject
    if body is not None:
        patch["body"] = body
    db.update_lead(lead_id, **patch)


def _lead_card(lead: dict, settings: dict, expanded: bool = False) -> None:
    lid = lead["id"]
    name = lead.get("store_name") or lead.get("domain") or f"row {lid}"
    with st.expander(f"{name}  ·  {_state_badge(lead)}", expanded=expanded):
        if lead.get("error"):
            st.error(lead["error"])
        if not lead.get("body"):
            st.caption("No message for this store yet.")
            if lead.get("flags"):
                st.caption("Notes: " + " · ".join(lead["flags"]))
            return

        subject = st.text_input("Subject", value=lead.get("subject", ""), key=f"subj_{lid}")
        if lead.get("subjects"):
            st.caption("Other options: " + " | ".join(f"`{s}`" for s in lead["subjects"][1:]))

        body = st.text_area("Message", value=lead.get("body", ""), height=230, key=f"body_{lid}")
        st.caption(f"{len(body.split())} words · {lead.get('engine') or 'template'}"
                   + (f" · hook: {lead.get('notes', '')[:90]}" if lead.get("notes") else ""))

        # Everything lives in one row: the two ways to open the draft, then the
        # three buttons worth keeping at arm's length. The long tail went into
        # "⋯", which is what made these cards three rows tall.
        to = lead.get("email") or ""
        a, b, c, d, e = st.columns([1.2, 1.0, 0.72, 0.98, 0.68])
        if to:
            a.markdown(_mail_anchor(lid, mailto_url(to, subject, body), "✉️ Open draft", primary=True))
            b.markdown(_mail_anchor(lid, gmail_url(to, subject, body), "Gmail web"))
        else:
            a.button("No email", disabled=True, use_container_width=True, key=f"noemail_{lid}")
        if c.button("💾 Save", key=f"save_{lid}", use_container_width=True,
                    help="Save the subject and body you just edited."):
            db.update_lead(lid, subject=subject, body=body)
            st.success("Saved")
        if d.button("🔁 Rewrite", key=f"re_{lid}", use_container_width=True,
                    help="Write this one again from the crawled facts."):
            facts = lead.get("facts") or {}
            facts["signal_phrases"] = crawler.signal_phrases(facts.get("signals") or [])
            facts["strong_signals"] = crawler.strong_signal_phrases(facts.get("signals") or [])
            with st.spinner("Rewriting..."):
                composed = run_async(composer.compose(
                    facts, settings,
                    store_hint=facts.get("store_name") or lead.get("store_name") or "there"))
            subs = composed.get("subjects") or [composer.local_subject_for(facts, settings)]
            db.update_lead(lid, subject=subs[0], body=composed.get("body", ""),
                           subjects=subs, engine=composed.get("engine", ""),
                           notes=(composed.get("hook") or "")[:300])
            st.rerun()
        if e.button("✅ Sent", key=f"sent_{lid}", use_container_width=True,
                    help="Confirm you pressed send. Moves the row to Sent."):
            mark_sent(lid)
            st.rerun()

        more, _spacer = st.columns([1, 5])
        with more:
            with st.popover("⋯"):
                st.markdown("**Copy**")
                st.code(f"Subject: {subject}\n\n{body}", language=None)
                if lead.get("facts"):
                    st.markdown("**What we found**")
                    facts = lead["facts"]
                    for k, v in (
                        ("Store", facts.get("store_name")), ("Tagline", facts.get("tagline")),
                        ("Location", facts.get("location")), ("Founded", facts.get("founded_year")),
                        ("Founder", facts.get("founder_name")),
                        ("Price range", facts.get("price_range")),
                        ("Signals", ", ".join(facts.get("signals") or [])),
                        ("Products", " · ".join(
                            pr.get("title", "") for pr in (facts.get("products") or [])[:6])),
                    ):
                        if v:
                            st.markdown(f"- **{k}:** {v}")
                    st.markdown("**Pages read**")
                    for pg in (lead["facts"].get("pages_crawled") or []):
                        st.markdown(f"- `{pg.get('status')}` {pg.get('label')} — {pg.get('url')}")
                if db.state_of(lead) == "opened":
                    if st.button("↩ Back to To send", key=f"back_{lid}", use_container_width=True,
                                 help="Undo: forgets that the draft was opened."):
                        db.unmark_opened(lid)
                        st.rerun()
                elif to:
                    if st.button("Mark as opened", key=f"opened_{lid}", use_container_width=True,
                                 help="Only needed if your browser blocked the automatic "
                                      "move after you opened a draft."):
                        db.mark_opened(lid)
                        st.rerun()

        # Plumbing, not a control: the tracker script clicks this on your behalf
        # so Python learns which row was opened. Hidden by CSS.
        if to:
            st.checkbox(f"__open_{lid}__", key=f"openchk_{lid}", label_visibility="collapsed",
                        on_change=_on_draft_opened, args=(lid,))


def tab_review(settings: dict) -> None:
    leads = leads_as_rows()
    if not leads:
        st.info("Nothing yet. Add a list, then crawl it.")
        return

    st.markdown(
        _MAIL_CSS.replace("__PRIMARY__", str(st.get_option("theme.primaryColor") or "#ff4b4b")),
        unsafe_allow_html=True,
    )

    groups: dict[str, list[dict]] = {k: [] for k, _t, _n in REVIEW_SECTIONS}
    for l in leads:
        groups[db.state_of(l)].append(l)
    written = groups["to_send"] + groups["opened"] + groups["sent"]

    cols = st.columns(4)
    cols[0].metric("To send", len(groups["to_send"]))
    cols[1].metric("Opened", len(groups["opened"]),
                   help="Draft opened, send never confirmed.")
    cols[2].metric("Sent", len(groups["sent"]))
    cols[3].metric("Failed", len(groups["failed"]))

    if written:
        d1, d2 = st.columns(2)
        d1.download_button(
            "⬇︎ Tappable outbox page",
            data=build_outbox_html([lead_to_outbox_entry(l) for l in written], settings),
            file_name="outbox.html", mime="text/html", use_container_width=True,
            help="A page where every store is a card with an 'Open draft' button. "
                 "Easiest way to send from a phone - open it in Chrome.",
        )
        d2.download_button(
            "⬇︎ CSV of all messages",
            data=export_csv(written), file_name="messages.csv", mime="text/csv",
            use_container_width=True,
        )

    st.divider()
    st.caption("Opening a draft moves that store into **Opened** by itself, so the "
               "list always shows what is left.")

    for key, title, note in REVIEW_SECTIONS:
        rows = groups[key]
        if not rows:
            continue
        st.markdown(f"#### {title} &nbsp;`{len(rows)}`", unsafe_allow_html=True)
        if note:
            st.caption(note)
        for lead in rows:
            _lead_card(lead, settings,
                       expanded=(key == "to_send" and len(rows) == 1))
        st.markdown("&nbsp;", unsafe_allow_html=True)

    # Rendered last, so every anchor above already exists when it starts looking.
    # st.iframe is the supported replacement for components.v1.html (which is
    # past its removal date); it embeds the HTML string in an iframe that keeps
    # JavaScript and same-origin access, which is what the tracker needs.
    st.iframe(_OPEN_TRACKER_JS, height=1)


def export_csv(leads: list[dict]) -> str:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["email", "store_name", "domain", "subject", "body", "status", "engine", "hook"])
    for l in leads:
        w.writerow([
            l.get("email", ""), l.get("store_name", ""), l.get("domain", ""),
            l.get("subject", ""), (l.get("body") or "").replace("\n", "\\n"),
            l.get("status", ""), l.get("engine", ""), l.get("notes", ""),
        ])
    return buf.getvalue()


def tab_data(settings: dict) -> None:
    st.markdown("#### Backup and restore")
    st.caption("Free hosts have no persistent disk. Every rebuild wipes the database, "
               "so download a backup before you redeploy and restore it afterwards.")

    payload = db.export_all()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    c1, c2 = st.columns(2)
    c1.download_button(
        f"⬇︎ Backup everything ({payload['lead_count']} stores)",
        data=json.dumps(payload, indent=1, ensure_ascii=False),
        file_name=f"outreach-backup-{stamp}.json", mime="application/json",
        use_container_width=True,
        help="Leads, messages and settings in one file. Your API key is not included.",
    )
    with c2:
        restore = st.file_uploader("Restore from a backup", type=["json"], key="restore",
                                   label_visibility="collapsed")
        if restore is not None:
            mode = st.radio("Restore mode", ["Replace everything", "Merge (add only new)"],
                            horizontal=True, key="rmode")
            if st.button("Restore now"):
                try:
                    result = db.import_all(json.loads(restore.getvalue().decode()),
                                           mode="merge" if mode.startswith("Merge") else "replace")
                except Exception as e:  # noqa: BLE001
                    st.error(str(e))
                else:
                    st.success(f"Restored {result['added']} stores "
                               f"({result['skipped']} skipped).")
                    st.rerun()

    st.divider()
    st.markdown("#### Danger zone")
    if st.checkbox("I want to delete every row in the list"):
        if st.button("Delete everything", type="primary"):
            db.clear_leads()
            st.success("Cleared.")
            st.rerun()

    with st.expander("Where is the database?"):
        st.code(str(db.DB_PATH), language=None)
        st.caption("On a free host this path is wiped on rebuild. Use Backup before "
                   "every redeploy - that is what it is for.")
        if os.environ.get("OUTREACH_HOSTED") and not str(db.DB_PATH).startswith(
            ("/var/data", "/data", "/mnt")
        ):
            st.warning("This looks like ephemeral storage. Your data will not survive a rebuild.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    if not require_login():
        return

    st.markdown("## 🎬 Video Outreach")
    st.caption("Upload a list of Shopify stores → it reads each one → you get a message "
               "that could only be written for that store owner → tap send.")

    if not (os.environ.get("APP_PASSWORD") or "").strip():
        st.warning("**No password is set.** Anyone who finds this URL can use it and read "
                   "your lead list. Add `APP_PASSWORD` in this app's secrets.")

    settings = sidebar_settings()
    if st.sidebar.button("Sign out", use_container_width=True):
        st.session_state.auth_token = None
        st.rerun()

    tab1, tab2, tab3, tab4 = st.tabs(["1 · Add list", "2 · Crawl & compose",
                                      "3 · Review & send", "4 · Backup"])
    with tab1:
        tab_add_list()
    with tab2:
        tab_crawl(settings)
    with tab3:
        tab_review(settings)
    with tab4:
        tab_data(settings)


main()
