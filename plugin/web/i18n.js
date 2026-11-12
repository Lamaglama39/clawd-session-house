/**
 * @file UI language: the Japanese text is the message key, and the English table maps it.
 */

const STORAGE_KEY = "claude-status.lang";
const LANGS = ["ja", "en"];

/** English text for each Japanese message. "{name}" marks a value filled in by t(). */
const EN = {
  // Live view
  ダブルクリックまたはFキーで全画面: "Double-click or press F for full screen",
  起動: "Starting",
  待機中: "Idle",
  処理中: "Working",
  承認待ち: "Needs approval",
  終了: "Ended",
  サブエージェント: "Subagents",
  作業中のサブエージェント: "Subagents at work",
  振り返り: "History",
  表示言語: "Language",
  "サーバーとの接続が切れています。自動で再接続します":
    "Lost the connection to the server. Reconnecting automatically",
  再接続中: "Reconnecting",
  セッションを待っています: "Waiting for sessions",
  "Claude Codeを起動すると、ここにセッションが表示されます":
    "Start Claude Code and its sessions appear here",
  このタイルを消す: "Remove this tile",
  サブエージェント実行中: "Running a subagent",
  "入力待ち（サブエージェント{n}件が作業中）": "Waiting for input ({n} subagents working)",
  "入力待ち（バックグラウンド{n}件）": "Waiting for input ({n} in the background)",
  セッション開始: "Session started",
  "{type} が {tool} の承認待ち": "{type} needs approval for {tool}",
  ツール: "a tool",
  ユーザーの操作待ち: "Waiting for your input",
  "{tool} の承認待ち": "{tool} needs approval",
  プロンプトを処理中: "Processing the prompt",
  "{tool} 完了": "{tool} done",
  "{tool} 失敗": "{tool} failed",
  "{tool} は自動拒否": "{tool} auto-denied",
  "{tool} 実行中": "Running {tool}",
  セッション終了: "Session ended",
  APIエラーで停止: "Stopped by an API error",
  入力待ち: "Waiting for input",
  "セッションID {id}": "Session ID {id}",
  "作業中のタスク: {task}": "Current task: {task}",
  すべて完了: "All done",
  "バックグラウンド作業: {text}": "Background work: {text}",
  "タスク {done}/{total}": "Tasks {done}/{total}",
  "ほかに{n}件のサブエージェント": "{n} more subagents",
  完了: "Done",
  起動中: "Starting",
  "ツール実行 {n}回": "{n} tool calls",
  // Notifications
  "通知 非対応": "Alerts unsupported",
  このブラウザはデスクトップ通知に対応していません:
    "This browser does not support desktop notifications",
  "通知 ブロック中": "Alerts blocked",
  ブラウザのサイト設定で通知を許可すると使えます:
    "Allow notifications in the browser's site settings to use alerts",
  "通知 オン": "Alerts on",
  "通知 オフ": "Alerts off",
  "承認待ちになったときと、1分以上の処理が終わったときに通知します":
    "Notifies you when a session needs approval or finishes work that took over a minute",
  "({n}) 承認待ち | {title}": "({n}) Needs approval | {title}",
  "{name} が承認待ちです": "{name} needs approval",
  "{tool} の実行に承認が必要です": "{tool} needs your approval to run",
  "{name} の処理が終わりました": "{name} finished",
  "処理時間 {d}": "Working time {d}",
  // Times and counts
  "{n}秒": "{n}s",
  "{n}分": "{n}m",
  "{n}時間": "{n}h",
  "{m}分{s}秒": "{m}m {s}s",
  "{h}時間{m}分": "{h}h {m}m",
  "{n}回": "{n}",
  "{n}件": "{n}",
  "{date}({day})": "{date} ({day})",
  その他: "Other",
  // Review
  "振り返り | Clawd Session House": "History | Clawd Session House",
  期間: "Period",
  今日: "Today",
  "7日": "7 days",
  "30日": "30 days",
  "90日": "90 days",
  日付で指定: "Pick dates",
  開始日: "Start date",
  終了日: "End date",
  "〜": "–",
  CSVをダウンロード: "Download CSV",
  ライブ表示: "Live view",
  期間の合計: "Totals for the period",
  日別の作業時間: "Work time by day",
  処理時間と承認待ち時間を1日ごとに積み上げています:
    "Working time and approval wait time, stacked for each day",
  処理時間: "Working time",
  承認待ち時間: "Approval wait",
  表で見る: "Show as a table",
  時間帯別の処理時間: "Working time by hour",
  "期間中の処理時間を、作業した時間帯（0〜23時）ごとに合計しています":
    "Working time in the period, totaled by the hour of day it happened (0-23)",
  プロジェクト別: "By project",
  よく使ったツール: "Most used tools",
  セッション: "Sessions",
  "行を選ぶと、状態の移り変わりとサブエージェントの動きを表示します":
    "Select a row to see its state timeline and subagents",
  開始時刻: "Started",
  セッション名: "Session",
  セッション時間: "Duration",
  プロンプト送信回数: "Prompts",
  ツール実行回数: "Tool calls",
  サブエージェント起動回数: "Subagent runs",
  "履歴を読み込めません。サーバーが --no-history で起動しているか、記録を保存できていません。サーバーのログを確認してから、起動し直してください。プラグインで入れた場合は /clawd-session-house:restart で起動し直せます。":
    "History is unavailable. The server was started with --no-history, or it cannot save records. Check the server log, then restart the server. With the plugin, run /clawd-session-house:restart.",
  "履歴を読み込めませんでした。サーバーが動いているか確認してください。エラー: {msg}":
    "Could not load the history. Check that the server is running. Error: {msg}",
  "平均 {avg} × {n}回": "{avg} on average × {n}",
  承認待ちなし: "No approval waits",
  "プロンプト送信 {n}回": "{n} prompts sent",
  "APIエラーで停止 {n}回": "Stopped by API errors {n} times",
  Claudeが作業していた時間: "Time Claude spent working",
  ツール実行: "Tool calls",
  "承認を求められた回数 {n}回": "{n} approval requests",
  起動した回数: "Times started",
  プロンプト送信: "Prompts sent",
  日付: "Date",
  セッション数: "Sessions",
  "{h}時台": "{h}:00-{h}:59",
  この期間の記録はありません: "No records for this period",
  この期間はサブエージェントを使っていません: "No subagents were used in this period",
  プロジェクト: "Project",
  種類: "Type",
  起動回数: "Runs",
  平均実行時間: "Average duration",
  "詳細を読み込めませんでした（{msg}）": "Could not load the details ({msg})",
  "{label} {time}から{d}": "{label} from {time} for {d}",
  継続中: "ongoing",
  "セッション時間 {d}": "Duration {d}",
  "承認待ち時間 {d}": "Approval wait {d}",
  "終了理由 {reason}": "End reason {reason}",
  状態の移り変わり: "State timeline",
  "サブエージェント（{n}件）": "Subagents ({n})",
  使ったツール: "Tools used",
  "完了したタスク（{n}件）": "Completed tasks ({n})",
};

/** Current UI language ("ja" or "en"). */
export let lang = readLang();

/**
 * Translate a Japanese message and fill in its "{name}" placeholders.
 * Messages without an English entry are shown in Japanese.
 *
 * @param {string} text  Japanese message, which is also the key.
 * @param {Record<string, string|number>} [params]
 * @returns {string}
 */
export function t(text, params = {}) {
  const msg = (lang === "en" && EN[text]) || text;
  return msg.replace(/\{(\w+)\}/g, (m, key) => (key in params ? String(params[key]) : m));
}

/**
 * Change the language for this page only (used by tests; the tabs save and reload).
 * @param {"ja"|"en"} code
 */
export function setLang(code) {
  lang = code;
}

/**
 * Translate the page's fixed text and wire up the language tabs.
 *
 * Elements marked with data-i18n get their text translated, and data-i18n-title /
 * data-i18n-aria-label translate those attributes. Template contents are included.
 */
export function applyI18n() {
  document.documentElement.lang = lang;
  const scopes = [document, ...[...document.querySelectorAll("template")].map((x) => x.content)];
  for (const scope of scopes) {
    for (const el of scope.querySelectorAll("[data-i18n]"))
      el.textContent = t(el.textContent.trim());
    for (const attr of ["title", "aria-label"]) {
      for (const el of scope.querySelectorAll(`[data-i18n-${attr}]`)) {
        el.setAttribute(attr, t(el.getAttribute(attr)));
      }
    }
  }
  for (const button of document.querySelectorAll(".lang button")) {
    button.setAttribute("aria-pressed", String(button.dataset.lang === lang));
    button.addEventListener("click", () => {
      if (button.dataset.lang === lang) return;
      try {
        localStorage.setItem(STORAGE_KEY, button.dataset.lang);
      } catch {
        // shortcut: the language cannot be switched where storage is unavailable. Put it in the URL if needed
        return;
      }
      // Reloading misses nothing, unlike replacing every rendered string
      location.reload();
    });
  }
}

function readLang() {
  try {
    // Read it through window so Node tests do not warn about touching an unset localStorage
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (LANGS.includes(saved)) return saved;
  } catch {
    // When storage is unavailable, follow the browser language
  }
  return globalThis.navigator?.language?.startsWith("ja") ? "ja" : "en";
}
