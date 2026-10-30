#!/bin/bash
# ============================================================================
#  stop-server.sh
#  Stops the server.py started by start-server.sh.
#  The server is shared by all sessions, so it is not called on SessionEnd; run it by hand.
#
#  Usage:  scripts/stop-server.sh [--port 9000]
#  CLAUDE_STATUS_PORT / CLAUDE_STATUS_RUN_DIR work as in start-server.sh.
#  Only the server on the given port is stopped; servers on other ports are left alone.
# ============================================================================

# DIR is the root of the repository (plugin)
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="${CLAUDE_STATUS_RUN_DIR:-${CLAUDE_PLUGIN_DATA:-$DIR}/run}"

PORT="${CLAUDE_STATUS_PORT:-8765}"
case "$1" in
--port) PORT="$2" ;;
--port=*) PORT="${1#--port=}" ;;
esac
PIDFILE="$RUN/server-$PORT.pid"
# The plugin directory changes with each version, so check against the one the server started from
ROOTFILE="$RUN/server-$PORT.root"
ROOT="$(cat "$ROOTFILE" 2>/dev/null)"

if [ ! -f "$PIDFILE" ]; then
  echo "not running (port $PORT)"
  exit 0
fi

# The PID may have been reused by another process, so check its command line
PID="$(cat "$PIDFILE" 2>/dev/null)"
if [ -n "$PID" ] && ps -p "$PID" -o command= 2>/dev/null | grep -qF "${ROOT:-$DIR}/server/server.py"; then
  kill -TERM "$PID" 2>/dev/null
  # Wait up to 2 seconds (0.1 s x 20)
  for _ in $(seq 1 20); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.1
  done
  # Last resort when an open SSE connection keeps the server from exiting
  kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null
  echo "stopped (port $PORT, pid $PID)"
else
  echo "not running (port $PORT, stale pid file removed)"
fi
rm -f "$PIDFILE" "$ROOTFILE"
exit 0
