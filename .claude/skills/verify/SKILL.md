---
name: verify
description: このリポジトリの変更を検証する。テスト、lint、整形、プラグインの定義の検証をまとめて実行し、変えた場所に応じた確認も行う。コードや文書を変えたあと、作業の完了を報告する前、コミットやPRを作る前に使う。
---

# 変更の検証

## 1. まとめて検証する

```bash
npm run check
```

テスト（`node:test`と`unittest`）、lint（ruff、shellcheck）、整形の確認（ruff、prettier、shfmt）、マーケットプレイス（直下）とプラグイン（`plugin/`）の定義の`claude plugin validate --strict`を順に実行します。

失敗したときは、次のように扱います。

- 整形の差分だけなら、`npm run format`で整形してから、もう一度`npm run check`を実行する。
- テストが落ちたら、実装を直す。テストを消したり、期待値を実装に合わせて書き換えたりしない。仕様の変更でテストを直すときは、どのテストをなぜ変えたかを報告する。
- `uvx`や`npx`がツールを取得できないときは、実行できなかった検証を報告する。通ったことにしない。

## 2. 変えた場所ごとの確認

| 変えた場所                                                    | 追加の確認                                                                                                                                            |
| ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `plugin/server/`、`plugin/scripts/*.py`、`tests/*.py`         | Python 3.9でも通るか: `uv run --no-project --python 3.9 python -m unittest discover -s tests`                                                         |
| `plugin/web/`                                                 | `npm run test:ui`を実行し、`.tmp/screenshots/<日時>/`の画像を見る。テストに無い操作は、下の手順でテスト用のポートのサーバーを使って確かめる           |
| `plugin/web/`の文言                                           | 足した日本語の文言に、`plugin/web/i18n.js`の英訳がある。「JA / EN」で切り替えて表示を確かめる                                                         |
| `plugin/hooks/`、`plugin/scripts/install-hooks.py`            | `plugin/hooks/hooks.json`と`HOOK_EVENTS`が揃っている。`send-event.sh`がサーバーが止まっていても何も出さず`exit 0`で終わる                             |
| `plugin/skills/`、`.claude-plugin/`、`plugin/.claude-plugin/` | `claude plugin validate --strict .`と`claude plugin validate --strict plugin`。スキルの動作は、ユーザーに下の「プラグインとして試す」で確かめてもらう |
| `README.md`、`docs/`                                          | リンク先の見出しがある。書いた数値がコードの定数と合っている                                                                                          |
| 新しいファイル                                                | 開発用のファイルを`plugin/`に置いていない。`plugin/`の直下に足したら、`tests/test_plugin_layout.py`の`SHIPPED`にも足した                              |

### 画面のテストとスクリーンショット

```bash
npm run test:ui
```

- 空いているポートにテスト用のサーバーを立て、手元のGoogle Chromeで、ライブ表示と振り返り画面を複数の幅と英語で開きます。横にはみ出さないこと、タイルの「×」の2回押し、ページ送りなどを確かめます。
- スクリーンショットは`.tmp/screenshots/<日時>/`に保存されます。最後に表示される保存先の画像を開き、見た目を確かめる。
- Playwrightは`uv`が版を固定して用意するので、手元に入れる必要はありません。普段の`npm test`では、このテストは飛ばされます。
- 画面に操作を足したら、`tests/test_ui.py`にも確かめる処理を足す。

### ブラウザで表示を確かめる

テストに無い操作を手で試すときの手順です。普段使いの`8765`番のサーバーには触れず、`18765`番で試します。シェルの状態はコマンドの間で引き継がれないので、毎回`CLAUDE_STATUS_PORT`と`CLAUDE_STATUS_RUN_DIR`を付けます。

```bash
CLAUDE_STATUS_PORT=18765 CLAUDE_STATUS_RUN_DIR=.tmp/run-verify ./plugin/scripts/start-server.sh --no-history
python3 plugin/scripts/demo.py --static 6 --url http://127.0.0.1:18765/hook
# http://127.0.0.1:18765/ を開いて確かめる。claude-in-chromeが使えるならそれで開く
CLAUDE_STATUS_PORT=18765 CLAUDE_STATUS_RUN_DIR=.tmp/run-verify ./plugin/scripts/stop-server.sh
rm -rf .tmp/run-verify
```

- 状態が動き続ける表示を見るときは、`--static 6`の代わりに`--sessions 4`を付けて実行し、終わったらCtrl+Cで止める。
- 振り返り画面は、ダミーの記録を一時ファイルに作り、`--no-history`の代わりに`--db`で指定して起動する。

  ```bash
  python3 plugin/scripts/demo.py --seed-history .tmp/run-verify/history.sqlite3 --days 30
  CLAUDE_STATUS_PORT=18765 CLAUDE_STATUS_RUN_DIR=.tmp/run-verify ./plugin/scripts/start-server.sh --db .tmp/run-verify/history.sqlite3
  # http://127.0.0.1:18765/history.html を開く
  ```

- 幅は1920、1100、420程度で、はみ出しや重なりがないかを見る。
- 終わったら、必ずテスト用のサーバーを止める。

### プラグインとして試す

スキルやフックの定義を変えたときに、ユーザーに頼む手順です。Claudeは実行せず、コマンドを伝えます。

```bash
CLAUDE_STATUS_PORT=18765 claude --plugin-dir plugin --setting-sources project
```

- `--setting-sources project`で、ユーザー設定のフックとインストール済みのプラグインを読み込まないようにする。
- 試し終わったら、サーバーを止めてもらう。

  ```bash
  CLAUDE_STATUS_PORT=18765 CLAUDE_PLUGIN_DATA=~/.claude/plugins/data/clawd-session-house-inline ./plugin/scripts/stop-server.sh
  ```

## 3. 報告する

次を分けて報告します。

- 実行した検証と結果。テストの件数も書く。
- 足したテストと、変えたテスト。変えた理由も書く。
- 確かめていないこと。ブラウザで見ていない、Python 3.9で試していない、など。
