"""Command-line mode: crawl, compose, and write a tappable outbox page.

Built for phones and locked-down environments - no web server, no FastAPI, no
uvicorn. Only needs httpx + beautifulsoup4 (+ openpyxl for Excel files).

    python3 -m app.cli leads.csv --limit 5
    python3 -m app.cli leads.csv --no-ai

Outputs:
    outbox.html    tap a store, Gmail opens with subject + body pre-filled
    messages.csv   the same data in a spreadsheet you can send from anywhere
    outbox.json    run state - re-running skips stores already finished
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import html as html_mod
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

# --- friendly dependency check before anything else imports them -------------
_MISSING = []
for mod, pkg in (("httpx", "httpx"), ("bs4", "beautifulsoup4")):
    try:
        __import__(mod)
    except ImportError:
        _MISSING.append(pkg)
if _MISSING:
    sys.exit(
        "Missing packages: " + ", ".join(_MISSING) + "\n\n"
        "In Pydroid 3, open the menu (three lines, top left) -> Pip, and install:\n"
        "    httpx\n"
        "    beautifulsoup4\n"
        "    openpyxl        (only if your list is .xlsx)\n"
    )

# Pydroid 3's "Run" button executes this FILE, not the module, so relative
# imports would blow up. Support both ways of starting it.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app import compose as composer  # type: ignore[no-redef]
    from app import crawler, db, ingest  # type: ignore[no-redef]
else:
    from . import compose as composer  # noqa: E402
    from . import crawler, db, ingest  # noqa: E402


# ---------------------------------------------------------------- lead loading

def load_leads(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"No such file: {path}")
    try:
        rows = ingest.parse_file(path.name, path.read_bytes())
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Could not read {path.name}: {e}")
    if not rows:
        sys.exit(
            f"No usable rows in {path.name}.\n"
            "Each line needs an email address and/or a store domain, e.g.\n"
            "    hello@store.com, store.com, Store Name\n"
            "    store.com"
        )
    return rows


# ------------------------------------------------------------------ run state

def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(path: Path, state: dict) -> None:
    """Written after every store so a killed run loses nothing."""
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def lead_key(lead: dict) -> str:
    return (lead.get("domain") or "").strip().lower() or (lead.get("email") or "").strip().lower()


# ---------------------------------------------------------------------- crawl

async def crawl_all(leads: list[dict], settings: dict, args, state: dict, state_path: Path) -> None:
    import httpx

    todo = []
    for lead in leads:
        key = lead_key(lead)
        if not key:
            continue
        done = state.get(key, {}).get("status") == "ready"
        if done and not args.redo:
            print(f"  skip  {key}  (already done - use --redo to rewrite)", flush=True)
            continue
        todo.append(lead)

    if not todo:
        print("  nothing left to crawl", flush=True)
        return

    print(f"  crawling {len(todo)} store(s), {args.concurrency} at a time\n", flush=True)

    limits = httpx.Limits(max_connections=max(2, args.concurrency * 2))
    timeout = httpx.Timeout(30.0, connect=15.0)
    sem = asyncio.Semaphore(args.concurrency)
    counter = {"n": 0, "start": time.time()}

    async with httpx.AsyncClient(
        headers=dict(crawler.HEADERS), limits=limits, follow_redirects=True,
        verify=False, timeout=timeout,
    ) as client:
        fetcher = crawler.Fetcher(
            client,
            delay=float(settings.get("request_delay", 0.5) or 0),
            respect_robots=bool(settings.get("respect_robots", True)),
        )

        async def one(lead: dict) -> None:
            key = lead_key(lead)
            async with sem:
                counter["n"] += 1
                n = counter["n"]
                label = lead.get("store_name") or key
                print(f"  [{n}/{len(todo)}] {label} - crawling...", flush=True)
                entry: dict = {
                    "email": lead.get("email", ""), "domain": lead.get("domain", ""),
                    "store_name": lead.get("store_name", ""), "status": "crawling",
                    "flags": list(lead.get("flags") or []), "engine": "", "hook": "",
                    "subjects": [], "subject": "", "body": "", "error": "",
                    "pages": [], "found": {},
                }
                try:
                    result = await crawler.crawl_store(fetcher, lead, settings)
                    if not result["ok"]:
                        entry.update(status="failed", error=result["error"])
                        state[key] = entry
                        save_state(state_path, state)
                        print(f"  [{n}/{len(todo)}] {label} - FAILED: {result['error']}", flush=True)
                        return

                    facts = result["facts"]
                    facts["signal_phrases"] = crawler.signal_phrases(facts.get("signals") or [])
                    facts["strong_signals"] = crawler.strong_signal_phrases(facts.get("signals") or [])
                    entry["store_name"] = result.get("store_name_found") or entry["store_name"] or key
                    if result.get("email_found") and not entry["email"]:
                        entry["email"] = result["email_found"]
                    entry["flags"] = result["flags"]
                    entry["pages"] = [
                        {"label": p.get("label", ""), "url": p.get("url", ""), "status": p.get("status", 0)}
                        for p in facts.get("pages_crawled", [])
                    ]
                    entry["found"] = {
                        "founder": facts.get("founder_name", ""),
                        "location": facts.get("location", ""),
                        "price_range": facts.get("price_range", ""),
                        "signals": facts.get("signals", []),
                        "products": [p.get("title", "") for p in (facts.get("products") or [])[:5]],
                    }

                    print(f"  [{n}/{len(todo)}] {label} - writing...", flush=True)
                    out = await composer.compose(
                        facts, settings, store_hint=facts.get("store_name") or entry["store_name"] or "there"
                    )
                    subjects = out.get("subjects") or [composer.local_subject_for(facts, settings)]
                    entry.update(
                        status="ready", engine=out.get("engine", ""),
                        subjects=subjects, subject=subjects[0],
                        body=out.get("body", ""), hook=out.get("hook", ""),
                    )
                except Exception as e:  # noqa: BLE001
                    entry.update(status="failed", error=f"{type(e).__name__}: {e}"[:200])

                state[key] = entry
                save_state(state_path, state)
                if entry["status"] == "ready":
                    print(
                        f"  [{n}/{len(todo)}] {label} - ready  ({entry['engine'] or 'template'})\n"
                        f"        {entry['subject']}",
                        flush=True,
                    )
                else:
                    print(f"  [{n}/{len(todo)}] {label} - {entry['status']}: {entry['error']}", flush=True)

        await asyncio.gather(*(one(l) for l in todo))

    print(f"\n  finished in {round(time.time() - counter['start'], 1)}s", flush=True)


# -------------------------------------------------------------------- outputs

def mailto_url(to: str, subject: str, body: str) -> str:
    return f"mailto:{quote(to)}?subject={quote(subject)}&body={quote(body)}"


def gmail_url(to: str, subject: str, body: str) -> str:
    return (
        "https://mail.google.com/mail/?view=cm&fs=1&tf=1"
        f"&to={quote(to)}&su={quote(subject)}&body={quote(body)}"
    )


def build_outbox_html(entries: list[dict], settings: dict) -> str:
    e = html_mod.escape
    ready = [x for x in entries if x.get("status") == "ready"]
    failed = [x for x in entries if x.get("status") == "failed"]
    sender = settings.get("sender_name") or ""

    cards = []
    for i, item in enumerate(ready, 1):
        to = item.get("email", "")
        subject = item.get("subject", "")
        body = item.get("body", "")
        if not to:
            action = (
                '<div class="warn">No email address found for this store. Add one to '
                'your list and re-run, then this button will work.</div>'
                '<button class="btn ghost" disabled>No address</button>'
            )
        else:
            action = (
                f'<a class="btn" href="{e(mailto_url(to, subject, body))}" data-open="{i}">Open in mail app</a>'
                f'<a class="btn ghost" href="{e(gmail_url(to, subject, body))}" target="_blank" '
                f'rel="noopener" data-open="{i}">Gmail web</a>'
            )
        alternates = "".join(
            f'<li>{e(s)}</li>' for s in (item.get("subjects") or [])[1:]
        )
        sources = " · ".join(
            f'{e(p["label"])}' for p in (item.get("pages") or [])
        )
        first = item.get("found", {}) or {}
        details = []
        if first.get("price_range"):
            details.append(f'price range {e(first["price_range"])}')
        if first.get("founder"):
            details.append(f'founder {e(first["founder"])}')
        if first.get("location"):
            details.append(e(first["location"]))
        if first.get("signals"):
            details.append(e(", ".join(first["signals"][:4])))

        cards.append(f"""
    <article class="card" id="c{i}">
      <div class="ribbon">Opened already &mdash; check before emailing again</div>
      <header>
        <div>
          <h2>{e(item.get("store_name") or item.get("domain") or "store")}</h2>
          <p class="meta">{e(to or "no email address")} &middot; {e(item.get("domain", ""))}</p>
          {'<p class="meta small">' + " &middot; ".join(details) + "</p>" if details else ""}
        </div>
        <span class="tag">{e(item.get("engine") or "template")}</span>
      </header>
      <div class="subject"><span>Subject</span>{e(subject)}</div>
      <pre class="body">{e(body)}</pre>
      <div class="actions">
        {action}
        <button class="btn ghost" data-copy="{i}">Copy</button>
        <button class="btn ghost sent" data-sent="{i}">Sent</button>
      </div>
      {'<details><summary>Other subject options</summary><ul>' + alternates + "</ul></details>" if alternates else ""}
      {'<details><summary>Where this came from</summary><p class="meta small">' + sources + "</p></details>" if sources else ""}
    </article>""")

    failed_html = ""
    if failed:
        rows = "".join(
            f'<li><b>{e(x.get("store_name") or x.get("domain") or "?")}</b> &mdash; {e(x.get("error", ""))}</li>'
            for x in failed
        )
        failed_html = f"""
  <section class="failed">
    <h3>{len(failed)} store(s) could not be finished</h3>
    <ul>{rows}</ul>
    <p class="meta small">Fix the address in your list and re-run - finished stores are skipped,
       only these get retried.</p>
  </section>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Outbox - {len(ready)} ready to send</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
  body {{ margin:0; background:#0b0d10; color:#e6e9ee; font:16px/1.55 -apple-system,Roboto,Helvetica,Arial,sans-serif; }}
  .top {{ position:sticky; top:0; z-index:9; background:rgba(11,13,16,.94); backdrop-filter:blur(8px);
         border-bottom:1px solid #232932; padding:14px 16px; }}
  .top h1 {{ margin:0; font-size:17px; }}
  .top p {{ margin:2px 0 0; color:#8b95a5; font-size:13px; }}
  .wrap {{ max-width:720px; margin:0 auto; padding:16px; }}
  .card {{ background:#12151a; border:1px solid #232932; border-radius:14px; padding:16px; margin-bottom:14px; }}
  .card header {{ display:flex; justify-content:space-between; gap:10px; align-items:flex-start; }}
  .card h2 {{ margin:0; font-size:17px; }}
  .meta {{ margin:2px 0 0; color:#8b95a5; font-size:13px; word-break:break-word; }}
  .meta.small {{ font-size:12px; color:#6f7987; }}
  .tag {{ font:11px/1 ui-monospace,monospace; color:#5eead4; background:#134e4a; border-radius:99px; padding:4px 8px; white-space:nowrap; }}
  .subject {{ margin:12px 0 6px; font-weight:600; }}
  .subject span {{ display:block; font:11px/1 ui-monospace,monospace; color:#8b95a5; letter-spacing:.06em;
                  text-transform:uppercase; margin-bottom:4px; }}
  pre.body {{ background:#0e1116; border:1px solid #1c222b; border-radius:10px; padding:12px;
             white-space:pre-wrap; word-wrap:break-word; font:14.5px/1.6 -apple-system,Roboto,Helvetica,sans-serif;
             margin:0 0 12px; color:#dfe4ea; }}
  .actions {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }}
  @media (min-width:560px) {{ .actions {{ grid-template-columns:repeat(4,minmax(0,1fr)); }} }}
  .btn {{ display:block; background:#5eead4; color:#052e2b; text-decoration:none; font-weight:700;
         padding:13px 8px; border-radius:10px; border:0; font-size:14.5px; cursor:pointer;
         text-align:center; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .btn.ghost {{ background:transparent; color:#e6e9ee; border:1px solid #2e3641; font-weight:500; }}
  .btn[disabled] {{ opacity:.4; }}
  .warn {{ background:#2a1e08; border:1px solid #6b4a12; color:#fbbf24; padding:10px; border-radius:9px;
          font-size:13px; margin-bottom:10px; }}
  details {{ margin-top:10px; }}
  summary {{ color:#8b95a5; font-size:13px; cursor:pointer; }}
  details ul {{ margin:6px 0 0; padding-left:18px; color:#b9c2cf; font-size:14px; }}
  .failed {{ background:#12151a; border:1px solid #3a1e1e; border-radius:14px; padding:16px; }}
  .failed h3 {{ margin:0 0 8px; font-size:15px; color:#fca5a5; }}
  .failed ul {{ margin:0; padding-left:18px; color:#b9c2cf; font-size:14px; }}
  .done {{ opacity:.45; }}
  .done .btn {{ pointer-events:none; }}
  /* A card whose draft was already opened. It moves to its own section at the
     bottom, so "have I emailed this one?" is answered by where the card sits. */
  .card.opened {{ border-color:#6b4a12; opacity:.62; }}
  .card.opened .ribbon {{ display:block; }}
  .ribbon {{ display:none; font:11px/1 ui-monospace,monospace; color:#fbbf24; background:#2a1e08;
            border-radius:99px; padding:5px 9px; margin-bottom:10px; }}
  .sectitle {{ display:flex; align-items:baseline; gap:9px; margin:22px 0 12px; font-size:15px;
              color:#e6e9ee; }}
  .sectitle:first-child {{ margin-top:0; }}
  .sectitle .cnt {{ font:12px/1 ui-monospace,monospace; color:#8b95a5; border:1px solid #2e3641;
                   border-radius:99px; padding:4px 9px; }}
  .sectitle small {{ font-weight:400; color:#6f7987; font-size:12px; }}
  .hidden {{ display:none; }}
  footer {{ color:#6f7987; font-size:12px; text-align:center; padding:8px 0 40px; }}
</style>
</head>
<body>
  <div class="top">
    <h1>{len(ready)} message{'s' if len(ready) != 1 else ''} ready to send</h1>
    <p>Tap a button to open it in your email app with everything filled in.
       <span id="counts"></span></p>
  </div>
  <div class="wrap">
    <h2 class="sectitle" id="toSendTitle">To send <span class="cnt" id="toSendCount">0</span></h2>
    <div id="toSend">
      {''.join(cards) or '<p class="meta">Nothing ready yet.</p>'}
    </div>
    <div id="openedWrap" class="hidden">
      <h2 class="sectitle">Opened <span class="cnt" id="openedCount">0</span>
        <small>you tapped these already</small></h2>
      <p class="meta small">You opened these drafts on this device. Check whether you actually
         pressed send &mdash; then tap &ldquo;Sent&rdquo; so the card stops nagging you.</p>
      <div id="opened"></div>
    </div>
    {failed_html}
    <footer>
      Generated {time.strftime('%d %b %Y, %H:%M')}{(' &middot; ' + e(sender)) if sender else ''}<br>
      Tap "Sent" after you email a store &mdash; this page remembers it on this device,
      so you can close it and come back without emailing anyone twice.
    </footer>
  </div>
<script>
// Copy subject + body to the clipboard.
document.querySelectorAll('[data-copy]').forEach(function (btn) {{
  btn.addEventListener('click', function () {{
    var card = btn.closest('.card');
    var text = 'Subject: ' + card.querySelector('.subject').innerText.replace(/^Subject\\s*/,'')
             + '\\n\\n' + card.querySelector('pre.body').innerText;
    navigator.clipboard.writeText(text).then(function () {{
      btn.textContent = 'Copied';
      setTimeout(function () {{ btn.textContent = 'Copy'; }}, 1500);
    }}).catch(function () {{ btn.textContent = 'Press and hold to copy'; }});
  }});
}});

// Everything below is remembered on this device via localStorage. A downloaded
// page has no server to ask what you already did, so the browser is the only
// place that knows. If localStorage is unavailable the taps still work, they
// just will not survive a reload.
function loadList(key) {{
  try {{ return JSON.parse(localStorage.getItem(key) || '[]'); }} catch (e) {{ return []; }}
}}
function storeList(key, list) {{
  try {{ localStorage.setItem(key, JSON.stringify(list)); }} catch (e) {{}}
}}
var KEY = 'outreach-sent-v1';
var OPEN_KEY = 'outreach-opened-v1';
var sent = loadList(KEY);
var opened = loadList(OPEN_KEY);

// Cards are identified by domain, not by position, so the memory survives a
// page that was regenerated with the stores in a different order.
function cardKey(card) {{
  var meta = card.querySelector('.meta');
  var domain = meta ? (meta.innerText.split('\\u00b7')[1] || '').trim() : '';
  return domain || card.querySelector('h2').innerText.trim();
}}

function recount() {{
  var left = document.querySelectorAll('#toSend .card:not(.opened)').length;
  var done = document.querySelectorAll('#opened .card').length;
  document.getElementById('toSendCount').textContent = left;
  document.getElementById('openedCount').textContent = done;
  document.getElementById('openedWrap').classList.toggle('hidden', done === 0);
  document.getElementById('counts').textContent = done ? ' &middot; ' + done + ' already opened' : '';
}}

// Opening the draft is not sending it - but it is enough to move the card out
// of the way. The point is never opening the same store twice by accident.
function markOpened(card) {{
  if (!card || card.classList.contains('opened')) return;
  card.classList.add('opened');
  document.getElementById('opened').appendChild(card);
  var k = cardKey(card);
  if (opened.indexOf(k) === -1) {{ opened.push(k); storeList(OPEN_KEY, opened); }}
  recount();
}}

document.querySelectorAll('[data-open]').forEach(function (a) {{
  a.addEventListener('click', function () {{ markOpened(a.closest('.card')); }});
}});

// Re-apply what this device already tapped.
document.querySelectorAll('#toSend .card').forEach(function (card) {{
  if (opened.indexOf(cardKey(card)) !== -1) markOpened(card);
}});

function paint(btn, id, isSent) {{
  var card = document.getElementById(id);
  card.classList.toggle('done', isSent);
  btn.textContent = isSent ? 'Sent \u2713' : 'Sent';
}}

document.querySelectorAll('[data-sent]').forEach(function (btn) {{
  var id = 'c' + btn.dataset.sent;
  var domain = cardKey(document.getElementById(id));
  var marked = sent.indexOf(domain) !== -1;
  paint(btn, id, marked);
  btn.addEventListener('click', function () {{
    var isSent = sent.indexOf(domain) === -1;
    if (isSent) {{ sent.push(domain); }} else {{ sent = sent.filter(function (d) {{ return d !== domain; }}); }}
    storeList(KEY, sent);
    paint(btn, id, isSent);
  }});
}});

recount();
</script>
</body>
</html>
"""


def write_csv(path: Path, entries: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["email", "store_name", "domain", "subject", "body", "status", "engine", "hook"])
        for x in entries:
            w.writerow([
                x.get("email", ""), x.get("store_name", ""), x.get("domain", ""),
                x.get("subject", ""), (x.get("body", "") or "").replace("\n", "\\n"),
                x.get("status", ""), x.get("engine", ""), x.get("hook", ""),
            ])


# ----------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m app.cli",
        description="Crawl stores and write a tappable outbox page. No web server needed.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python3 -m app.cli leads.csv --limit 5      try 5 stores first\n"
            "  python3 -m app.cli leads.csv                do the whole list\n"
            "  python3 -m app.cli leads.csv --redo         rewrite ones already done\n"
        ),
    )
    ap.add_argument("leads", type=Path, help="CSV, XLSX or TXT file with email / domain / store name")
    ap.add_argument("--out", type=Path, default=None, help="where to write the outbox page")
    ap.add_argument("--state", type=Path, default=None, help="run state (lets you resume)")
    ap.add_argument("--csv-out", type=Path, default=None, help="spreadsheet copy of the results")
    ap.add_argument("--limit", type=int, default=0, help="only process the first N stores")
    ap.add_argument("--redo", action="store_true", help="re-crawl and rewrite stores already finished")
    ap.add_argument("--no-ai", action="store_true", help="force templates even if an API key is set")
    ap.add_argument("--concurrency", type=int, default=3, help="how many stores at once (default 3)")

    if argv is None and len(sys.argv) == 1:
        ap.print_help()
        print(
            "\n  Give it your list file, for example:\n"
            "      python3 -m app.cli leads.csv\n"
            "      python3 -m app.cli leads.csv --limit 5\n"
            "\n  On Android / Pydroid 3, see ANDROID.md - and note that outputs are\n"
            "  written next to your list file, so they're easy to find.\n"
        )
        return 1

    args = ap.parse_args(argv)

    # Outputs default to sitting NEXT TO THE LIST FILE, not the current folder.
    # On a phone the current folder is unpredictable and often unwritable;
    # Downloads is where you'll actually be able to open the result.
    base = args.leads.resolve().parent
    args.out = args.out or (base / "outbox.html")
    args.state = args.state or (base / "outbox.json")
    args.csv_out = args.csv_out or (base / "messages.csv")

    settings = db.load_settings()
    if args.no_ai:
        settings["use_ai"] = False

    leads = load_leads(args.leads)
    if args.limit:
        leads = leads[: args.limit]

    ai = "on (" + (settings.get("model") or "model") + ")" if (
        settings.get("use_ai") and (settings.get("api_key") or "").strip()
    ) else "off - using templates"

    print()
    print(f"  list       : {args.leads}  ({len(leads)} store{'s' if len(leads) != 1 else ''})")
    print(f"  AI writing : {ai}")
    print(f"  outbox     : {args.out.resolve()}")
    print(f"  state      : {args.state.resolve()}   (re-running skips finished stores)")
    print()

    state = load_state(args.state)
    try:
        asyncio.run(crawl_all(leads, settings, args, state, args.state))
    except KeyboardInterrupt:
        print("\n  stopped - progress saved, re-run to continue", flush=True)

    entries = list(state.values())
    ready = [x for x in entries if x.get("status") == "ready"]
    failed = [x for x in entries if x.get("status") == "failed"]

    args.out.write_text(build_outbox_html(entries, settings), encoding="utf-8")
    write_csv(args.csv_out, entries)

    print()
    print(f"  {len(ready)} ready, {len(failed)} failed")
    print(f"  outbox written to : {args.out.resolve()}")
    print(f"  spreadsheet       : {args.csv_out.resolve()}")
    print()
    print("  Open outbox.html and tap a store to send.")
    print("  On Android, open it with your file manager or Chrome's Downloads.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
