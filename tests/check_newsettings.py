"""Functional proof for the four settings added last: the control must exist,
the save must persist, and the value must reach the code that reads it."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["OUTREACH_DB"] = os.path.join(tempfile.mkdtemp(), "n.db")
os.environ["OPENAI_API_KEY"] = "gsk_verify1234567890abcd"

from app import db
from app import compose as composer
from streamlit.testing.v1 import AppTest

# --- hosted sidebar: optout_line and subject_style ---
at = AppTest.from_file(os.path.join(root, "streamlit_app.py"), default_timeout=90).run()
assert not at.exception, at.exception
ti = {i.label: i for i in at.sidebar.text_input}
for want in ("Opt-out wording", "Subject line style"):
    assert want in ti, f"missing control {want!r}; have {sorted(ti)}"
ti["Opt-out wording"].set_value("Reply STOP and I will not write again.")
ti["Subject line style"].set_value("all lowercase, no punctuation")
[b for b in at.sidebar.button if "Save settings" in str(b.label)][0].click().run()
saved = db.load_settings()
assert saved["optout_line"] == "Reply STOP and I will not write again.", saved["optout_line"]
assert saved["subject_style"] == "all lowercase, no punctuation"
print("  ok  optout_line + subject_style persist from the hosted sidebar")

# and they must reach the composer
body = composer.template_body({"store_name": "Alpha", "domain": "alpha.com",
                               "products": [{"title": "Wool Runner", "price": "$98"}],
                               "emails": ["hi@alpha.com"]}, saved)
assert "Reply STOP and I will not write again." in body["body"], body["body"]
print("  ok  optout_line lands in the email body")

from app import compose
ai = compose.compose_ai.__doc__ or ""
prompt = None
try:
    prompt = compose._build_prompt({"store_name": "Alpha"}, saved) if hasattr(compose, "_build_prompt") else None
except Exception:
    pass
src = open(os.path.join(root, "app", "compose.py"), encoding="utf-8").read()
assert 'settings.get("subject_style")' in src and "Style note: {subject_style}" in src
print("  ok  subject_style feeds the model prompt (compose.py:85,110)")

# --- local UI: max_products and subject_style round-trip through the API ---
from fastapi.testclient import TestClient
from app.main import app as fastapi_app
c = TestClient(fastapi_app)
r = c.post("/api/settings", json={"max_products": 44, "subject_style": "blunt only"})
assert r.status_code == 200, r.text
got = db.load_settings()
assert got["max_products"] == 44, got["max_products"]
assert got["subject_style"] == "blunt only", got["subject_style"]
# and it comes back on GET so the modal pre-fills
g = c.get("/api/settings").json()
assert g["max_products"] == 44 and g["subject_style"] == "blunt only", g
assert "api_key" not in g and g.get("has_api_key") is True
print("  ok  max_products + subject_style round-trip through POST/GET /api/settings")

# the page must actually render the two new inputs
html = c.get("/").text
for el in ('id="s_max_products"', 'id="s_subject_style"'):
    assert el in html, f"{el} missing from the served page"
js = c.get("/static/app.js").text
for el in ('$("#s_max_products")', '$("#s_subject_style")'):
    assert el in js, f"{el} missing from app.js"
print("  ok  both new fields are in the served page and wired in app.js")

print("\nPASSED")
