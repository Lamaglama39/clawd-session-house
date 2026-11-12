#!/bin/bash
# ============================================================================
#  open-dashboard.sh
#  Starts the server with start-server.sh if it is not running, then opens the dashboard.
#
#  Usage:  plugin/scripts/open-dashboard.sh
#  The port can be set with the CLAUDE_STATUS_PORT environment variable (default: 8765).
# ============================================================================

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${CLAUDE_STATUS_PORT:-8765}"
URL="http://127.0.0.1:$PORT/"

"$DIR/start-server.sh" --port "$PORT" || exit 1

if command -v open >/dev/null 2>&1; then
  open "$URL" && echo "opened $URL"
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 && echo "opened $URL"
else
  echo "open $URL in your browser"
fi
