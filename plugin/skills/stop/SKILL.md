---
name: stop
description: Clawd Session Houseのサーバーを止める。次のClaude Codeのセッションが始まるか、openのスキルを使うと、また起動する。
disable-model-invocation: true
allowed-tools: Bash(CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" "${CLAUDE_PLUGIN_ROOT}/scripts/stop-server.sh")
---

!`CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" "${CLAUDE_PLUGIN_ROOT}/scripts/stop-server.sh" 2>&1 || true`

上の出力は、サーバーを止めた結果です。結果を短い1文でユーザーに伝えてください。
次のClaude Codeのセッションが始まったときか、/clawd-session-house:open を使ったときに、また起動することも伝えてください。
伝えるときは、ユーザーが会話で使っている言語を使ってください。
