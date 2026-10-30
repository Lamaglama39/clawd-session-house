#!/bin/bash
# ============================================================================
#  start-server.sh
#  Starts server.py in the background. Does nothing if a server already responds.
#  After starting, waits up to 3 seconds for the health check to pass.
#
#  Usage:  scripts/start-server.sh [server.py options...]
#    e.g.:  scripts/start-server.sh --port 9000
#  Environment variables:
#    CLAUDE_STATUS_PORT     port (default: 8765)
#    CLAUDE_STATUS_RUN_DIR  where the PID and log files go (default: run/ at the repository root,
#                           or $CLAUDE_PLUGIN_DATA/run/ when called from the plugin hooks)
#
#  PID and log files are kept per port as run/server-<port>.pid / .log, so a server
#  on another port is never mistaken for a stale PID file and stopped.
# ============================================================================

# DIR is the root of the repository (plugin). run/ and data/ live here too
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="${CLAUDE_STATUS_RUN_DIR:-${CLAUDE_PLUGIN_DATA:-$DIR}/run}"
mkdir -p "$RUN"

# If --port is given, use it for the health check URL and the PID file as well
PORT="${CLAUDE_STATUS_PORT:-8765}"
ARGS=("$@")
for ((i = 0; i < ${#ARGS[@]}; i++)); do
  case "${ARGS[$i]}" in
  --port) PORT="${ARGS[$((i + 1))]}" ;;
  --port=*) PORT="${ARGS[$i]#--port=}" ;;
  esac
done

PIDFILE="$RUN/server-$PORT.pid"
LOG="$RUN/server-$PORT.log"
# Directory the running server was started from. It changes with each plugin version
ROOTFILE="$RUN/server-$PORT.root"
OLD_ROOT="$(cat "$ROOTFILE" 2>/dev/null)"
OLD_ROOT="${OLD_ROOT:-$DIR}"
HEALTH_URL="http://127.0.0.1:$PORT/healthz"

healthy() {
  curl --silent --fail --noproxy '*' --max-time 1 -o /dev/null "$HEALTH_URL"
}

# The PID may have been reused by another process, so check its command line
is_our_server() {
  ps -p "$1" -o command= 2>/dev/null | grep -qF "$OLD_ROOT/server/server.py"
}

# A plugin update leaves the old version's server running. Claude Code marks old versions with
# .orphaned_at and deletes them after 14 days, so replace the server when that mark appears.
# Unmarked versions are still used by older sessions; replacing them would flip between versions
outdated() {
  [ "$OLD_ROOT" != "$DIR" ] && { [ ! -d "$OLD_ROOT" ] || [ -e "$OLD_ROOT/.orphaned_at" ]; }
}

# Even if the PID file is stale, treat the server as running when HTTP responds
if healthy; then
  outdated || exit 0
  # Even for an old version, keep using it unless the PID file proves it is ours
  OLD_PID="$(cat "$PIDFILE" 2>/dev/null)"
  { [ -n "$OLD_PID" ] && is_our_server "$OLD_PID"; } || exit 0
fi

# Clean up a process left without responding, or an old version's server, before starting
if [ -f "$PIDFILE" ]; then
  OLD_PID="$(cat "$PIDFILE" 2>/dev/null)"
  if [ -n "$OLD_PID" ] && is_our_server "$OLD_PID"; then
    kill -TERM "$OLD_PID" 2>/dev/null
    # Wait up to 2 seconds for the port to free up (force-kill if an SSE connection keeps it alive)
    for _ in $(seq 1 20); do
      kill -0 "$OLD_PID" 2>/dev/null || break
      sleep 0.1
    done
    kill -0 "$OLD_PID" 2>/dev/null && kill -9 "$OLD_PID" 2>/dev/null
  fi
  rm -f "$PIDFILE"
fi

# nohup + & + disown detach the server so it outlives the Claude Code session that started it
nohup python3 -u "$DIR/server/server.py" --port "$PORT" "$@" </dev/null >>"$LOG" 2>&1 &
echo $! >"$PIDFILE"
echo "$DIR" >"$ROOTFILE"
disown 2>/dev/null

for _ in $(seq 1 30); do
  healthy && echo "started (port $PORT, pid $(cat "$PIDFILE"))" && exit 0
  sleep 0.1
done

echo "server did not become healthy; see $LOG" >&2
exit 1
