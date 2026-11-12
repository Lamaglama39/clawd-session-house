---
name: restart
description: Clawd Session Houseのサーバーを起動し直す。プラグインを更新したあとや、サーバーのコードを変えたあとに使う。
disable-model-invocation: true
allowed-tools:
  - Bash(CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" "${CLAUDE_PLUGIN_ROOT}/scripts/stop-server.sh")
  - Bash(CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" "${CLAUDE_PLUGIN_ROOT}/scripts/start-server.sh")
---

```!
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" "${CLAUDE_PLUGIN_ROOT}/scripts/stop-server.sh" 2>&1 || true
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" "${CLAUDE_PLUGIN_ROOT}/scripts/start-server.sh" 2>&1 || true
```

上の出力は、サーバーを止めてから起動した結果です。結果を短い1文でユーザーに伝えてください。
開いているダッシュボードは自動でつながり直し、動いているセッションのタイルは次のイベントで戻ることも伝えてください。
エラーが出ている場合は内容を説明し、出力にあるログファイルの場所を示してください。自分で再試行はしないでください。
伝えるときは、ユーザーが会話で使っている言語を使ってください。
