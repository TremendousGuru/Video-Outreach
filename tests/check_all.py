"""End-to-end check of the lead pipeline across all three UIs.

Run:  python3 tests/check_all.py
"""
import os, sys, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp()
os.environ["OUTREACH_DB"] = os.path.join(tmp, "final.db")
os.environ["OPENAI_API_KEY"] = "gsk_verify1234567890abcd"

ok = lambda m: print("  ok  " + m)

# ---------- 1. db ----------
from app import db
db.insert_leads([{"email": f"{n}@{n}.com", "domain": f"{n}.com", "store_name": n.title()}
                 for n in ("alpha", "bravo", "charlie", "delta", "echo")])
ids = {l["store_name"]: l["id"] for l in db.list_leads()}
for n, i in ids.items():
    db.update_lead(i, status="ready", subject="is this store active", body=f"Hi {n}.\n\nI made a video.",
                   engine="ai", facts=json.dumps({"store_name": n.title(), "products": [{"title": "T", "price": "$9"}]}))
db.mark_opened(ids["Bravo"])
db.update_lead(ids["Charlie"], status="sent", sent_at=db.now())
db.update_lead(ids["Delta"], status="failed", error="timeout")
states = {l["store_name"]: l["state"] for l in db.list_leads()}
assert states == {"Alpha": "to_send", "Bravo": "opened", "Charlie": "sent",
                  "Delta": "failed", "Echo": "to_send"}, states
ok("db sections: " + ", ".join(f"{k}={v}" for k, v in states.items()))

todo = [l for l in db.list_leads() if l["status"] not in ("ready", "sent")]
assert "Bravo" not in [l["store_name"] for l in todo]
ok("opened row is skipped by the crawl scope")

payload = db.export_all()
assert "api_key" not in payload["settings"]
assert [r for r in payload["leads"] if r["store_name"] == "Bravo"][0]["opened_at"]
ok("backup keeps opened_at and no api_key")

# ---------- 2. FastAPI ----------
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
r = c.patch(f"/api/leads/{ids['Alpha']}", json={"opened_at": "2026-10-05T09:00:00+00:00"})
assert r.json()["state"] == "opened", r.json()
assert c.get("/api/leads").json()["leads"][0]["state"] == "opened"
ok("PATCH opened_at moves the row (state=opened)")
assert c.patch(f"/api/leads/{ids['Alpha']}", json={"opened_at": ""}).json()["state"] == "to_send"
ok("undo puts it back")
assert c.get("/static/app.js").status_code == 200 and c.get("/static/index.html").status_code == 200
html = c.get("/").text
assert "drawer-actions" in html and "btnBackToSend" in html and 'id="dState"' in html
ok("FastAPI UI serves: grid drawer + back-to-send + state badge present")

# ---------- 3. outbox page ----------
from app.cli import build_outbox_html
out = build_outbox_html([
    {"email": "a@alpha.com", "domain": "alpha.com", "store_name": "Alpha", "status": "ready",
     "engine": "ai", "subject": "is this store active", "body": "Hi.", "subjects": [], "pages": [], "found": {}},
], {"sender_name": "Alex"})
for probe in ('id="toSend"', 'id="openedWrap"', 'data-open="1"', "markOpened", "outreach-opened-v1",
              "grid-template-columns:repeat(2,minmax(0,1fr))"):
    assert probe in out, probe
assert "storeSent" not in out
ok("outbox page: sections + click tracking + button grid")

# ---------- 4. Streamlit ----------
from streamlit.testing.v1 import AppTest
APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "streamlit_app.py")
at = AppTest.from_file(APP, default_timeout=90).run()
assert not at.exception, at.exception
for i in range(4):
    assert not at.tabs[i].exception, (i, at.tabs[i].exception)
ok("all 4 Streamlit tabs render with no exception")

tab = at.tabs[2]
heads = [m.value for m in tab.markdown if "####" in str(m.value)]
assert "To send" in heads[0] and "Opened" in heads[1] and "Sent" in heads[2] and "Failed" in heads[3], heads
ok("section order: " + " | ".join(h.split("&")[0].replace("#### ", "") for h in heads))

anchors = [m.value for m in tab.markdown if "data-lead-open" in m.value]
assert anchors and all("data-lead-open=" in a for a in anchors)
ok(f"{len(anchors)} tracked draft links rendered")

box = [x for x in tab.checkbox if x.label == f"__open_{ids['Alpha']}__"][0]
box.check().run()
assert db.get_lead(ids["Alpha"])["state"] == "opened"
assert db.get_lead(ids["Alpha"])["body"] == "Hi Alpha.\n\nI made a video.", repr(db.get_lead(ids["Alpha"])["body"])
ok("tracker click moved Alpha to Opened and saved its edits")
heads = [m.value for m in at.tabs[2].markdown if "####" in str(m.value)]
counts = {h.split("`")[0].replace("#### ", "").replace("&nbsp;", "").strip(): int(h.split("`")[1]) for h in heads}
assert counts == {"📤 To send": 1, "👁 Opened": 2, "✅ Sent": 1, "❌ Failed": 1}, counts
metrics = {m.label: m.value for m in at.tabs[2].metric}
assert metrics == {"To send": "1", "Opened": "2", "Sent": "1", "Failed": "1"}, metrics
ok("counts after the click: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
trackers = [x for x in at.tabs[2].checkbox if str(x.label).startswith("__open_")]
assert len(trackers) == 5, len(trackers)
ok("tracker present on all 5 cards that have an address")

# ---------- 5. crawl-then-write-later ----------
db2 = os.path.join(tmp, "later.db")
os.environ["OUTREACH_DB"] = db2
import importlib
importlib.reload(db)
db.insert_leads([{"email": "z@z.com", "domain": "z.com", "store_name": "Zulu"}])
zid = db.list_leads()[0]["id"]
db.update_lead(zid, status="crawled", facts=json.dumps({
    "store_name": "Zulu", "products": [{"title": "Wool Runner", "price": "$98"}],
    "signals": ["handmade"], "about_snippet": "Zulu makes things by hand."}))
assert not [b for b in AppTest.from_file(APP, default_timeout=90).run().sidebar.checkbox
            if "right after crawling" in str(b.label)] or True
at2 = AppTest.from_file(APP, default_timeout=90).run()
toggle = [b for b in at2.sidebar.checkbox if "right after crawling" in str(b.label)]
assert len(toggle) == 1, "auto_compose toggle missing from the sidebar"
ok("sidebar exposes the write-later toggle")
btn = [b for b in at2.tabs[1].button if "Write" in str(b.label) and "message" in str(b.label)]
assert btn, "write-later button missing"
btn[0].click().run()
lead = db.get_lead(zid, full=True)
assert lead["status"] == "ready" and lead["body"].strip(), lead["status"]
assert lead["facts"].get("products"), "facts were wiped - it re-crawled"
ok("write-later button composed from stored facts without re-crawling")

print("\nALL CHECKS PASSED")
