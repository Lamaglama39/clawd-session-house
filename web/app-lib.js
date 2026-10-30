/**
 * @file Pure helpers for the live view: tile layout, labels, elapsed-time formatting,
 * subagent placement, and alert detection (no DOM access).
 */

import { CANVAS_H, CANVAS_W } from "./clawd.js";
import { t } from "./i18n.js";

/** Reference tile size used to compare grid candidates (Clawd + info area). */
export const REF_TILE_W = 220;
export const REF_TILE_H = 300;

/**
 * Cells that a block of tiles occupies: one row when it fits, otherwise full
 * width and as many rows as needed.
 *
 * @param {number} size  Number of tiles in the block.
 * @param {number} cols  Number of grid columns.
 * @returns {{cols: number, rows: number}}
 */
export function blockSpan(size, cols) {
  const w = Math.min(size, cols);
  return { cols: w, rows: Math.ceil(size / w) };
}

/**
 * Number of rows needed to place blocks in order, the way CSS grid auto-placement
 * does it (a block never goes back to fill an earlier gap).
 *
 * @param {number[]} sizes  Number of tiles in each block, in display order.
 * @param {number} cols     Number of grid columns.
 * @returns {number}
 */
export function rowsNeeded(sizes, cols) {
  const used = [];
  const free = (row, col, span) => {
    for (let r = row; r < row + span.rows; r++)
      for (let c = col; c < col + span.cols; c++) if (used[r]?.[c]) return false;
    return true;
  };
  let row = 0;
  let col = 0;
  for (const size of sizes) {
    const span = blockSpan(size, cols);
    for (;;) {
      if (col + span.cols > cols) {
        row++;
        col = 0;
      } else if (free(row, col, span)) break;
      else col++;
    }
    for (let r = row; r < row + span.rows; r++) {
      used[r] ||= [];
      for (let c = col; c < col + span.cols; c++) used[r][c] = true;
    }
    col += span.cols;
  }
  return used.length;
}

/**
 * Choose the grid that makes every tile as large as possible.
 *
 * @param {number[]} sizes  Number of tiles in each block (a folder group is one block), in display order.
 * @param {number} width   Available width [px].
 * @param {number} height  Available height [px].
 * @param {number} gap     Gap between tiles [px].
 * @returns {{cols: number, rows: number, tileW: number, tileH: number}}
 */
export function chooseGrid(sizes, width, height, gap = 0) {
  const count = sizes.reduce((sum, n) => sum + n, 0);
  if (count <= 0) return { cols: 0, rows: 0, tileW: 0, tileH: 0 };
  let best = null;
  for (let cols = 1; cols <= count; cols++) {
    const rows = rowsNeeded(sizes, cols);
    const tileW = (width - gap * (cols - 1)) / cols;
    const tileH = (height - gap * (rows - 1)) / rows;
    if (tileW <= 0 || tileH <= 0) continue;
    const scale = Math.min(tileW / REF_TILE_W, tileH / REF_TILE_H);
    const empty = cols * rows - count;
    // At the same scale, prefer the arrangement with fewer empty cells
    if (
      best === null ||
      scale > best.scale + 1e-9 ||
      (Math.abs(scale - best.scale) <= 1e-9 && empty < best.empty)
    ) {
      best = { cols, rows, tileW, tileH, scale, empty };
    }
  }
  if (best === null) return { cols: 1, rows: count, tileW: 0, tileH: 0 };
  return { cols: best.cols, rows: best.rows, tileW: best.tileW, tileH: best.tileH };
}

/**
 * Format a duration for display, e.g. "12秒", "3分05秒", "1時間02分" ("12s", "3m 05s", "1h 02m" in English).
 *
 * @param {number} ms  Duration in milliseconds (negative values are clamped to 0).
 * @returns {string}
 */
export function formatElapsed(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  if (h > 0) return t("{h}時間{m}分", { h, m: pad(m) });
  if (m > 0) return t("{m}分{s}秒", { m, s: pad(s) });
  return t("{n}秒", { n: s });
}

/**
 * Split a formatted value such as "3分05秒" or "1,106回" into numbers and units,
 * so that the UI can set the units in a smaller size.
 *
 * @param {string} text
 * @returns {{text: string, unit: boolean}[]}
 */
export function splitUnits(text) {
  return String(text)
    .split(/(\d[\d,.]*)/)
    .filter(Boolean)
    .map((part) => ({ text: part, unit: !/^\d/.test(part) }));
}

/**
 * Build display labels for session tiles.
 *
 * The heading is the custom session title when there is one, otherwise the
 * project (folder) name. Tiles whose headings collide are numbered; the number
 * is returned separately so the UI can keep it visible even when a long
 * heading is truncated. When a title is shown, the folder name is returned as
 * a secondary line.
 *
 * @param {{id: string, project: string, title?: string|null}[]} sessions  Sessions in the order
 *   to number them (start time, so a moved tile keeps its number).
 * @returns {Map<string, {name: string, suffix: string, folder: string}>} Session id -> label parts.
 */
export function buildLabels(sessions) {
  const headingOf = (s) => s.title || s.project;
  const totals = new Map();
  for (const s of sessions) {
    const h = headingOf(s);
    totals.set(h, (totals.get(h) || 0) + 1);
  }
  const seen = new Map();
  const labels = new Map();
  for (const s of sessions) {
    const name = headingOf(s);
    let suffix = "";
    if (totals.get(name) > 1) {
      const n = (seen.get(name) || 0) + 1;
      seen.set(name, n);
      suffix = `#${n}`;
    }
    labels.set(s.id, { name, suffix, folder: s.title ? s.project : "" });
  }
  return labels;
}

/**
 * Put sessions in the order the user arranged them. Sessions that were never
 * arranged follow in their given order (start time).
 *
 * @param {{id: string}[]} sessions
 * @param {string[]} saved  Session ids in the arranged order.
 * @returns {object[]}
 */
export function arrange(sessions, saved) {
  const rank = new Map(saved.map((id, i) => [id, i]));
  const at = (s) => rank.get(s.id) ?? saved.length;
  return [...sessions].sort((a, b) => at(a) - at(b));
}

/**
 * Gather sessions that share a working folder. Each group stands where its
 * first session is; a session without a folder is a group of its own.
 *
 * @param {{id: string, cwd?: string}[]} sessions  In display order.
 * @returns {object[][]} Groups in display order.
 */
export function groupByFolder(sessions) {
  const groups = new Map();
  for (const s of sessions) {
    const key = s.cwd || s.id;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(s);
  }
  return [...groups.values()];
}

/**
 * New order after dropping one tile onto another: the moved item takes the
 * place of the one it was dropped on. A tile cannot leave its folder group, so
 * dropping it onto another group's tile moves its whole group.
 *
 * @param {string[][]} groups  Session ids per group, in display order.
 * @param {string} from  Dragged session id.
 * @param {string} to    Session id it was dropped on.
 * @returns {string[]} Session ids in the new order.
 */
export function moveTile(groups, from, to) {
  const move = (list, i, j) => {
    const out = [...list];
    out.splice(j, 0, ...out.splice(i, 1));
    return out;
  };
  const gi = groups.findIndex((g) => g.includes(from));
  const gj = groups.findIndex((g) => g.includes(to));
  if (gi < 0 || gj < 0) return groups.flat();
  if (gi !== gj) return move(groups, gi, gj).flat();
  const g = groups[gi];
  return groups
    .map((other) => (other === g ? move(g, g.indexOf(from), g.indexOf(to)) : other))
    .flat();
}

/**
 * Pick the subagents to show in a tile: running ones first (oldest first), then
 * the most recently finished ones.
 *
 * @param {{state: string, startedAt: number, endedAt: number|null}[]} agents
 * @param {number} max  Maximum number of items to show.
 * @returns {{shown: object[], hidden: number}}
 */
export function visibleAgents(agents, max) {
  const list = Array.isArray(agents) ? agents : [];
  const running = list.filter((a) => a.state !== "done").sort((a, b) => a.startedAt - b.startedAt);
  const done = list.filter((a) => a.state === "done").sort((a, b) => b.endedAt - a.endedAt);
  const ordered = [...running, ...done];
  const limit = Math.max(0, Math.floor(max));
  return { shown: ordered.slice(0, limit), hidden: Math.max(0, ordered.length - limit) };
}

/**
 * Smallest size the parent Clawd may keep, relative to the "below" layout,
 * when its subagents stand beside it in the same room.
 */
export const BESIDE_MIN_SCALE = 0.9;

/**
 * What one subagent layout measured.
 * @typedef {object} AgentsFit
 * @property {number} width   Parent canvas width [px].
 * @property {number} height  Parent canvas height [px].
 * @property {number} items   Number of subagents shown in that layout.
 */

/**
 * Decide whether subagents stand beside the parent Clawd in the room or in a
 * row under the room, from what each layout measured.
 *
 * @param {AgentsFit} beside  Measured with subagents beside the parent Clawd.
 * @param {AgentsFit} below   Measured with subagents in a row under the room.
 * @returns {boolean} true when the parent Clawd keeps at least BESIDE_MIN_SCALE of
 *   its size and no fewer subagents are shown.
 */
export function agentsBeside(beside, below) {
  const fit = ({ width, height }) => Math.max(0, Math.min(width / CANVAS_W, height / CANVAS_H));
  const base = fit(below);
  return base > 0 && fit(beside) >= base * BESIDE_MIN_SCALE && beside.items >= below.items;
}

/**
 * Summarize background work, leaving out subagents (they have their own strip).
 *
 * @param {Record<string, number>|undefined} background  Counts by task type.
 * @returns {{count: number, text: string}}
 */
export function backgroundSummary(background) {
  const entries = Object.entries(background || {}).filter(([type, n]) => type !== "subagent" && n);
  const count = entries.reduce((sum, [, n]) => sum + n, 0);
  return { count, text: entries.map(([type, n]) => `${type} ${n}`).join(", ") };
}

/**
 * Label for the task list progress, or null when the session has no tasks.
 *
 * @param {{total: number, done: number, current: string|null}|undefined} tasks
 * @returns {{text: string, current: string|null}|null}
 */
export function taskSummary(tasks) {
  if (!tasks || !tasks.total) return null;
  return {
    text: t("タスク {done}/{total}", { done: tasks.done, total: tasks.total }),
    current: tasks.current || null,
  };
}

/**
 * Detect state changes worth a desktop notification.
 *
 * A "turn" starts when a session leaves IDLE/START and ends when it returns to
 * IDLE. Only turns that lasted at least ``minTurnMs`` produce a "done" alert.
 * Sessions seen for the first time never alert (so opening the page is quiet).
 *
 * @param {Map<string, {state: string, turnStart: number|null}>} prev  Result of the previous call.
 * @param {{id: string, state: string, since: number}[]} sessions  Current snapshot.
 * @param {number} minTurnMs
 * @returns {{alerts: {kind: "wait"|"done", session: object, turnMs?: number}[], next: Map}}
 */
export function detectAlerts(prev, sessions, minTurnMs) {
  const idle = (state) => state === "IDLE" || state === "START" || state === "OFF";
  const alerts = [];
  const next = new Map();
  for (const s of sessions) {
    const before = prev.get(s.id);
    let turnStart = before ? before.turnStart : null;
    if (before && before.state !== s.state) {
      if (idle(before.state) && !idle(s.state)) turnStart = s.since;
      if (s.state === "WAIT") alerts.push({ kind: "wait", session: s });
      if (s.state === "IDLE" && !idle(before.state) && turnStart !== null) {
        const turnMs = s.since - turnStart;
        if (turnMs >= minTurnMs) alerts.push({ kind: "done", session: s, turnMs });
      }
      if (idle(s.state)) turnStart = null;
    } else if (!before && !idle(s.state)) {
      turnStart = s.since;
    }
    next.set(s.id, { state: s.state, turnStart });
  }
  return { alerts, next };
}
