#!/usr/bin/env python3
"""Prove settlement on mem.borisinc.com — on chain, not from our own ledger.

WHY THIS EXISTS (2026-11-19): the calls ledger records `paid` = the HTTP status
of the /paid/* answer, plus caller/ua. It stores NO transaction hash, so a row
with paid=1 cannot, on its own, distinguish "a client really paid USDC and we
served it" from "something returned 200". funnel.py therefore could never
answer the only revenue question that matters. This script answers it from
Base mainnet: every inbound USDC transfer to the merchant wallet, with tx hash,
sender, amount and block time.

Read-only. No keys, no signing, no writes. Public RPC, no auth.

Usage:
  python3 scripts/prove_settlements.py                # last 45 days
  python3 scripts/prove_settlements.py --days 7
  python3 scripts/prove_settlements.py --from-block 38000000
"""
import argparse
import json
import time
import urllib.request

RPC = "https://mainnet.base.org"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
MERCHANT = "0x30A535132ea7573Fd339B30FEE8b677E0ec947E0"
# keccak256("Transfer(address,address,uint256)")
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
CHUNK = 100_000


def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.loads(r.read())
    if "error" in out:
        raise RuntimeError(f"{method}: {out['error']}")
    return out["result"]


def block_ts(n):
    b = rpc("eth_getBlockByNumber", [hex(n), False])
    return int(b["timestamp"], 16)


def block_at_or_after(ts):
    """Binary-search the first block whose timestamp >= ts."""
    lo, hi = 0, int(rpc("eth_blockNumber", []), 16)
    while lo < hi:
        mid = (lo + hi) // 2
        if block_ts(mid) < ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=45.0)
    ap.add_argument("--from-block", type=int, default=0)
    a = ap.parse_args()

    head = int(rpc("eth_blockNumber", []), 16)
    start = a.from_block or block_at_or_after(int(time.time() - a.days * 86400))
    print(f"merchant {MERCHANT}")
    print(f"scanning USDC Transfer -> merchant, blocks {start}..{head} ({head - start} blocks)")

    topic_to = "0x" + "0" * 24 + MERCHANT[2:].lower()
    hits = []
    n = start
    while n <= head:
        hi = min(n + CHUNK - 1, head)
        logs = rpc("eth_getLogs", [{
            "address": USDC,
            "fromBlock": hex(n),
            "toBlock": hex(hi),
            "topics": [TRANSFER_TOPIC, None, topic_to],
        }])
        for lg in logs:
            hits.append({
                "block": int(lg["blockNumber"], 16),
                "tx": lg["transactionHash"],
                "from": "0x" + lg["topics"][1][-40:],
                "amount_usdc": int(lg["data"], 16) / 1e6,
            })
        n = hi + 1

    total = 0.0
    for h in sorted(hits, key=lambda x: x["block"]):
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(block_ts(h["block"])))
        except Exception:
            ts = "?"
        total += h["amount_usdc"]
        print(f"  {ts}  {h['amount_usdc']:.6f} USDC  from {h['from']}  {h['tx']}")

    print(f"\ninbound transfers: {len(hits)}   total received: {total:.6f} USDC")
    # An x402 sale is an inbound transfer at the route price ($0.001-$0.01).
    sales = [h for h in hits if h["amount_usdc"] <= 0.01]
    print(f"at-or-below route price (x402-shaped): {len(sales)}")
    if not hits:
        print("\nVERDICT: no USDC has ever reached the merchant wallet -> zero settled payments.")
    elif not sales:
        print("\nVERDICT: value arrived, but none of it is route-priced -> no x402 sale proven.")
    else:
        print("\nVERDICT: route-priced inbound USDC exists -> at least one settled x402 payment is provable on chain.")


if __name__ == "__main__":
    main()
