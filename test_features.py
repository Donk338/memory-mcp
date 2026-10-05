"""Offline tests for trial/sub/grant/agent-ns features."""
import base64, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_server as M
from eth_account import Account

# namespace model
assert M.VALID_NS_EXT.match("0x" + "a" * 40)
assert M.VALID_NS_EXT.match("agent:8453:58766") and M.VALID_NS_EXT.match("trial:abc123def456")
assert not M.VALID_NS_EXT.match("agent:1:5") and not M.VALID_NS_EXT.match("bogus")
print("PASS namespace model")

# grant roundtrip
owner, grantee = Account.create(), Account.create()
exp = int(time.time()) + 86400
td = M._grant_typed(owner.address.lower(), grantee.address.lower(), "rw", exp)
from eth_account.messages import encode_typed_data
sig = "0x" + owner.sign_message(encode_typed_data(full_message=td)).signature.hex() if False else \
      "0x" + Account.sign_message(encode_typed_data(full_message=td), owner.key).signature.hex()
blob = base64.b64encode(json.dumps({"namespace": owner.address.lower(), "grantee": grantee.address.lower(),
                                    "mode": "rw", "expires": exp, "signature": sig}).encode()).decode()
ns = M._verify_grant(blob, grantee.address.lower(), need_write=True)
assert ns == owner.address.lower()
print("PASS grant sign/verify roundtrip (write)")

# grant failure modes
def fails(**kw):
    d = {"namespace": owner.address.lower(), "grantee": grantee.address.lower(), "mode": "read", "expires": exp, "signature": sig}
    d.update(kw)
    b = base64.b64encode(json.dumps(d).encode()).decode()
    try:
        M._verify_grant(b, kw.pop("_payer", grantee.address.lower()), kw.pop("_write", False)); return False
    except (ValueError, Exception): return True
assert fails(_payer="0x" + "1" * 40), "wrong payer accepted"
assert fails(expires=int(time.time()) - 10), "expired grant accepted"
assert fails(mode="read", _write=True), "read grant allowed write"
assert fails(expires=int(time.time()) + 200 * 86400), "200d grant accepted"
# tampered mode (sig was over 'rw'): mode=write not signed
assert fails(mode="write"), "tampered mode accepted"
print("PASS grant failure modes")

# remap: on_behalf mismatch + agent-keyed via preseeded cache (no RPC)
try:
    M._remap_ns({"on_behalf": "0x" + "2" * 40, "grant": blob}, grantee.address.lower(), write=False)
    assert False, "on_behalf mismatch accepted"
except ValueError: pass
c = M._mem()
c.execute("CREATE TABLE IF NOT EXISTS agent_wallet_cache(agent_id INTEGER PRIMARY KEY, wallet TEXT, ts REAL)")
c.execute("INSERT OR REPLACE INTO agent_wallet_cache VALUES(424242, ?, ?)", (grantee.address.lower(), time.time()))
c.commit(); c.close()
assert M._remap_ns({"agent_id": 424242}, grantee.address.lower()) == "agent:8453:424242"
try:
    M._remap_ns({"agent_id": 424242}, owner.address.lower()); assert False, "wrong wallet got agent ns"
except ValueError: pass
c = M._mem(); c.execute("DELETE FROM agent_wallet_cache WHERE agent_id=424242"); c.commit(); c.close()
print("PASS remap: grants + agent-keyed namespace auth")
print("ALL FEATURE TESTS PASS")

# --- POST-as-GET on the GET-shaped paid routes (2026-10-04) --------------
# A bare POST to a paid route used to answer `405 allow: GET` — the paywall
# never ran, no challenge was emitted — and every x402 discovery crawler probes
# with POST (the MCP transport is POST). The post_as_get middleware folds the
# body into the query string and rewrites to GET so the paywall runs.
assert M._post_body_params(b'{"query":"x","k":3}', "application/json") == {"query": "x", "k": 3}
assert M._post_body_params(
    b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"memory_search","arguments":{"query":"y"}}}',
    "application/json") == {"query": "y"}   # JSON-RPC tools/call envelope
assert M._post_body_params(b"query=z&k=2", "application/x-www-form-urlencoded") == {"query": "z", "k": "2"}
assert M._post_body_params(b"", "application/json") == {}
assert M._post_body_params(b"not json", "application/json") == {}
assert M._post_body_params(b'{"nested":{"a":1}}', "application/json") == {}  # scalars only
print("PASS post_as_get body folding")

from starlette.testclient import TestClient
_c = TestClient(M.fapp)
_r = _c.post("/paid/memory/search", json={"query": "x"})
assert _r.status_code == 402, f"POST /paid/memory/search -> {_r.status_code}, want 402 (was 405)"
_body = _r.json()
assert _body["accepts"][0]["scheme"] == "exact" and _body["accepts"][0]["network"] == "eip155:8453"
assert "query=x" in _body["resource"]["url"]          # body folded into the challenge URL
# /mcp is a real POST transport and must NOT be rewritten to GET
assert _c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).status_code != 402
print("PASS POST -> payable 402 on paid routes; /mcp untouched")

# --- server/discover on /mcp (2026-10-05) --------------------------------
# The 2025-11-25 MCP draft method `server/discover` is what an agent calls
# FIRST (no initialize, no session). The installed mcp lib does not know it,
# so every probe used to get -32602 "Invalid request parameters" and graded
# us dead. The shim in M.app() answers it before the session manager sees it.
_d = M._mcp_discover_result("disc-7")["result"]
assert _d["resultType"] == "complete" and _d["supportedVersions"][0] == "2025-11-25"
assert _d["capabilities"] == {"tools": {}}
assert _d["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "memory-mcp"
assert "memory_search" in _d["instructions"]
print("PASS _mcp_discover_result shape")

with TestClient(M.app) as _dc:   # context manager runs lifespan -> session manager
    _r = _dc.post("/mcp", headers={"accept": "application/json, text/event-stream"},
                  json={"jsonrpc": "2.0", "id": "disc-7", "method": "server/discover", "params": {}})
    assert _r.status_code == 200, _r.status_code
    _j = _r.json()
    assert "error" not in _j, _j
    assert _j["id"] == "disc-7" and _j["result"]["capabilities"] == {"tools": {}}
    # the same app must still serve the real transport (receive replay is not lossy)
    _r2 = _dc.post("/mcp", headers={"accept": "application/json, text/event-stream"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                                    "clientInfo": {"name": "t", "version": "0"}}})
    assert _r2.status_code == 200 and "serverInfo" in _r2.text, _r2.text[:200]
print("PASS server/discover 200 on /mcp, initialize unaffected")

# --- OpenAPI x402 discovery on /openapi.json (2026-10-05) ------------------
# BUG FIXED: custom_openapi() built the schema then fell off the end with no
# return, so /openapi.json served `null` and every crawler (x402scan) read the
# route price as None -> dead listing. Assert the schema is real and priced.
_o = M.fapp.openapi()
assert isinstance(_o, dict), type(_o)
assert _o["servers"] == [{"url": "https://mem.borisinc.com"}]
assert _o["info"]["x-guidance"].startswith("Persistent, wallet-namespaced memory")
for _p, _amt in (("/paid/memory/write", "0.001000"), ("/paid/memory/search", "0.002000"),
                 ("/paid/memory/export", "0.010000"), ("/paid/memory/forget", "0.001000"),
                 ("/paid/memory/import", "0.010000"), ("/subscribe", "2.000000")):
    _op = _o["paths"][_p]["get"]
    assert _op["x-payment-info"]["price"]["amount"] == _amt, (_p, _op.get("x-payment-info"))
    assert _op["x-payment-info"]["price"]["currency"] == "USD"
    assert "402" in _op["responses"], _p
for _p in ("/free/memory/write", "/free/memory/search", "/health", "/memory/stats"):
    assert _o["paths"][_p]["get"]["security"] == [], _p
assert M.fapp.openapi() is M.fapp.openapi_schema          # cached, not rebuilt
print("PASS /openapi.json serves a priced schema (6 paid routes, free routes security:[])")
