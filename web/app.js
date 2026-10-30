/**
 * @file Dashboard controller: receives session snapshots over SSE and renders tiles.
 */

import { AgentStrip, setText } from "./agents.js";
import { ClawdView, Scene, START_DURATION_MS } from "./clawd.js";
import { applyI18n, t } from "./i18n.js";
import {
  agentsBeside,
  arrange,
  backgroundSummary,
  blockSpan,
  buildLabels,
  chooseGrid,
  formatElapsed,
  groupByFolder,
  moveTile,
  splitUnits,
  taskSummary,
} from "./app-lib.js";
import { Notifier } from "./notify.js";

/** State word -> scene, badge label, and color class. */
const STATE_INFO = {
  START: { scene: Scene.START, label: t("起動"), cls: "idle" },
  IDLE: { scene: Scene.IDLE, label: t("待機中"), cls: "idle" },
  BUSY: { scene: Scene.PROCESSING, label: t("処理中"), cls: "busy" },
  WAIT: { scene: Scene.APPROVAL, label: t("承認待ち"), cls: "wait" },
  OFF: { scene: Scene.END, label: t("終了"), cls: "off" },
};

const GRID_GAP = 16;
// Group frames are drawn in the gaps, so widen the gaps when groups exist to keep frames apart
const GROUP_GAP = 24;
const CURSOR_HIDE_MS = 3000;
const TEXT_REFRESH_MS = 250;
const ORDER_KEY = "claude-status.order";

applyI18n();
const grid = document.getElementById("grid");
const empty = document.getElementById("empty");
const template = document.getElementById("tile-template");
const conn = document.getElementById("conn");
const counters = {
  wait: document.querySelector('[data-count="wait"]'),
  busy: document.querySelector('[data-count="busy"]'),
  idle: document.querySelector('[data-count="idle"]'),
  agents: document.querySelector('[data-count="agents"]'),
};
const notifier = new Notifier(document.getElementById("notify"));

/** @type {Map<string, {el: HTMLElement, view: ClawdView, session: object, refs: object}>} */
const tiles = new Map();
let order = [];
/** @type {object[][]} Sessions grouped by folder, in display order. */
let groups = [];
/** @type {Map<string, HTMLElement>} Frame element per folder with two or more sessions. */
const boxes = new Map();
// Order set by dragging. Sessions never reordered follow at the end in start-time order
let saved = readOrder();
// Sessions in start-time order. Heading numbers (#1, #2) follow it and survive reordering
let current = [];
// Server time minus browser time, so elapsed times are computed against the server clock
let clockOffset = 0;
let lastTextRefresh = 0;
const emptyCanvas = document.getElementById("empty-clawd");
const emptyView = new ClawdView(emptyCanvas, { transparent: true, anchor: "bottom" });
let emptyFloor = 0;
const emptyStartedAt = performance.now();
// When a tile resizes, whether subagents fit on the right side changes too
const resizer = new ResizeObserver(() => placeAgents(tiles.values()));

const serverNow = () => Date.now() + clockOffset;

/**
 * Decide what a session currently looks like, including the START -> IDLE switch.
 * @param {object} s  Session from the snapshot.
 * @param {number} now  Server time [ms].
 */
function viewOf(s, now) {
  const info = STATE_INFO[s.state] || STATE_INFO.IDLE;
  const elapsed = now - s.since;
  const shown = s.state === "START" && elapsed > START_DURATION_MS ? STATE_INFO.IDLE : info;
  return { info, shown, elapsed };
}

/**
 * Human-readable detail line for a session.
 * @param {object} s
 */
function detailOf(s) {
  const tool = s.tool;
  if (s.state === "BUSY" && tool === "Agent" && s.event !== "PostToolUse") {
    return t("サブエージェント実行中");
  }
  if (s.state === "IDLE" && s.event !== "StopFailure") {
    // Show that work continues in the background even while the parent waits for input
    const agents = runningAgents(s);
    if (agents > 0) return t("入力待ち（サブエージェント{n}件が作業中）", { n: agents });
    const bg = backgroundSummary(s.background).count;
    if (bg > 0) return t("入力待ち（バックグラウンド{n}件）", { n: bg });
  }
  switch (s.state) {
    case "START":
      return t("セッション開始");
    case "WAIT": {
      // When the approval wait is inside a subagent, name that subagent
      const waiting = (s.agents || []).find((a) => a.waiting && a.state !== "done");
      if (waiting)
        return t("{type} が {tool} の承認待ち", {
          type: waiting.type,
          tool: waiting.tool || t("ツール"),
        });
      if (s.event === "Elicitation" || (s.event === "Notification" && !tool))
        return t("ユーザーの操作待ち");
      return tool ? t("{tool} の承認待ち", { tool }) : t("承認待ち");
    }
    case "BUSY":
      if (!tool) return t(s.event === "UserPromptSubmit" ? "プロンプトを処理中" : "処理中");
      if (s.event === "PostToolUse") return t("{tool} 完了", { tool });
      if (s.event === "PostToolUseFailure") return t("{tool} 失敗", { tool });
      if (s.event === "PermissionDenied") return t("{tool} は自動拒否", { tool });
      return t("{tool} 実行中", { tool });
    case "OFF":
      return t("セッション終了");
    default:
      return t(s.event === "StopFailure" ? "APIエラーで停止" : "入力待ち");
  }
}

/** Number of subagents still working in a session. */
function runningAgents(s) {
  return (s.agents || []).filter((a) => a.state !== "done").length;
}

/**
 * Create the DOM for one session tile.
 * @param {object} s
 */
function createTile(s) {
  const el = template.content.firstElementChild.cloneNode(true);
  el.dataset.id = s.id;
  const refs = {
    name: el.querySelector(".tile-name"),
    suffix: el.querySelector(".tile-suffix"),
    folder: el.querySelector(".tile-folder"),
    badge: el.querySelector(".badge"),
    elapsed: el.querySelector(".tile-elapsed"),
    detail: el.querySelector(".tile-detail"),
    mode: el.querySelector(".tile-mode"),
    tasks: el.querySelector(".tile-tasks"),
    bg: el.querySelector(".tile-bg"),
  };
  const tile = {
    el,
    // Leave the background unpainted so the room light shows through, and keep the feet on the floor
    view: new ClawdView(el.querySelector(".tile-stage canvas"), {
      transparent: true,
      anchor: "bottom",
    }),
    strip: new AgentStrip(el.querySelector(".tile-agents")),
    session: s,
    refs,
    floor: 0,
  };
  el.querySelector(".tile-dismiss").addEventListener("click", () => {
    // The x only shows on ended tiles, but a click during a state change must not delete a live session
    if (tile.session.state !== "OFF") return;
    fetch(`api/sessions/${encodeURIComponent(tile.session.id)}`, { method: "DELETE" }).catch(
      () => {},
    );
  });
  resizer.observe(el);
  return tile;
}

/**
 * Put each tile's subagents beside the parent Clawd when the parent keeps most
 * of its size there, and in a row under the room otherwise.
 * @param {Iterable<object>} list  Tiles to lay out.
 */
function placeAgents(list) {
  const targets = [...list].filter((t) => t.el.classList.contains("has-agents"));
  if (targets.length === 0) return;
  // Measure the parent and item count for both placements; switch all tiles first so layout runs twice
  const measure = (mode) => {
    for (const t of targets) t.el.dataset.agents = mode;
    return targets.map((t) => ({
      width: t.view.canvas.clientWidth,
      height: t.view.canvas.clientHeight,
      items: Math.min(t.strip.capacity(), (t.session.agents || []).length),
    }));
  };
  const below = measure("below");
  const beside = measure("beside");
  targets.forEach((t, i) => {
    t.el.dataset.agents = agentsBeside(beside[i], below[i]) ? "beside" : "below";
    // The width per item depends on the placement, so recompute how many fit
    t.strip.update(t.session.agents);
  });
}

/**
 * Refresh the text parts of a tile (cheap, but not needed every frame).
 * @param {object} tile
 * @param {{name: string, suffix: string, folder: string}} label
 * @param {number} now
 */
function updateTileText(tile, label, now) {
  const s = tile.session;
  const { shown, elapsed } = viewOf(s, now);
  const r = tile.refs;
  tile.el.dataset.state = shown.cls;
  setText(r.name, label.name);
  setText(r.suffix, label.suffix);
  setText(r.folder, label.folder);
  // The full name, working directory, and session ID of a truncated heading show on hover
  r.name.title = [s.title, s.cwd, t("セッションID {id}", { id: s.id.slice(0, 8) })]
    .filter(Boolean)
    .join("\n");
  setText(r.badge, shown.label);
  setElapsed(r.elapsed, formatElapsed(elapsed));
  setText(r.detail, detailOf(s));
  const mode = s.mode && s.mode !== "default" ? s.mode : "";
  r.mode.hidden = !mode;
  setText(r.mode, mode);

  const tasks = taskSummary(s.tasks);
  r.tasks.hidden = !tasks;
  if (tasks) {
    setText(r.tasks, tasks.text);
    r.tasks.title = tasks.current
      ? t("作業中のタスク: {task}", { task: tasks.current })
      : t("すべて完了");
    r.tasks.dataset.complete = String(s.tasks.done === s.tasks.total);
  }
  const bg = backgroundSummary(s.background);
  r.bg.hidden = bg.count === 0;
  if (bg.count) {
    setText(r.bg, `BG ${bg.count}`);
    r.bg.title = t("バックグラウンド作業: {text}", { text: bg.text });
  }
  tile.strip.update(s.agents);
  const hasAgents = !tile.strip.root.hidden;
  if (hasAgents !== tile.el.classList.contains("has-agents")) {
    tile.el.classList.toggle("has-agents", hasAgents);
    if (hasAgents) placeAgents([tile]);
    else delete tile.el.dataset.agents;
  }
}

/**
 * Show an elapsed time such as "3分05秒" with the units in smaller spans.
 * @param {HTMLElement} el
 * @param {string} text
 */
function setElapsed(el, text) {
  if (el.dataset.text === text) return;
  el.dataset.text = text;
  el.replaceChildren(
    ...splitUnits(text).map((part) => {
      if (!part.unit) return document.createTextNode(part.text);
      const unit = document.createElement("span");
      unit.className = "unit";
      unit.textContent = part.text;
      return unit;
    }),
  );
}

/**
 * Pass the floor height computed by a ClawdView to CSS (only when it changed).
 * @param {HTMLElement} el  Element that receives --floor-h.
 * @param {number} prev
 * @param {number} next
 * @returns {number}
 */
function syncFloor(el, prev, next) {
  if (Math.abs(prev - next) < 0.25) return prev;
  el.style.setProperty("--floor-h", `${next}px`);
  return next;
}

/** Recompute the grid so that every tile is as large as possible. */
function relayout() {
  const hasTiles = order.length > 0;
  grid.hidden = !hasTiles;
  empty.hidden = hasTiles;
  if (!hasTiles) return;
  const sizes = groups.map((g) => g.length);
  const gap = sizes.some((n) => n > 1) ? GROUP_GAP : GRID_GAP;
  grid.style.setProperty("--gap", `${gap}px`);
  const rect = grid.getBoundingClientRect();
  const { cols, rows } = chooseGrid(sizes, rect.width - gap * 2, rect.height - gap * 2, gap);
  grid.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
  grid.style.gridTemplateRows = `repeat(${rows}, minmax(0, 1fr))`;
  for (const g of groups) {
    if (g.length < 2) continue;
    const span = blockSpan(g.length, cols);
    const box = boxes.get(g[0].cwd);
    box.style.gridColumn = `span ${span.cols}`;
    box.style.gridRow = `span ${span.rows}`;
  }
}

/**
 * Put an element at a position among its parent's children (only when it is not there yet).
 * @param {HTMLElement} parent
 * @param {HTMLElement} el
 * @param {number} i
 */
function placeAt(parent, el, i) {
  if (parent.children[i] !== el) parent.insertBefore(el, parent.children[i] || null);
}

/**
 * Apply a snapshot from the server.
 * @param {{now: number, sessions: object[]}} snap
 */
function applySnapshot(snap) {
  clockOffset = snap.now - Date.now();
  render(snap.sessions);
}

/**
 * Create, remove, and arrange tiles for the given sessions.
 * @param {object[]} sessions  Sessions in start time order.
 */
function render(sessions) {
  current = sessions;
  const seen = new Set();
  for (const s of sessions) {
    seen.add(s.id);
    let tile = tiles.get(s.id);
    if (!tile) {
      tile = createTile(s);
      tiles.set(s.id, tile);
    }
    tile.session = s;
  }
  for (const [id, tile] of tiles) {
    if (!seen.has(id)) {
      resizer.unobserve(tile.el);
      tile.el.remove();
      tiles.delete(id);
    }
  }

  // Follow the dragged order and gather each folder's sessions at its first tile; frame groups of 2+
  groups = groupByFolder(arrange(sessions, saved));
  order = groups.flat().map((s) => s.id);
  const blocks = groups.map((g) => {
    if (g.length === 1) return tiles.get(g[0].id).el;
    let box = boxes.get(g[0].cwd);
    if (!box) {
      box = document.createElement("div");
      box.className = "group";
      boxes.set(g[0].cwd, box);
    }
    g.forEach((s, i) => placeAt(box, tiles.get(s.id).el, i));
    return box;
  });
  blocks.forEach((el, i) => placeAt(grid, el, i));
  for (const [key, box] of boxes) {
    if (!blocks.includes(box)) {
      box.remove();
      boxes.delete(key);
    }
  }
  const hasTitles = sessions.some((s) => s.title);
  // A folder-name line appearing or disappearing changes the room height at the same tile size
  const titlesChanged = hasTitles !== grid.classList.contains("has-titles");
  grid.classList.toggle("has-titles", hasTitles);

  relayout();
  if (titlesChanged) placeAgents(tiles.values());
  refreshText(serverNow());
  notifier.update(sessions, buildLabels(sessions));
}

/** Read the arranged order of session ids saved in this browser. */
function readOrder() {
  try {
    const ids = JSON.parse(localStorage.getItem(ORDER_KEY));
    return Array.isArray(ids) ? ids : [];
  } catch {
    return [];
  }
}

/** Save the arranged order of session ids in this browser. */
function writeOrder(ids) {
  try {
    localStorage.setItem(ORDER_KEY, JSON.stringify(ids));
  } catch {
    // Even if saving fails, keep the order while this page stays open
  }
}

/** Let tiles be reordered by dragging one onto another. */
function setupDrag() {
  let dragged = null;
  let marked = null;
  const tileOf = (e) => e.target.closest(".tile");
  const mark = (el) => {
    if (marked === el) return;
    marked?.classList.remove("drop-target");
    marked = el;
    el?.classList.add("drop-target");
  };
  grid.addEventListener("dragstart", (e) => {
    dragged = tileOf(e);
    if (!dragged) return;
    e.dataTransfer.effectAllowed = "move";
    // Firefox does not start a drag unless some data is set
    e.dataTransfer.setData("text/plain", "");
    dragged.classList.add("dragging");
  });
  grid.addEventListener("dragover", (e) => {
    const el = tileOf(e);
    if (!dragged || !el) return mark(null);
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (el === dragged) return mark(null);
    // Over a tile of another group the whole group moves, so highlight the entire target group
    const box = el.closest(".group");
    mark(box && !box.contains(dragged) ? box : el);
  });
  grid.addEventListener("drop", (e) => {
    e.preventDefault();
    const el = tileOf(e);
    if (!dragged || !el || el === dragged) return;
    const ids = groups.map((g) => g.map((s) => s.id));
    saved = moveTile(ids, dragged.dataset.id, el.dataset.id);
    writeOrder(saved);
    render(current);
  });
  grid.addEventListener("dragend", () => {
    dragged?.classList.remove("dragging");
    dragged = null;
    mark(null);
  });
}

/** Update every tile's text and the header counters. */
function refreshText(now) {
  const labels = buildLabels(current);
  const totals = { wait: 0, busy: 0, idle: 0, agents: 0 };
  for (const id of order) {
    const tile = tiles.get(id);
    updateTileText(tile, labels.get(id), now);
    const cls = viewOf(tile.session, now).shown.cls;
    if (cls in totals) totals[cls]++;
    totals.agents += runningAgents(tile.session);
  }
  for (const [key, el] of Object.entries(counters)) {
    setText(el, String(totals[key]));
    el.parentElement.dataset.zero = String(totals[key] === 0);
  }
}

/** Animation loop: draw every Clawd each frame and refresh text periodically. */
function frame(ts) {
  const now = serverNow();
  if (order.length === 0) {
    emptyView.render(Scene.END, ts - emptyStartedAt);
    emptyFloor = syncFloor(emptyCanvas.parentElement, emptyFloor, emptyView.floorPx);
  } else {
    for (const id of order) {
      const tile = tiles.get(id);
      const { info, elapsed } = viewOf(tile.session, now);
      tile.view.render(info.scene, elapsed);
      tile.floor = syncFloor(tile.el, tile.floor, tile.view.floorPx);
      tile.strip.render(now);
    }
  }
  if (ts - lastTextRefresh >= TEXT_REFRESH_MS) {
    lastTextRefresh = ts;
    refreshText(now);
  }
  requestAnimationFrame(frame);
}

/** Connect to the server-sent event stream (EventSource reconnects by itself). */
function connect() {
  const es = new EventSource("events");
  es.addEventListener("open", () => {
    conn.dataset.conn = "open";
  });
  es.addEventListener("error", () => {
    conn.dataset.conn = "error";
  });
  es.addEventListener("snapshot", (e) => {
    try {
      applySnapshot(JSON.parse(e.data));
    } catch (err) {
      console.error("invalid snapshot", err);
    }
  });
}

/** Toggle browser fullscreen (requires a user gesture). */
function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  else document.documentElement.requestFullscreen().catch(() => {});
}

function setupInteraction() {
  document.getElementById("topbar").addEventListener("dblclick", (e) => {
    // A quick double press on a button or link is not a double-click on the header
    if (!e.target.closest("button, a")) toggleFullscreen();
  });
  document.addEventListener("keydown", (e) => {
    if ((e.key === "f" || e.key === "F") && !e.metaKey && !e.ctrlKey && !e.altKey)
      toggleFullscreen();
  });
  // Hide the cursor when idle, only while viewing full screen (never in a normal window)
  let hideTimer = 0;
  const showCursor = () => {
    document.body.classList.remove("hide-cursor");
    clearTimeout(hideTimer);
    if (!document.fullscreenElement) return;
    hideTimer = setTimeout(() => document.body.classList.add("hide-cursor"), CURSOR_HIDE_MS);
  };
  document.addEventListener("mousemove", showCursor);
  document.addEventListener("fullscreenchange", showCursor);
  window.addEventListener("resize", relayout);
}

setupInteraction();
setupDrag();
relayout();
connect();
requestAnimationFrame(frame);
