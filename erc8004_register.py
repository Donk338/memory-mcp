#!/usr/bin/env python3
"""Register Boris Memory (mem.borisinc.com) as an ERC-8004 agent on Base.
Reuses Aegis's erc8004 primitives. Broadcast is operator-run (--live + key source).
Usage: erc8004_register.py [--live --key-config ANCHOR_PRIVATE_KEY] | [--finalize AGENT_ID]"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, "/home/donk/aegis")
import erc8004

BASE = Path(__file__).parent
REG_FILE = BASE / "agent-registration.json"
REG_URI = "https://mem.borisinc.com/.well-known/agent-registration.json"
META = dict(
    name="Boris Memory",
    description=("Persistent agent memory paid per call via x402 (USDC on Base). Your wallet is your "
                 "private memory namespace — no signup, no API key. Semantic (embedding) search. "
                 "write $0.001 / search $0.002 / export $0.01; stats free. MCP + HTTP."),
    web="https://mem.borisinc.com",
    mcp_endpoint="https://mem.borisinc.com/mcp")

def build(agent_id=None):
    f = erc8004.build_registration_file(**META, agent_id=agent_id)
    return f

def main(argv):
    if "--finalize" in argv:
        aid = int(argv[argv.index("--finalize") + 1])
        REG_FILE.write_text(json.dumps(build(aid), indent=2))
        print(f"finalized with agentId {aid} -> {REG_FILE}")
        return
    REG_FILE.write_text(json.dumps(build(), indent=2))
    data = erc8004.encode_register(REG_URI)
    print(f"registration file -> {REG_FILE} (served at {REG_URI})")
    print(f"register(string) tx: to={erc8004.IDENTITY} ({len(data)//2-1} bytes)")
    if "--live" not in argv:
        print("DRY RUN — rerun with --live --key-config ANCHOR_PRIVATE_KEY (reads /home/donk/aegis/config.env)")
        return
    key = None
    if "--key-config" in argv:
        name = argv[argv.index("--key-config") + 1]
        cfg = dict(l.strip().split("=", 1) for l in open("/home/donk/aegis/config.env") if "=" in l)
        key = cfg.get(name)
    if "--key-env" in argv:
        import os; key = os.environ.get(argv[argv.index("--key-env") + 1])
    if not key:
        sys.exit("--live requires --key-config NAME or --key-env NAME")
    r = erc8004.send_tx(erc8004.IDENTITY, data, key, broadcast=True)
    print(f"broadcast: {r.get('tx_hash')}")
    aid = None
    for _ in range(10):
        rc = erc8004.wait_receipt(r["tx_hash"])
        try:
            aid = erc8004.agent_id_from_receipt(rc); break
        except ValueError:
            time.sleep(3)
    if aid is None:
        sys.exit("mined but logs not indexed — find agentId on blockscout, then --finalize N")
    print(f"registered as agent #{aid} — finalizing")
    main([argv[0], "--finalize", str(aid)])

if __name__ == "__main__":
    main(sys.argv)
