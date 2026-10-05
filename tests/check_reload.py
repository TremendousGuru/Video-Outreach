"""The stale-module shim.

Streamlit Community Cloud pulls a push into the running container without
restarting the process, so streamlit_app.py is re-executed from disk while
app/*.py stays as the module objects cached at boot. That is the
AttributeError-on-a-function-that-exists failure. These tests pin the fix,
including the baseline bug: the comparison must be against the real process
start, not against the first time the shim happened to run.
"""
import os, sys, time, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["OUTREACH_DB"] = os.path.join(tempfile.mkdtemp(), "r.db")

import app as pkg
from app import db, pipeline

src = open(os.path.join(root, "streamlit_app.py"), encoding="utf-8").read()
ns = {"__name__": "shim", "os": os, "time": time}
exec(src[src.index("def _process_started_at"):src.index("_refresh_app_modules()\n\nfrom app import compose")], ns)

# 1. the baseline is the real process start, read from /proc
age = time.time() - ns["_process_started_at"]()
assert 0 <= age < 60, f"process age implausible: {age}"
print(f"  ok  process start read from /proc ({age:.2f}s ago)")

# 2. a file rewritten after boot is reloaded, and the old module comes back
del db.state_of
assert not hasattr(db, "state_of")
time.sleep(2)
os.utime(os.path.join(root, "app", "db.py"), None)
reloaded = ns["_refresh_app_modules"]()
assert "app.db" in reloaded, reloaded
assert hasattr(db, "state_of") and db.state_of({"status": "ready"}) == "to_send"
print(f"  ok  pull after boot reloads app.db and restores state_of ({reloaded})")

# 3. reload must keep the module object, or app/pipeline's `from . import db`
#    reference would still point at the old code
assert sys.modules["app.db"] is db and pipeline.db is db
print("  ok  same module object - pipeline.db still wired")

# 4. nothing newer than boot -> nothing reloaded
assert ns["_refresh_app_modules"]() == []
print("  ok  a second run reloads nothing")

# 5. no /proc must degrade to reloading nothing, not everything
saved_open = ns["os"].sysconf
try:
    def boom(*a, **k):
        raise OSError("no /proc")
    ns["os"].sysconf = boom
    os.utime(os.path.join(root, "app", "crawler.py"), None)
    assert ns["_refresh_app_modules"]() == [], "should reload nothing without /proc"
    print("  ok  without /proc it reloads nothing rather than everything")
finally:
    ns["os"].sysconf = saved_open

print("\nPASSED")
