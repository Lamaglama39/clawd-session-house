# clawd-session-house

複数のClaude Codeセッションの状態をブラウザに表示するダッシュボードで、`plugin/`がClaude Codeのプラグインです。
このファイルには、このリポジトリで作業するときの約束を書きます。利用者向けの説明は`README.md`、仕組みと仕様は`docs/specification.md`、開発の手順は`docs/development.md`にあります。

## 構成

- `plugin/`: プラグイン本体。利用者の手元にコピーされるのはここだけ。
  - `hooks/send-event.sh`: フックの入力JSONを`POST /hook`でサーバーへ転送する。
  - `server/server.py`: 状態サーバー。セッションの状態管理（`SessionStore`）、SSEの配信、API、画面の配信。
  - `server/history.py`: 振り返り用のSQLiteへの記録と集計。
  - `web/`: ライブ表示と振り返りの画面。ビルドなしのES Modulesで、ファイルがそのまま配信される。DOMに触れない計算は`app-lib.js`、`history-lib.js`、`clawd.js`に置き、Node.jsでテストする。
  - `scripts/`: サーバーの起動と停止、手動で入れる場合のフックの登録、デモ。
  - `skills/`、`hooks/hooks.json`、`.claude-plugin/plugin.json`: プラグインとして利用者の環境で動く部品。
- `.claude-plugin/marketplace.json`: マーケットプレイスの定義。`source`は`./plugin`。
- `tests/`: Pythonの`unittest`と`node:test`。
- `docs/`、`README.md`: 文書。
- `CLAUDE.md`、`.claude/`、`package.json`、`ruff.toml`、`.prettierrc.json`: 開発用。

## プラグインに開発用のものを混ぜない

- 開発用のファイル（スキル、サブエージェント、設定、メモ、テスト、ツールの設定）は、`plugin/`の外に置く。開発用のスキルとサブエージェントは`.claude/skills/`と`.claude/agents/`に置く。
- `plugin/`の直下に足してよいのは、利用者に必要なものだけ。足すときは、`tests/test_plugin_layout.py`の`SHIPPED`にも足す。
- `plugin/`の中に、シンボリックリンクと、`CLAUDE.md`、`package.json`、ロックファイルを置かない。`tests/test_plugin_layout.py`が確かめる。
- `plugin/`の中の次の場所は、プラグインの部品として利用者の環境で読み込まれる。必要なものだけを置く。
  - `skills/`、`commands/`、`agents/`、`hooks/hooks.json`、`output-styles/`、`themes/`、`workflows/`、`monitors/`、`bin/`
  - `plugin/`の直下の`settings.json`、`.mcp.json`、`.lsp.json`
- `plugin/skills/`の3つのスキルは利用者向けの機能なので、開発の都合で変えない。
- `LICENSE`と`NOTICE`は、直下と`plugin/`に同じものを置く。片方を直したら、もう片方も同じにする。

## コマンド

| 目的                      | コマンド                                                                              |
| ------------------------- | ------------------------------------------------------------------------------------- |
| すべての検証              | `npm run check`。テスト、lint、整形の確認、マーケットプレイスとプラグインの定義の検証 |
| テスト                    | `npm test`                                                                            |
| 画面のテスト              | `npm run test:ui`。スクリーンショットを`.tmp/screenshots/<日時>/`に保存する           |
| lintと整形の確認          | `npm run lint`。ruff、prettier、shellcheck、shfmt                                     |
| 整形                      | `npm run format`                                                                      |
| Pythonのテストを1ファイル | `python3 -m unittest discover -s tests -p 'test_server.py' -v`                        |
| Nodeのテストを1ファイル   | `node --test tests/app-lib.test.js`                                                   |

必要なもの: Python 3.9以上、Node.js 22以上、`uv`（lintと整形のツールと、画面のテストのPlaywrightを、`uv run`で版を固定して実行する）、Google Chrome（画面のテスト）、Claude Code。

ツールの版は、公開から7日以上たったものだけを使う。直下の`uv.toml`と`.npmrc`で、それより新しい版は取得されない。

- ツールを足したり版を上げたりするときは、`package.json`に公開から7日以上たった版を書く。取得できないときに、設定を外したり上書きしたりしない。
- `uvx`は使わない。リポジトリの`uv.toml`を読まないため、`uv run --isolated --no-project --with`を使う。`--isolated`が無いと、手元のPythonに入っているパッケージまでツールから見えてしまう。

## 壊してはいけない約束

- `plugin/hooks/send-event.sh`は、何があっても標準出力に何も出さず、`exit 0`で終わる。フックの出力と終了コードは、Claude Codeの権限判断に影響するため。
- サーバーはPythonの標準ライブラリだけで動かす。Python 3.9で動く書き方にする。
- `plugin/web/`は依存パッケージもビルドも使わない。
- プロンプトの本文、ツールの入力と結果、サブエージェントの指示文と最終回答は、保存も表示もしない。記録する項目は`docs/specification.md`の「記録する内容」の表のとおり。
- サーバーの守りを外さない。待ち受けは既定で`127.0.0.1`、`Host`ヘッダーの検査、`POST /hook`は`Content-Type: application/json`だけ、全応答に`Content-Security-Policy: frame-ancestors 'none'`。
- 画面の文言を足したり変えたりしたら、`plugin/web/i18n.js`の英訳も直す。日本語の文言がそのままキーになっている。
- フックのイベント、matcher、timeoutは、`plugin/hooks/hooks.json`と`plugin/scripts/install-hooks.py`の`HOOK_EVENTS`で揃える。`tests/test_install_hooks.py`が突き合わせる。
- 次の名前、形、置き場所は、利用者の設定や記録が引き継がれなくなるので変えない。
  - 環境変数`CLAUDE_STATUS_*`、ブラウザの保存キー`claude-status.*`、`install-hooks.py`の目印`# clawd-session-house`
  - 手動で入れた場合の`data/`と`run/`は、リポジトリの直下（`plugin/`の1つ上）
  - SQLiteのスキーマ。変える必要があるときは、`SCHEMA_VERSION`を上げて既存の記録を移す処理を用意し、先にユーザーに相談する。
- 動作を変えたら、`README.md`と`docs/`の該当箇所も直す。文書に書いた数値は、コードの定数と合わせる。

## 普段使いの環境に触れない

開発者自身もこのダッシュボードを普段使いしています。検証で、普段使いのサーバーと記録に触れないでください。

- 普段使いのサーバーは`8765`番。手で試すときは、別のポートと一時フォルダを使う。手順は`verify`スキルにある。
- `plugin/scripts/demo.py`の既定の送信先は`8765`番。必ず`--url`でテスト用のサーバーを指定する。
- `data/`、`run/`、`~/.claude/plugins/data/clawd-session-house-*/`は実際の利用記録。読まない、消さない、書き換えない。
- `~/.claude/settings.json`を書き換えない。`install-hooks.py`を試すときは、`--settings`で一時ファイルを指定する。
- テストは空きポートと一時フォルダを使い、`CLAUDE_STATUS_*`と`CLAUDE_PLUGIN_*`の環境変数を外して動かしている。新しいテストも同じようにする。

## テストの書き方

- Python: `unittest`。時刻は`tests/test_server.py`の`FakeClock`のように、差し替えられる時計を渡す。サーバーは`("127.0.0.1", 0)`で起動する。
- JavaScript: `node:test`と`node:assert/strict`。日本語の出力を確かめるテストは、`setLang("ja")`で言語を固定する。
- 不具合を直すときは、先に不具合を再現するテストを書き、失敗することを確かめてから直す。
- 既存のテストを消したり、期待値を実装に合わせて書き換えたりしない。仕様を変えるためにテストを直すときは、どのテストをなぜ変えたかを報告する。
- リファクタリングでは、テストを変えずに全件通ることを確かめる。

## 作業の流れ

機能追加やリファクタリングを頼まれたら、短い依頼でも次の順に進めます。

1. `git status`で作業ツリーがきれいか確かめる。未コミットの変更があれば、どう扱うかユーザーに聞く。
2. 最新の`main`から作業用のブランチを作る。`main`の上で直接変更しない。
3. 変更が届く範囲を洗い出す。呼び出し元、テスト、`docs/`、`plugin/web/i18n.js`の英訳、`plugin/hooks/hooks.json`と`install-hooks.py`。
4. 小さく変え、そのたびに関係するテストを実行する。
5. 終わったら`verify`スキルで検証する。
6. 結果を報告する。通した検証、足したテスト、確かめていないことを分けて書く。
7. コミットする前に、ユーザーに確認する。

## ブランチとコミット

- ブランチ名は、種類と英小文字・ハイフンの要約にする。
  - `feature/<要約>`: 機能追加
  - `fix/<要約>`: 不具合の修正
  - `refactor/<要約>`: 動作を変えない整理
  - `docs/<要約>`: 文書だけの変更
- 1つのコミットには1つの変更を入れる。メッセージは既存の履歴に合わせ、英語の命令形で1行にする。例: `Add the history page`
- `main`へ直接pushしない。強制push、`git reset --hard`、`git clean`はしない。
- PRは`main`に向けて作る。本文には、主な変更、テスト（通したもの、足したもの、実行方法）、関連タスク、その他を書く。
- `plugin/.claude-plugin/plugin.json`の`version`は、機能のブランチでは上げない。版を上げると利用者に届くので、リリースのときにまとめて上げる。手順は`docs/development.md`の「新しい版を出す」にある。

## 完了の条件

- `npm run check`が通る。
- 変えた動作と直した不具合に、テストがある。
- 画面を変えたら、`npm run test:ui`が通り、撮ったスクリーンショットで見た目を確かめた。
- `README.md`、`docs/`、英訳が実装と合っている。
- 開発用のファイルを`plugin/`に置いていない。
