---
name: open
description: Clawd Session Houseのダッシュボードを既定のブラウザで開く。サーバーが止まっていれば先に起動する。ユーザーが「セッションハウスを開いて」「Clawd Session Houseを見せて」などと頼んだときに使う。
allowed-tools: Bash(CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" "${CLAUDE_PLUGIN_ROOT}/scripts/open-dashboard.sh")
---

!`CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" "${CLAUDE_PLUGIN_ROOT}/scripts/open-dashboard.sh" 2>&1 || true`

上の出力は、ダッシュボードを開いた結果です。結果を短い1文でユーザーに伝え、URLも書いてください。
エラーが出ている場合は内容を説明し、出力にあるログファイルの場所を示してください。自分で再試行はしないでください。
伝えるときは、ユーザーが会話で使っている言語を使ってください。
