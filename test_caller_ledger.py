"""Offline tests: caller/ua instrumentation on the x402 calls ledger.

Verifies (1) a legacy ledger is migrated additively and (2) log_call persists
the request-scoped caller identity published by the call_log middleware.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_server as M  # noqa: E402

tmp = Path(tempfile.mkdtemp()) / "calls.db"

# --- 1. legacy ledger (pre-caller schema) migrates additively -------------
c = sqlite3.connect(tmp)
c.execute("""CREATE TABLE calls(id INTEGER PRIMARY KEY, ts REAL, tool TEXT, ns TEXT,
             paid INT, price TEXT, latency_ms REAL, err TEXT)""")
c.execute("INSERT INTO calls(ts,tool,ns,paid,price,latency_ms,err) VALUES(1,'old','',0,'$0',1,'')")
c.commit(); c.close()

M.CALLDB = tmp
M._migrate_calls()
cols = {r[1] for r in sqlite3.connect(tmp).execute("PRAGMA table_info(calls)")}
assert {"caller", "ua"} <= cols, cols
old = sqlite3.connect(tmp).execute("SELECT tool FROM calls WHERE id=1").fetchone()
assert old == ("old",), "migration must not drop existing rows"
print("PASS legacy ledger migrated additively, old rows preserved")

# --- 2. caller identity is persisted on each write ------------------------
M._caller_ctx.set(("203.0.113.7", "Mozilla/5.0 (compatible; SomeBot/1.0)"))
M.log_call("http:/paid/memory/search", "0x" + "a" * 40, 0, "$0.002", 1.0)
row = sqlite3.connect(tmp).execute(
    "SELECT tool, paid, caller, ua FROM calls ORDER BY id DESC LIMIT 1").fetchone()
assert row[2] == "203.0.113.7", row
assert "SomeBot" in row[3], row
print("PASS caller/ua recorded on ledger write")

# --- 3. caller IP prefers edge headers over the loopback socket peer -------
class _Req:
    def __init__(self, headers):
        self.headers = headers
        self.client = type("C", (), {"host": "127.0.0.1"})()

assert M._caller_ip(_Req({"cf-connecting-ip": "203.0.113.9"})) == "203.0.113.9"
assert M._caller_ip(_Req({"x-forwarded-for": "203.0.113.8, 10.0.0.1"})) == "203.0.113.8"
assert M._caller_ip(_Req({})) == "127.0.0.1"
print("PASS caller IP resolution (edge headers -> socket peer)")

print("\nALL PASS")
