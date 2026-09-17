#!/usr/bin/env bash
# run_memory_mcp.sh — start the memory-mcp x402 surface on donk (127.0.0.1:8407).
#
# WHY THIS FILE EXISTS (2026-09-18):
# /etc is mounted read-only on this box, so a systemd unit cannot be installed.
# This script + an @reboot user-crontab line is the durable start path.
#
# CRITICAL: the ASGI target MUST be `memory_server:app`, not `memory_server:fapp`.
# `app` is the wrapper that routes /mcp* to the MCP StreamableHTTPSessionManager;
# starting `fapp` alone serves the HTTP/paid/well-known surface but 404s the whole
# MCP surface — i.e. the exact endpoint listed on the MCP registry goes dark.
#
# Idempotent: if something is already listening on PORT, it exits 0 without touching it.

set -u
REPO=/home/donk/memory-mcp
VENV=/home/donk/memory-mcp-venv
PORT=8407
LOG=$REPO/logs/memory-mcp.log

mkdir -p "$REPO/logs"
cd "$REPO" || exit 1

if ss -ltn 2>/dev/null | grep -q "127.0.0.1:$PORT"; then
    echo "already listening on 127.0.0.1:$PORT — nothing to do"
    exit 0
fi

export PYTHONUNBUFFERED=1
setsid nohup "$VENV/bin/uvicorn" memory_server:app \
    --host 127.0.0.1 --port "$PORT" --log-level warning \
    >> "$LOG" 2>&1 < /dev/null &

for _ in $(seq 1 20); do
    sleep 1
    if curl -sf -m 3 "http://127.0.0.1:$PORT/health" > /dev/null; then
        echo "memory-mcp up on $PORT (memory_server:app)"
        exit 0
    fi
done

echo "FAILED to come up on $PORT — see $LOG" >&2
exit 1
