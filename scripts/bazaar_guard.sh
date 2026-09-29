#!/usr/bin/env bash
# bazaar_guard.sh — daily check that the x402 paid surface SILL advertises a
# callable discovery shape. Wired 2026-09-29 after memory-mcp commit a71d159:
# declare_discovery_extension(input=...) was given a transport descriptor instead
# of example values, so all 5 paid routes shipped an example no agent could
# satisfy — silently, for as long as the routes existed. The bug class re-arms
# every time someone adds a paid route, so it is checked, not remembered.
#
# Success is silent-but-dated (one line). Failure writes the full verifier output
# to a .fail file that is easy to grep from an attention sweep.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
PY=./.venv/bin/python; [ -x "$PY" ] || PY=/home/donk/memory-mcp-venv/bin/python
OUT=$(mktemp)
if "$PY" scripts/verify_bazaar_routes.py >"$OUT" 2>&1; then
    echo "$(date -Is) OK $(tail -1 "$OUT")"
    rm -f "$OUT"
else
    echo "$(date -Is) FAIL — paid discovery surface is invalid"
    cat "$OUT"
    cat "$OUT" > logs/bazaar_guard.fail
    rm -f "$OUT"
    exit 1
fi
