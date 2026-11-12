#!/bin/bash
# ============================================================================
#  send-event.sh  (called from every Claude Code hook)
#  POSTs the hook input JSON read from stdin to the status server as is.
#
#  Usage:  send-event.sh [--autostart]
#    --autostart  start the server with start-server.sh first if it is not running
#                 (meant for the SessionStart hook only)
#
#  Prints nothing to stdout and exits 0 whatever happens, so the hook output and
#  exit code never affect Claude Code's permission decisions or display.
#  The destination can be changed with environment variables:
#    CLAUDE_STATUS_PORT  port of the local server (default: 8765)
#    CLAUDE_STATUS_URL   full destination URL (for example, a server on another machine)
# ============================================================================

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
URL="${CLAUDE_STATUS_URL:-http://127.0.0.1:${CLAUDE_STATUS_PORT:-8765}/hook}"

# stdin can be read only once, so read all of it before waiting for the autostart
PAYLOAD="$(cat)"

if [ "$1" = "--autostart" ]; then
  "$DIR/scripts/start-server.sh" </dev/null >/dev/null 2>&1
fi

# --noproxy keeps proxy settings out of the way, and the response wait is capped at 2 seconds
printf '%s' "$PAYLOAD" | curl --silent --noproxy '*' \
  --connect-timeout 1 --max-time 2 \
  -H 'Content-Type: application/json' \
  --data-binary @- -o /dev/null "$URL" >/dev/null 2>&1

exit 0
