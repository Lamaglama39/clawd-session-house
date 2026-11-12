# 開発

## テスト

```bash
npm test                                   # 下の2つをまとめて実行
python3 -m unittest discover -s tests -v   # サーバー、サブエージェント、履歴、インストーラー、スクリプト、ファイルの配置のテスト
node --test tests/*.test.js                # Clawd描画、レイアウト、振り返り画面の計算。Node.js 22以上
claude plugin validate .                   # マーケットプレイスの定義を検証
claude plugin validate plugin              # プラグインの定義を検証
```

## デモ

Claude Codeを起動しなくても、ダミーのセッションで表示を確認できます。

```bash
python3 plugin/scripts/demo.py                # 4セッションが状態を変え続ける。Ctrl+Cで終了
python3 plugin/scripts/demo.py --sessions 9   # 9セッション
python3 plugin/scripts/demo.py --static 6     # サブエージェントやタスクを含む6セッションを、固定の状態で作って終了
```

> [!WARNING]
> 既定の送信先は普段使いの`http://127.0.0.1:8765/hook`なので、デモのイベントも振り返りの記録に残ります。普段使いのサーバーを汚したくない場合は、別のポートでサーバーを起動し、`--url`で送信先を指定してください。

```bash
CLAUDE_STATUS_PORT=9000 ./plugin/scripts/start-server.sh --no-history
python3 plugin/scripts/demo.py --static 6 --url http://127.0.0.1:9000/hook
CLAUDE_STATUS_PORT=9000 ./plugin/scripts/stop-server.sh
```

`--static`で作ったタイルは動いているセッションとして扱われるため「×」は出ません。サーバーを再起動すると消えます。

## ダミーの記録で試す

振り返り画面の見た目は、ダミーの記録を別のファイルに作って確かめられます。保存先に普段の記録の`data/history.sqlite3`を指定すると、書き込まずに終了します。

```bash
python3 plugin/scripts/demo.py --seed-history /tmp/demo-history.sqlite3 --days 30
CLAUDE_STATUS_PORT=9000 ./plugin/scripts/start-server.sh --db /tmp/demo-history.sqlite3
# http://127.0.0.1:9000/history.html を開く。終わったら CLAUDE_STATUS_PORT=9000 ./plugin/scripts/stop-server.sh
```

## プラグインとして試す

### 1つのセッションだけで読み込む

リポジトリの`plugin/`を、そのままプラグインとして読み込めます。インストールはしないので、そのセッションだけで有効です。
手動でもフックを登録している環境では、イベントが2回届きます。`--setting-sources project`を付けて、ユーザー設定のフックを読み込まないようにしてください。

```bash
claude --plugin-dir plugin --setting-sources project
```

そのセッションで起動したサーバーは、セッションが終わっても動き続けます。動いている間は、手動で登録したフックのイベントもこのサーバーに届き、記録は`~/.claude/plugins/data/clawd-session-house-inline/`に入ります。試し終わったら止めてください。

```bash
CLAUDE_PLUGIN_DATA=~/.claude/plugins/data/clawd-session-house-inline ./plugin/scripts/stop-server.sh
./plugin/scripts/start-server.sh   # 手動で入れている場合は、普段のサーバーを起動し直す
```

### インストールして試す

GitHubを通さずに、手元のリポジトリをプラグインとしてインストールできます。リポジトリの直下で次を実行し、新しいセッションを始めます。

```bash
python3 plugin/scripts/install-hooks.py --uninstall   # 手動でフックを入れている場合だけ
claude plugin marketplace add "$PWD"
claude plugin install clawd-session-house@lamaglama39
```

- プラグインは、このフォルダの`plugin/`からそのまま読み込まれます。フォルダの変更は、次のセッションか`/reload-plugins`から反映されます。
- `plugin/web/`の変更は、ブラウザを再読み込みすれば反映されます。`plugin/server/`を変えたときは、`/clawd-session-house:restart`でサーバーを起動し直します。
- 記録の保存先は、GitHubから入れた場合と同じ`~/.claude/plugins/data/clawd-session-house-lamaglama39/`です。
- インストールしたままフォルダを移したり名前を変えたりすると、動かなくなります。外してから入れ直してください。

外すときは、`/clawd-session-house:stop`でサーバーを止めてから、アンインストールしてマーケットプレイスの登録も外します。

```bash
claude plugin uninstall clawd-session-house@lamaglama39   # 記録を残すときは--keep-dataを付ける
claude plugin marketplace remove lamaglama39
```

## 新しい版を出す

Claude Codeは、`plugin/.claude-plugin/plugin.json`の`version`で更新を見分けます。`version`を上げずに変更をpushしても、利用者が更新したときに新しい版は届きません。
手順は、公式ドキュメントの[Release a new version](https://code.claude.com/docs/en/plugins/host-marketplace#release-a-new-version)に沿っています。

1. `plugin/.claude-plugin/plugin.json`の`version`を上げます。例えば`0.1.0`を`0.2.0`にします。
   - 版は`plugin.json`だけに書き、`.claude-plugin/marketplace.json`には書きません。両方に書くと`plugin.json`の値が使われ、`claude plugin validate`が食い違いを報告します。
2. テストと検証を通します。マーケットプレイスとプラグインの両方を、`--strict`で検証します。`--strict`を付けると、警告も失敗として扱います。

   ```bash
   npm test
   claude plugin validate --strict .
   claude plugin validate --strict plugin
   ```

3. `main`にpushします。

利用者は、READMEの[更新](../README.md#更新)の手順で新しい版を入れます。サードパーティのマーケットプレイスは自動更新が既定でオフなので、自動更新を有効にしていない利用者には、自分で更新するまで届きません。
[インストールして試す](#インストールして試す)の方法で入れた場合は、`version`に関係なく、手元のフォルダの内容がそのまま読み込まれます。

## プラグインの実装メモ

- `plugin/skills/*/SKILL.md`のスキルは、本文の`` !`コマンド` ``で`plugin/scripts/`のスクリプトを実行します。
  - `${CLAUDE_PLUGIN_DATA}`は本文の中で置き換わるだけで、環境変数には入りません。そのため、`CLAUDE_PLUGIN_DATA=...`を前に付けてスクリプトに渡します。
  - 同じコマンドを`allowed-tools`で許可しています。許可が無いと、何も実行されずに終わります。
  - `restart`と`stop`は`disable-model-invocation`にして、ユーザーが呼んだときだけ動くようにしています。
- プラグインを更新すると、版ごとにインストール先のフォルダが変わり、古い版のサーバーが動き続けます。そこで`start-server.sh`は、起動したフォルダを`run/server-<ポート>.root`に記録します。
  - 動いているサーバーのフォルダが消えているか、Claude Codeが古い版に付ける`.orphaned_at`という印があれば、そのサーバーを止めて新しい版で起動し直します。
  - 印のない別の版は入れ替えません。更新前のセッションで`/clear`などをしたときに、古い版へ戻らないようにするためです。
- リポジトリの直下はマーケットプレイスで、プラグインは`plugin/`です。`marketplace.json`の`source`を`./plugin`にしているので、利用者の手元にコピーされるのは`plugin/`の中身だけです。
  - 開発用のファイルは`plugin/`の外に置きます。`tests/test_plugin_layout.py`が、`plugin/`に`CLAUDE.md`、`.claude/`、`tests/`、`package.json`、ロックファイルなどが無いことを確かめます。
  - `plugin/`の直下に足してよいのは、利用者に必要なものだけです。足すときは、`test_plugin_layout.py`の`SHIPPED`にも足します。
  - `plugin/`にシンボリックリンクを置いて直下のフォルダを指す方法は使えません。`claude plugin validate`がリンク先を読まずに失敗します。
  - `LICENSE`と`NOTICE`は、GitHubで表示されるよう直下に置き、`plugin/`にも同じものを置きます。同じ内容であることもテストで確かめます。
  - 手動で入れた場合の`data/`と`run/`は、`plugin/`の1つ上のリポジトリの直下に置きます。`plugin/`へ移す前と同じ場所です。

## ファイル構成

```text
.
├── README.md             利用者向けの手順書
├── LICENSE               ソースコードのMITライセンス
├── NOTICE                Clawdの権利表記と、MITライセンスの対象外にしたファイル
├── SECURITY.md           脆弱性の報告方法
├── docs/                 仕様、設定と運用、困ったとき、開発。images/にREADMEのスクリーンショット
├── .claude-plugin/       マーケットプレイスの定義marketplace.json。sourceは./plugin
├── plugin/               プラグイン本体。利用者の手元にコピーされるのはここだけ
│   ├── .claude-plugin/   プラグインの定義plugin.json
│   ├── LICENSE / NOTICE  直下と同じもの
│   ├── skills/           プラグインのスキルopen・restart・stop。本文の!コマンドでscripts/を実行する
│   ├── hooks/
│   │   ├── hooks.json        プラグインとして入れたときのフックの登録内容
│   │   └── send-event.sh     フック入力をサーバーへ転送
│   ├── server/
│   │   ├── server.py         状態サーバー。フック受信、SSE配信、API、画面の配信
│   │   └── history.py        振り返り用のSQLiteの記録と集計
│   ├── scripts/
│   │   ├── start-server.sh   サーバーをバックグラウンド起動
│   │   ├── stop-server.sh    サーバー停止
│   │   ├── open-dashboard.sh サーバーを起動してブラウザでダッシュボードを開く
│   │   ├── install-hooks.py  手動で入れる場合の、settings.jsonへのフック登録と削除
│   │   └── demo.py           ダミーセッションでのプレビュー、ダミー履歴の生成
│   └── web/
│       ├── index.html / style.css / app.js   ライブ表示
│       ├── favicon.svg       Clawdのドット絵のファビコン
│       ├── clawd.js          Clawdのポーズ計算とドット絵の描画
│       ├── agents.js         タイル内のサブエージェントの列
│       ├── notify.js         タブ名の件数表示とデスクトップ通知
│       ├── app-lib.js        ライブ表示の計算。タイルの配置、見出し、経過時間の書式、通知の判定
│       ├── history.html / history.css / history.js   振り返り画面
│       └── history-lib.js    振り返り画面の書式・目盛り・タイムラインの計算
├── tests/                Python unittest / node:test
├── data/                 手動で入れた場合の振り返りの記録。.gitignore済み
└── run/                  手動で入れた場合のPID・ログ。.gitignore済み
```
