#!/usr/bin/env bash
# morning_call.sh — wrapper for the daily 7 AM Pipecat/Twilio call.
# Called by the launchd agent com.timbre.morningcall.
# Never run this manually unless you want to actually place a call.

set -euo pipefail

PROJECT="/Users/node3/projects/voice_fun"
LOGFILE="${PROJECT}/logs/morning_call.log"
PYTHON="${PROJECT}/.venv/bin/python"
SCRIPT="${PROJECT}/src/run_morning_call.py"

# Make sure cloudflared (in /opt/homebrew/bin) and system tools are on PATH.
export PATH="/opt/homebrew/bin:/usr/bin:/bin:$PATH"

# Create logs/ if it somehow doesn't exist yet.
mkdir -p "${PROJECT}/logs"

# Append a timestamped header so each run is easy to find in the log.
{
  echo ""
  echo "========================================"
  echo "morning_call run: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "========================================"
} >> "${LOGFILE}"

# Run the call script, tee both stdout and stderr into the log.
cd "${PROJECT}"
exec "${PYTHON}" "${SCRIPT}" >> "${LOGFILE}" 2>&1
