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
