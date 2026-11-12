/**
 * @file Tab-title badge and optional desktop notifications for waiting/finished sessions.
 */

import { t } from "./i18n.js";
import { detectAlerts, formatElapsed } from "./app-lib.js";

const STORAGE_KEY = "claude-status.notify";
const BASE_TITLE = "Clawd Session House";
/** Only turns at least this long produce a "finished" notification [ms]. */
const MIN_TURN_MS = 60_000;

/**
 * Keeps the tab title in sync with the number of sessions waiting for approval and,
 * when enabled, shows a desktop notification for new waits and long finished turns.
 */
export class Notifier {
  /**
   * @param {HTMLButtonElement} button  Toggle button in the header.
   */
  constructor(button) {
    this.button = button;
    this.prev = new Map();
    this.enabled = readSetting() && permission() === "granted";
    button.addEventListener("click", () => this.toggle());
    this.renderButton();
  }

  async toggle() {
    if (this.enabled) {
      this.enabled = false;
    } else if (permission() === "granted") {
      this.enabled = true;
    } else if (permission() === "default") {
      // The permission prompt can only be shown from a user action (a click)
      this.enabled = (await Notification.requestPermission()) === "granted";
    }
    writeSetting(this.enabled);
    this.renderButton();
  }

  renderButton() {
    const p = permission();
    this.button.setAttribute("aria-pressed", String(this.enabled));
    this.button.disabled = p === "denied" || p === "unsupported";
    if (p === "unsupported") {
      this.button.textContent = t("通知 非対応");
      this.button.title = t("このブラウザはデスクトップ通知に対応していません");
    } else if (p === "denied") {
      this.button.textContent = t("通知 ブロック中");
      this.button.title = t("ブラウザのサイト設定で通知を許可すると使えます");
    } else {
      this.button.textContent = t(this.enabled ? "通知 オン" : "通知 オフ");
      this.button.title = t("承認待ちになったときと、1分以上の処理が終わったときに通知します");
    }
  }

  /**
   * Handle a new snapshot.
   * @param {object[]} sessions
   * @param {Map<string, {name: string, suffix: string}>} labels
   */
  update(sessions, labels) {
    const waiting = sessions.filter((s) => s.state === "WAIT").length;
    document.title =
      waiting > 0 ? t("({n}) 承認待ち | {title}", { n: waiting, title: BASE_TITLE }) : BASE_TITLE;

    const { alerts, next } = detectAlerts(this.prev, sessions, MIN_TURN_MS);
    this.prev = next;
    // No notification while the page is visible (do not repeat what is already on screen)
    if (!this.enabled || (document.visibilityState === "visible" && document.hasFocus())) return;
    for (const alert of alerts) {
      const label = labels.get(alert.session.id);
      // Labels come from the same snapshot, so a missing one is unusual; fall back to the folder name
      const name = label
        ? `${label.name}${label.suffix ? ` ${label.suffix}` : ""}`
        : alert.session.project;
      if (alert.kind === "wait") {
        const tool = alert.session.tool;
        show(
          t("{name} が承認待ちです", { name }),
          tool ? t("{tool} の実行に承認が必要です", { tool }) : "",
          alert,
        );
      } else {
        show(
          t("{name} の処理が終わりました", { name }),
          t("処理時間 {d}", { d: formatElapsed(alert.turnMs) }),
          alert,
        );
      }
    }
  }
}

function show(title, body, alert) {
  try {
    // Notifications for one session replace each other, so they do not pile up in the notification center.
    // A replacement is silent by default, so renotify alerts every time
    const n = new Notification(title, {
      body,
      tag: `${alert.kind}:${alert.session.id}`,
      renotify: true,
    });
    n.onclick = () => {
      window.focus();
      n.close();
    };
  } catch {
    // Do nothing where notifications are unavailable
  }
}

function permission() {
  return typeof Notification === "undefined" ? "unsupported" : Notification.permission;
}

function readSetting() {
  try {
    return localStorage.getItem(STORAGE_KEY) === "on";
  } catch {
    return false;
  }
}

function writeSetting(on) {
  try {
    localStorage.setItem(STORAGE_KEY, on ? "on" : "off");
  } catch {
    // Stays enabled while this page is open, even if saving fails
  }
}
