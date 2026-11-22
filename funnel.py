#!/usr/bin/env python3
"""Private funnel report for the x402 memory surface (mem.borisinc.com).

Answers the only question that matters for revenue: of the payment challenges we
issued, how many came back delivered/settled -- and WHO was calling?

Reads memory-calls.db directly. Local only; this never leaves the box.

Usage:  python3 funnel.py [--top N] [--since DAYS]
"""
import argparse
import sqlite3
import time
from collections import Counter
from pathlib import Path

DB = Path(__file__).parent / "memory-calls.db"

# UA substrings that mean "machine looking at us, not a buyer".
CRAWLER_HINTS = (
    "bot", "crawler", "spider", "scan", "curl", "wget", "python-requests",
    "httpx", "go-http-client", "axios", "node-fetch", "okhttp", "headless",
    "zgrab", "masscan", "nmap", "facebookexternalhit", "slurp", "monitor",
)


def classify(ua: str) -> str:
    u = (ua or "").lower()
    if not u:
        return "unknown-ua"
    if any(h in u for h in CRAWLER_HINTS):
        return "crawler"
    if "x402" in u or "agent" in u or "mcp" in u:
        return "agent"
    return "other"


def mask(ip: str) -> str:
    if not ip:
        return "-"
    parts = ip.split(".")
    return ".".join(parts[:3]) + ".x" if len(parts) == 4 else ip[:8] + "…"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--since", type=float, default=0.0, help="days back (0 = all)")
    a = ap.parse_args()

    if not DB.exists():
        raise SystemExit(f"no ledger at {DB}")
    c = sqlite3.connect(DB)
    cols = {r[1] for r in c.execute("PRAGMA table_info(calls)")}
    has = {"caller", "ua"} <= cols
    sel = "ts,tool,ns,paid,price,err,caller,ua" if has else "ts,tool,ns,paid,price,err,'' ,''"
    cutoff = time.time() - a.since * 86400 if a.since else 0
    rows = c.execute(f"SELECT {sel} FROM calls WHERE ts>=?", (cutoff,)).fetchall()
    c.close()

    if not has:
        print("!! caller/ua columns missing -- run the new memory_server.py at least once to migrate")

    paid = [r for r in rows if str(r[1]).startswith("http:/paid/")]
    chall = [r for r in paid if r[3] == 0 and (r[5] or "") == ""]
    delivered = [r for r in paid if r[3] == 1]
    other_err = [r for r in paid if r[3] == 0 and (r[5] or "") != ""]

    print("=" * 62)
    print("memory-mcp x402 funnel" + (f"  (last {a.since:g}d)" if a.since else "  (all time)"))
    print("=" * 62)
    print(f"  challenges issued (402)   {len(chall):>7}")
    print(f"  delivered (HTTP 200)      {len(delivered):>7}")
    print(f"  other errors              {len(other_err):>7}")
    # NOTE (2026-11-21): calls.paid is an HTTP STATUS (1 = the /paid/* answer was
    # 200), NOT proof of payment. A 200 here means the x402 middleware let the call
    # through; settlement must be confirmed on chain. Reporting this count as
    # "settled revenue" is what produced the false 'ZERO settled' headline - the
    # chain showed real USDC in while this ledger said zero. Verify against the
    # USDC contract on Base, not this counter.
    print(f"  ---- 200s (verify on chain) {len(delivered):>4}")
    print("  chain check: USDC 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
    print("               -> payTo 0x30A535132ea7573Fd339B30FEE8b677E0ec947E0 (Base)")
    if chall:
        print(f"  conversion                {100.0 * len(delivered) / len(chall):>6.2f}%")

    print("\n  per route (challenge -> delivered)")
    per = Counter()
    for r in paid:
        per[(r[1], r[3])] += 1
    for route in sorted({r[1] for r in paid}):
        ch, dl = per[(route, 0)], per[(route, 1)]
        print(f"    {route:<34} {ch:>5} -> {dl:>4}")

    print("\n  caller class (challenges only)")
    cls = Counter(classify(r[7]) for r in chall)
    for k, v in cls.most_common():
        print(f"    {k:<14} {v:>6}")

    print(f"\n  top {a.top} callers by challenge count")
    for ip, n in Counter(r[6] or "-" for r in chall).most_common(a.top):
        print(f"    {mask(ip):<20} {n:>6}")

    # ---- free lane: the try-before-you-buy path. Trial needs no wallet (IP-keyed),
    # the wallet tier needs an EIP-191 signature. Count both, blame the right gate.
    trial = [r for r in rows if str(r[1]).startswith("http:/trial/")]
    free = [r for r in rows if str(r[1]).startswith("http:/free/")]
    free_ok = [r for r in free if r[3] == 0 and (r[5] or "") == ""]
    free_nosig = [r for r in free if "wallet_auth" in (r[5] or "")]
    trial_ok = [r for r in trial if (r[5] or "") == ""]
    trial_cap = [r for r in trial if "trial cap" in (r[5] or "")]
    print("\n  free lane (no payment expected)")
    print(f"    trial used (no wallet)              {len(trial_ok):>5}")
    print(f"    trial cap-hit (marketing signal)    {len(trial_cap):>5}")
    print(f"    wallet-tier delivered               {len(free_ok):>5}")
    print(f"    wallet-tier bounced at signature    {len(free_nosig):>5}")
    if free_nosig:
        print(f"    -> {100.0*len(free_ok)/max(1,len(free)):.1f}% of wallet-tier attempts got past the signature gate")

    print(f"\n  distinct callers {len({r[6] for r in chall})}   distinct UAs {len({r[7] for r in chall})}")
    if not has:
        print("\n  (caller/ua blank for historical rows -- instrumentation landed 2026-10-01)")
    print("=" * 62)


if __name__ == "__main__":
    main()
