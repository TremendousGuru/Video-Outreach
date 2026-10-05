"""Every setting the engine reads must be reachable from the hosted sidebar.

A setting that app/*.py honours but the sidebar cannot save is a setting you
cannot change on the hosted app - which is the app that matters.
"""
import os, re, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["OUTREACH_DB"] = os.path.join(tempfile.mkdtemp(), "s.db")
os.environ["OPENAI_API_KEY"] = "gsk_verify1234567890abcd"

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(root, "streamlit_app.py"), encoding="utf-8").read()

from app import db
from streamlit.testing.v1 import AppTest

# 1. Which settings does the engine actually read?
used = set()
for f in ("crawler.py", "compose.py", "pipeline.py", "cli.py"):
    used |= set(re.findall(r'settings\.get\(\s*["\'](\w+)["\']',
                           open(os.path.join(root, "app", f), encoding="utf-8").read()))
used |= set(re.findall(r'settings\.get\(\s*["\'](\w+)["\']', src))
# api_key is environment-only by design; base_url/model are set by the Provider picker.
not_ui = {"api_key", "base_url", "model", "subject_style", "optout_line"}
missing = sorted(used - not_ui - set(re.findall(r'"(\w+)":\s*\w+,?\s*$', src, re.M)))
print("engine reads:", len(used), "settings")

at = AppTest.from_file(os.path.join(root, "streamlit_app.py"), default_timeout=90).run()
assert not at.exception, at.exception

# 2. Each one must have a control in the sidebar and survive a save.
save = [b for b in at.sidebar.button if "Save settings" in str(b.label)][0]
save.click().run()
saved = db.load_settings()
unreachable = [k for k in sorted(used - not_ui) if k not in saved or saved[k] != db.DEFAULTS.get(k)]
print("saved by the sidebar:", sorted(k for k in used - not_ui if k in saved))
assert not unreachable, f"sidebar did not persist: {unreachable}"

# 3. Change the crawler knobs and confirm they land in the database.
ti = {i.label: i for i in at.sidebar.text_input}
assert "Company or handle (optional)" in ti and "Tone" in ti, sorted(ti)
ti["Company or handle (optional)"].set_value("Studio Nine")
ti["Tone"].set_value("dry, short, no exclamation marks")
sl = {s.label: s for s in at.sidebar.slider}
sl["Pages to read per store"].set_value(9)
sl["Products to read per store"].set_value(40)
ni = {n.label: n for n in at.sidebar.number_input}
ni["Pause between requests (seconds)"].set_value(1.5)
ck = {c.label: c for c in at.sidebar.checkbox}
ck["Hunt for a contact email when the row has none"].uncheck()
[b for b in at.sidebar.button if "Save settings" in str(b.label)][0].click().run()

after = db.load_settings()
expect = {"sender_company": "Studio Nine", "tone": "dry, short, no exclamation marks",
          "max_pages": 9, "max_products": 40, "request_delay": 1.5,
          "find_missing_emails": False}
for k, v in expect.items():
    assert after[k] == v, (k, after[k], v)
print("crawler knobs persisted:", expect)

# 4. And they must reach the code that uses them.
from app import compose as composer
body = composer.template_body({"store_name": "Alpha", "products": [{"title": "Wool Runner", "price": "$98"}],
                               "domain": "alpha.com"}, after)
assert "Studio Nine" in body["body"], body["body"]
print("sender_company reaches the email body ✅")
# 5. Parity: every setting the engine reads must be reachable from BOTH UIs.
#    This is what let six settings sit unreachable on the hosted app.
import re as _re
_js = open(os.path.join(root, "app", "static", "app.js"), encoding="utf-8").read()
_j = _js.index("async function saveSettings")
_patch = _js[_js.index("const patch = {", _j):_js.index("// No key here on purpose", _j)]
local_saves = set(_re.findall(r"(?:^|[,{])\s*(\w+):", _patch, _re.M))

_i = src.index('if st.button("Save settings"')
_block = src[src.index("db.save_settings({", _i):src.index("})", _i)]
hosted_saves = set(_re.findall(r'"(\w+)":', _block))

need = set(db.DEFAULTS) - {"api_key", "base_url", "model"}
assert not (need - hosted_saves), f"hosted sidebar cannot set: {sorted(need - hosted_saves)}"
assert not (need - local_saves), f"local UI cannot set: {sorted(need - local_saves)}"
print(f"  ok  all {len(need)} engine settings reachable from both UIs")

# 6. And no DEFAULTS key may be dead - a setting nothing reads is a knob that
#    looks like it does something.
dead = []
for k in db.DEFAULTS:
    hits = 0
    for f in ("crawler.py", "compose.py", "pipeline.py", "cli.py", "main.py"):
        hits += len(_re.findall(rf'settings\.get\(\s*["\']{k}["\']',
                                open(os.path.join(root, "app", f), encoding="utf-8").read()))
    hits += len(_re.findall(rf'settings\.get\(\s*["\']{k}["\']', src))
    if not hits:
        dead.append(k)
assert not dead, f"dead settings nothing reads: {dead}"
print("  ok  no dead settings in DEFAULTS")

print("\nPASSED")
