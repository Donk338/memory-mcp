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
    print(f"  delivered (200, paid)     {len(delivered):>7}")
    print(f"  other errors              {len(other_err):>7}")
    print(f"  ---- SETTLED REVENUE      {len(delivered):>7}")
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

    print(f"\n  distinct callers {len({r[6] for r in chall})}   distinct UAs {len({r[7] for r in chall})}")
    if not has:
        print("\n  (caller/ua blank for historical rows -- instrumentation landed 2026-10-01)")
    print("=" * 62)


if __name__ == "__main__":
    main()
