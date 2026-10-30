/**
 * @file Subagent strip shown under the main Clawd of a session tile.
 */

import { ClawdView, Scene } from "./clawd.js";
import { t } from "./i18n.js";
import { visibleAgents } from "./app-lib.js";

/** Fallback width of one strip item used to decide how many fit [px]. */
const ITEM_MIN_WIDTH = 120;
const MAX_ITEMS = 4;

/**
 * Manages the subagent items of one tile: small Clawds that walk while the
 * subagent works, look troubled while it waits for approval, and sleep once done.
 */
export class AgentStrip {
  /**
   * @param {HTMLElement} root  The `.tile-agents` container.
   */
  constructor(root) {
    this.root = root;
    /** @type {Map<string, {el: HTMLElement, view: ClawdView, agent: object, refs: object}>} */
    this.items = new Map();
    this.more = document.createElement("span");
    this.more.className = "agent-more";
    this.shown = [];
  }

  /**
   * Sync the DOM with the session's subagents.
   * @param {object[]|undefined} agents
   */
  update(agents) {
    // The width per item depends on the placement (right of the room or below), so use the CSS value.
    // A hidden element cannot be measured, so decide the visibility first and then measure
    this.root.hidden = !Array.isArray(agents) || agents.length === 0;
    const { shown, hidden } = visibleAgents(agents, this.capacity());
    this.root.hidden = shown.length === 0;
    const keep = new Set(shown.map((a) => a.id));
    for (const [id, item] of this.items) {
      if (!keep.has(id)) {
        item.el.remove();
        this.items.delete(id);
      }
    }
    shown.forEach((agent, i) => {
      let item = this.items.get(agent.id);
      if (!item) {
        item = createItem();
        this.items.set(agent.id, item);
      }
      item.agent = agent;
      fillItem(item);
      if (this.root.children[i] !== item.el) {
        this.root.insertBefore(item.el, this.root.children[i] || null);
      }
    });
    this.more.textContent = hidden > 0 ? `+${hidden}` : "";
    this.more.title = hidden > 0 ? t("ほかに{n}件のサブエージェント", { n: hidden }) : "";
    if (hidden > 0) this.root.appendChild(this.more);
    else this.more.remove();
    this.shown = shown;
  }

  /**
   * Number of items that fit in the strip's current layout.
   * @returns {number}
   */
  capacity() {
    const itemMin =
      parseFloat(getComputedStyle(this.root).getPropertyValue("--agent-item-min")) ||
      ITEM_MIN_WIDTH;
    const width = this.root.clientWidth || this.root.parentElement?.clientWidth || 0;
    return Math.max(1, Math.min(MAX_ITEMS, Math.floor(width / itemMin)));
  }

  /**
   * Draw every visible mini Clawd for the current frame.
   * @param {number} now  Server time [ms].
   */
  render(now) {
    for (const agent of this.shown) {
      const item = this.items.get(agent.id);
      if (!item) continue;
      const { scene, since } = sceneOf(agent);
      item.view.render(scene, now - since);
    }
  }
}

function createItem() {
  const el = document.createElement("div");
  el.className = "agent";
  const stage = document.createElement("div");
  stage.className = "agent-stage";
  const canvas = document.createElement("canvas");
  stage.appendChild(canvas);
  const text = document.createElement("div");
  text.className = "agent-text";
  const head = document.createElement("span");
  head.className = "agent-type";
  const mark = document.createElement("span");
  mark.className = "mark";
  const type = document.createElement("span");
  type.className = "agent-name";
  head.append(mark, type);
  const sub = document.createElement("span");
  sub.className = "agent-sub";
  text.append(head, sub);
  el.append(stage, text);
  // They stand in the parent's room, so leave the background unpainted and align the feet
  const view = new ClawdView(canvas, { transparent: true, anchor: "bottom" });
  return { el, view, agent: null, refs: { type, sub } };
}

function fillItem(item) {
  const a = item.agent;
  const state = a.state === "done" ? "done" : a.waiting ? "wait" : "running";
  item.el.dataset.agentState = state;
  setText(item.refs.type, a.type || "agent");
  let sub;
  if (state === "done") sub = t("完了");
  else if (state === "wait")
    sub = a.tool ? t("{tool} の承認待ち", { tool: a.tool }) : t("承認待ち");
  else sub = a.tool || t("起動中");
  setText(item.refs.sub, sub);
  // Descriptions do not fit in the tile, so show the full text on hover
  const lines = [a.type || "agent"];
  if (a.description) lines.push(a.description);
  lines.push(t("ツール実行 {n}回", { n: a.toolCount || 0 }));
  item.el.title = lines.join("\n");
}

function sceneOf(agent) {
  if (agent.state === "done") return { scene: Scene.END, since: agent.endedAt || agent.startedAt };
  if (agent.waiting) return { scene: Scene.APPROVAL, since: agent.startedAt };
  return { scene: Scene.PROCESSING, since: agent.startedAt };
}

/** Assign textContent only when it changed to avoid needless layout work. */
export function setText(el, text) {
  if (el.textContent !== text) el.textContent = text;
}
