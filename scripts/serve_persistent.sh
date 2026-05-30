#!/usr/bin/env bash
# serve_persistent.sh — wrapper for the WARM-TUNNEL DAEMON (com.timbre.tunnel).
# Keeps a cloudflared tunnel + uvicorn (twilio_bot:app, :8090) warm and a
# caffeinate assertion held, so the 7 AM call reuses a stable URL.
# This wrapper NEVER places a Twilio call.

set -euo pipefail

PROJECT="/Users/node3/projects/voice_fun"
LOGFILE="${PROJECT}/logs/tunnel_daemon.log"
PYTHON="${PROJECT}/.venv/bin/python"
SCRIPT="${PROJECT}/scripts/serve_persistent.py"

# cloudflared lives in /opt/homebrew/bin; caffeinate in /usr/bin.
export PATH="/opt/homebrew/bin:/usr/bin:/bin:$PATH"

mkdir -p "${PROJECT}/logs"

{
  echo ""
  echo "========================================"
  echo "tunnel daemon (wrapper) start: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "========================================"
} >> "${LOGFILE}"

cd "${PROJECT}"
exec "${PYTHON}" "${SCRIPT}" >> "${LOGFILE}" 2>&1
