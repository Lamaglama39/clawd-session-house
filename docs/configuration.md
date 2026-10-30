# 設定と運用

## サーバーの起動と停止

サーバーは、Claude Codeのセッションが始まったときに自動で起動します。
プラグインで入れた場合は、[スキル](../README.md#使い方)でダッシュボードを開いたり、サーバーを起動し直したり止めたりできます。

手動で入れた場合は、リポジトリの直下で次のスクリプトを使います。

| 操作                                                           | コマンド                                     |
| -------------------------------------------------------------- | -------------------------------------------- |
| サーバーを起動する。起動済みなら何もしない                     | `./scripts/start-server.sh`                  |
| サーバーを止める                                               | `./scripts/stop-server.sh`                   |
| ダッシュボードを開く。サーバーが止まっていれば起動してから開く | `./scripts/open-dashboard.sh`                |
| 状態をJSONで確認する                                           | `curl -s http://127.0.0.1:8765/api/sessions` |

既定以外のポートで動かしているときは、`CLAUDE_STATUS_PORT=9000 ./scripts/stop-server.sh`のように、スクリプトの前でポートを指定します。

## 設定

設定は、環境変数か`server.py`のオプションで指定します。

- 環境変数は、どちらの入れ方でも使えます。プラグインで入れた場合は、`~/.claude/settings.json`の`env`に書くと、フックと、フックから起動するサーバーに渡ります。
- `server.py`のオプションは、`./scripts/start-server.sh --history-days 90`のように、`start-server.sh`に付けて起動したときだけ効きます。フックから自動で起動したサーバーには付きません。

| 項目                                     | 環境変数                | `server.py`のオプション | 既定値      |
| ---------------------------------------- | ----------------------- | ----------------------- | ----------- |
| ポート                                   | `CLAUDE_STATUS_PORT`    | `--port`                | `8765`      |
| 待ち受けアドレス                         | `CLAUDE_STATUS_HOST`    | `--host`                | `127.0.0.1` |
| 終了したセッションのタイルを残す時間     | なし                    | `--ended-linger 秒`     | `60`        |
| イベントが無いセッションを消すまでの時間 | なし                    | `--expire-hours 時間`   | `12`        |
| PID・ログの置き場所                      | `CLAUDE_STATUS_RUN_DIR` | なし                    | `run/`      |

プラグインで入れた場合、PID・ログの置き場所の既定は`~/.claude/plugins/data/clawd-session-house-lamaglama39/run/`です。

### ポートを変える

サーバーとフックで同じポートを使うよう、両方を変えます。

プラグインで入れた場合は、`~/.claude/settings.json`の`env`に書きます。フックと、フックから起動するサーバーの両方に渡ります。

```json
{ "env": { "CLAUDE_STATUS_PORT": "9000" } }
```

手動で入れた場合は、フックを登録し直してからサーバーを起動します。

```bash
python3 scripts/install-hooks.py --port 9000
CLAUDE_STATUS_PORT=9000 ./scripts/open-dashboard.sh
```

## 手動で入れた場合のフックの登録

`scripts/install-hooks.py`は、`~/.claude/settings.json`にフックを登録します。

- 書き込む前に、`settings.json.bak-YYYYmmdd-HHMMSS`というバックアップを作ります。
- 追加や置き換えをするのは、コマンドの末尾に`# clawd-session-house`が付いた、このスクリプトのエントリだけです。ほかのフックや設定は変えません。
- 何度実行しても同じ結果になります。
- `SessionStart`のフックには`--autostart`が付き、サーバーが動いていなければ起動します。自動で起動させたくないときは、`--no-autostart`を付けて登録します。
- 特定のプロジェクトだけで使うときは、`--settings <プロジェクト>/.claude/settings.local.json`のように登録先を指定します。
- 登録したフックは、起動中のセッションにも反映されます。そのセッションのタイルは、プロンプトの送信などの次のイベントから表示されます。

## 振り返りの記録

| 項目       | 環境変数           | `server.py`のオプション | 既定値                 |
| ---------- | ------------------ | ----------------------- | ---------------------- |
| 保存先     | `CLAUDE_STATUS_DB` | `--db パス`             | `data/history.sqlite3` |
| 保持期間   | なし               | `--history-days 日数`   | `180`                  |
| 記録しない | なし               | `--no-history`          | 記録する               |

プラグインで入れた場合、保存先の既定は`~/.claude/plugins/data/clawd-session-house-lamaglama39/history.sqlite3`です。
記録する内容は、[仕様](specification.md#記録する内容)を参照してください。

### 記録を消す

サーバーを止めてから、データベースのファイル`history.sqlite3`を消します。同じ場所にある`history.sqlite3-wal`と`history.sqlite3-shm`も消します。

```bash
# 手動で入れた場合
./scripts/stop-server.sh
rm -f data/history.sqlite3*
./scripts/start-server.sh
```

プラグインで入れた場合は、`/clawd-session-house:stop`でサーバーを止め、次のコマンドでファイルを消してから、`/clawd-session-house:restart`で起動します。

```bash
rm -f ~/.claude/plugins/data/clawd-session-house-lamaglama39/history.sqlite3*
```

## 他の端末から見る

同じLAN内のタブレットや別のPCから見るときは、LANからの接続を受け付けるようにサーバーを起動し直します。フックから自動で起動したサーバーは、既定では`127.0.0.1`でしか待ち受けないためです。

```bash
# 手動で入れた場合
./scripts/stop-server.sh
./scripts/start-server.sh --host 0.0.0.0
```

プラグインで入れた場合は、`~/.claude/settings.json`の`env`に`"CLAUDE_STATUS_HOST": "0.0.0.0"`を書き、新しいセッションで`/clawd-session-house:restart`を実行します。

その端末のブラウザで`http://<サーバーを動かしているPCのIPアドレス>:8765/`を開きます。
macOSのファイアウォールが有効な場合は、Pythonへの着信接続を許可するか確認されることがあります。

> [!WARNING]
> サーバーには認証がありません。LANからの接続を受け付けるのは、信頼できるLAN内だけにしてください。フォルダ名やセッション名を含む振り返りの記録も、同じLANから見えるようになります。

手動で入れた場合は、サーバーを別のマシンで動かすこともできます。Claude Codeを使うマシンで、送信先を指定してフックを登録します。このとき、サーバーの自動起動は無効になります。

```bash
python3 scripts/install-hooks.py --url http://192.168.1.10:8765/hook
```
