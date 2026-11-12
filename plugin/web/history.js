/**
 * @file Review page: fetches /api/history/* and renders stats, charts, and session timelines.
 */

import {
  durationTicks,
  foldOthers,
  formatCount,
  formatDate,
  formatDuration,
  formatTime,
  labelIndices,
  parsePeriod,
  periodDates,
  periodQuery,
  sortRows,
  timelineLayout,
} from "./history-lib.js";
import { applyI18n, t } from "./i18n.js";
import { splitUnits } from "./app-lib.js";

const SVG_NS = "http://www.w3.org/2000/svg";
applyI18n();

const STATE_LABELS = { IDLE: t("待機中"), BUSY: t("処理中"), WAIT: t("承認待ち") };
const SERIES = {
  busy: { cls: "bar-busy", color: "var(--series-busy)" },
  wait: { cls: "bar-wait", color: "var(--series-wait)" },
};
const MAX_BAR = 24;
const GAP = 2;

const main = document.getElementById("main");
const notice = document.getElementById("notice");
const tooltip = document.getElementById("tooltip");
const csvLink = document.getElementById("csv");
const rangeButtons = [...document.querySelectorAll(".range button")];
const datesGroup = document.querySelector(".dates");
const fromInput = document.getElementById("from");
const toInput = document.getElementById("to");

let period = parsePeriod(location.hash);
let summary = null;
let selectedId = null;
let loadToken = 0;
let sessionList = [];
/** @type {{key: number, dir: 1|-1}|null} Column index and direction; null keeps the server order. */
let sessionSort = null;

/** Session table columns: heading, sort value, and whether it is a text column. */
const SESSION_COLUMNS = [
  { label: t("開始時刻"), value: (x) => x.startedAt },
  { label: t("セッション名"), value: sessionName, text: true },
  { label: t("セッション時間"), value: (x) => x.durationMs, num: true },
  { label: t("処理時間"), value: (x) => x.busyMs, num: true },
  { label: t("承認待ち時間"), value: (x) => x.waitMs, num: true },
  { label: t("プロンプト送信回数"), value: (x) => x.prompts, num: true },
  { label: t("ツール実行回数"), value: (x) => x.toolCalls, num: true },
  { label: t("サブエージェント起動回数"), value: (x) => x.subagents, num: true },
];

// ===== Small DOM helpers (values always go in via textContent, never parsed as HTML) =====

function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "style") node.style.cssText = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined) node.append(c);
  return node;
}

function s(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  for (const c of children.flat()) if (c) node.append(c);
  return node;
}

function svgText(x, y, text, anchor = "middle") {
  const t = s("text", { x, y, "text-anchor": anchor });
  t.textContent = text;
  return t;
}

/** Nodes for a value such as "4時間34分", with the units in smaller spans. */
function withUnits(text) {
  return splitUnits(text).map((part) =>
    part.unit ? h("span", { class: "unit", text: part.text }) : document.createTextNode(part.text),
  );
}

// ===== Tooltip =====

/**
 * @param {{x: number, y: number}} at  Viewport position.
 * @param {string} title
 * @param {{value: string, label: string, color?: string}[]} rows
 */
function showTip(at, title, rows) {
  tooltip.replaceChildren(
    h("div", { class: "tt-title", text: title }),
    ...rows.map((r) =>
      h(
        "div",
        { class: "tt-row" },
        r.color ? h("span", { class: "tt-key", style: `--key-color:${r.color}` }) : null,
        h("b", { text: r.value }),
        h("span", { text: r.label }),
      ),
    ),
  );
  tooltip.hidden = false;
  const rect = tooltip.getBoundingClientRect();
  const x = Math.min(at.x + 14, window.innerWidth - rect.width - 8);
  const y = Math.max(8, Math.min(at.y - rect.height - 10, window.innerHeight - rect.height - 8));
  tooltip.style.left = `${Math.max(8, x)}px`;
  tooltip.style.top = `${y}px`;
}

function hideTip() {
  tooltip.hidden = true;
}

/** Attach hover and keyboard-focus tooltips to a node. */
function withTip(node, build) {
  node.addEventListener("pointermove", (e) => {
    const { title, rows } = build();
    showTip({ x: e.clientX, y: e.clientY }, title, rows);
  });
  node.addEventListener("pointerleave", hideTip);
  node.addEventListener("focus", () => {
    const r = node.getBoundingClientRect();
    const { title, rows } = build();
    showTip({ x: r.left + r.width / 2, y: r.top }, title, rows);
  });
  node.addEventListener("blur", hideTip);
}

// ===== Data loading =====

async function getJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (res.status === 404) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(body.error || "not found");
    err.status = 404;
    throw err;
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function load() {
  const token = ++loadToken;
  main.classList.add("loading");
  rangeButtons.forEach((b) =>
    b.setAttribute("aria-pressed", String(Number(b.dataset.days) === period.days)),
  );
  showDates();
  const query = periodQuery(period);
  csvLink.href = `api/history/sessions.csv?${query}`;
  try {
    const [sum, list] = await Promise.all([
      getJson(`api/history/summary?${query}`),
      getJson(`api/history/sessions?${query}&limit=300`),
    ]);
    if (token !== loadToken) return;
    notice.hidden = true;
    summary = sum;
    renderAll(sum, list.sessions);
  } catch (err) {
    if (token !== loadToken) return;
    notice.hidden = false;
    notice.textContent =
      err.status === 404
        ? t(
            "履歴を読み込めません。サーバーが --no-history で起動しているか、記録を保存できていません。サーバーのログを確認してから、起動し直してください。プラグインで入れた場合は /clawd-session-house:restart で起動し直せます。",
          )
        : t("履歴を読み込めませんでした。サーバーが動いているか確認してください。エラー: {msg}", {
            msg: err.message,
          });
  } finally {
    if (token === loadToken) main.classList.remove("loading");
  }
}

// ===== Rendering =====

function renderAll(sum, sessions) {
  renderKpis(sum.totals);
  renderDaily(sum.daily);
  renderHourly(sum.hourly);
  renderProjects(sum.projects);
  renderTools(sum.tools);
  renderAgents(sum.agents);
  renderSessions(sessions);
}

function renderKpis(k) {
  const avg = k.waitCount
    ? t("平均 {avg} × {n}回", { avg: formatDuration(k.avgWaitMs), n: formatCount(k.waitCount) })
    : t("承認待ちなし");
  const tiles = [
    [
      t("セッション"),
      formatCount(k.sessions),
      t("プロンプト送信 {n}回", { n: formatCount(k.prompts) }),
    ],
    [
      t("処理時間"),
      formatDuration(k.busyMs),
      k.apiErrors
        ? t("APIエラーで停止 {n}回", { n: formatCount(k.apiErrors) })
        : t("Claudeが作業していた時間"),
    ],
    [t("承認待ち時間"), formatDuration(k.waitMs), avg],
    [
      t("ツール実行"),
      formatCount(k.toolCalls),
      t("承認を求められた回数 {n}回", { n: formatCount(k.approvals) }),
    ],
    [t("サブエージェント"), formatCount(k.subagents), t("起動した回数")],
  ];
  document
    .getElementById("kpis")
    .replaceChildren(
      ...tiles.map(([label, value, sub]) =>
        h(
          "div",
          { class: "kpi" },
          h("div", { class: "kpi-label", text: label }),
          h("div", { class: "kpi-value" }, withUnits(value)),
          h("div", { class: "kpi-sub", text: sub }),
        ),
      ),
    );
}

/** Rounded-top bar path (square at the baseline). */
function barPath(x, y, w, hgt) {
  const r = Math.min(4, hgt, w / 2);
  return `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt}Z`;
}

/**
 * Column chart with stacked series sharing one duration axis.
 *
 * @param {HTMLElement} container
 * @param {object[]} rows
 * @param {{keys: string[], label: (row: object) => string, tip: (row: object) => {title: string, rows: object[]}, height?: number, labelWidth?: number}} opts
 */
function columnChart(container, rows, opts) {
  const width = Math.max(280, container.clientWidth);
  const height = opts.height || 220;
  const m = { left: 56, right: 8, top: 10, bottom: 26 };
  const plotW = width - m.left - m.right;
  const plotH = height - m.top - m.bottom;
  const totals = rows.map((r) => opts.keys.reduce((sum, k) => sum + (r[k] || 0), 0));
  const axis = durationTicks(Math.max(0, ...totals));
  const y = (v) => m.top + plotH - (v / axis.max) * plotH;
  const band = plotW / Math.max(1, rows.length);
  const barW = Math.max(2, Math.min(MAX_BAR, band * 0.62));

  const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "group" });
  for (const tick of axis.ticks) {
    const ty = y(tick);
    svg.append(
      s("line", {
        class: tick === 0 ? "base-line" : "grid-line",
        x1: m.left,
        x2: width - m.right,
        y1: ty,
        y2: ty,
      }),
      svgText(m.left - 8, ty + 4, axis.label(tick), "end"),
    );
  }

  rows.forEach((row, i) => {
    const cx = m.left + band * i + band / 2;
    const x = cx - barW / 2;
    const g = s("g", { class: "col", tabindex: "0" });
    const tip = opts.tip(row);
    g.setAttribute(
      "aria-label",
      [tip.title, ...tip.rows.map((r) => `${r.label} ${r.value}`)].join(" "),
    );
    g.append(
      s("rect", { class: "hit", x: m.left + band * i, y: m.top, width: band, height: plotH }),
    );
    // Stacked segments are separated by 2px gaps, and only the top segment gets rounded corners
    let base = y(0);
    const present = opts.keys.filter((k) => row[k] > 0);
    present.forEach((k, idx) => {
      const segH = (row[k] / axis.max) * plotH;
      const drawH = idx > 0 ? Math.max(0, segH - GAP) : segH;
      const top = base - drawH;
      const cls = `bar ${SERIES[k].cls}`;
      if (idx === present.length - 1) {
        g.append(s("path", { class: cls, d: barPath(x, top, barW, drawH) }));
      } else {
        g.append(s("rect", { class: cls, x, y: top, width: barW, height: drawH }));
      }
      base = top - (idx < present.length - 1 ? GAP : 0);
    });
    withTip(g, () => opts.tip(row));
    svg.append(g);
  });

  for (const i of labelIndices(rows.length, plotW, opts.labelWidth || 40)) {
    svg.append(svgText(m.left + band * i + band / 2, height - 8, opts.label(rows[i])));
  }
  container.replaceChildren(svg);
}

function renderDaily(daily) {
  const container = document.getElementById("daily-chart");
  // Stack by series name (busy/wait), so repack the values that carry an Ms suffix
  columnChart(
    container,
    daily.map((r) => ({ ...r, busy: r.busyMs, wait: r.waitMs })),
    {
      keys: ["busy", "wait"],
      label: (r) => formatDate(r.date),
      tip: (r) => ({
        title: formatDate(r.date, true),
        rows: [
          { value: formatDuration(r.busyMs), label: t("処理時間"), color: SERIES.busy.color },
          { value: formatDuration(r.waitMs), label: t("承認待ち時間"), color: SERIES.wait.color },
          { value: t("{n}回", { n: formatCount(r.prompts) }), label: t("プロンプト送信") },
          { value: t("{n}件", { n: formatCount(r.sessions) }), label: t("セッション") },
        ],
      }),
    },
  );
  const table = document.getElementById("daily-table");
  table.replaceChildren(
    h(
      "thead",
      {},
      h(
        "tr",
        {},
        h("th", { text: t("日付") }),
        h("th", { class: "num", text: t("処理時間") }),
        h("th", { class: "num", text: t("承認待ち時間") }),
        h("th", { class: "num", text: t("プロンプト送信回数") }),
        h("th", { class: "num", text: t("セッション数") }),
      ),
    ),
    h(
      "tbody",
      {},
      daily.map((r) =>
        h(
          "tr",
          {},
          h("td", { text: formatDate(r.date, true) }),
          h("td", { class: "num", text: formatDuration(r.busyMs) }),
          h("td", { class: "num", text: formatDuration(r.waitMs) }),
          h("td", { class: "num", text: formatCount(r.prompts) }),
          h("td", { class: "num", text: formatCount(r.sessions) }),
        ),
      ),
    ),
  );
}

function renderHourly(hourly) {
  columnChart(
    document.getElementById("hourly-chart"),
    hourly.map((r) => ({ ...r, busy: r.busyMs })),
    {
      keys: ["busy"],
      height: 180,
      labelWidth: 28,
      label: (r) => String(r.hour),
      tip: (r) => ({
        title: t("{h}時台", { h: r.hour }),
        rows: [{ value: formatDuration(r.busyMs), label: t("処理時間"), color: SERIES.busy.color }],
      }),
    },
  );
}

/** Wrap a table so that it scrolls horizontally instead of overflowing its card. */
function tableWrap(tag, props, ...children) {
  return h("div", { class: "table-wrap" }, h(tag, props, ...children));
}

function emptyNote(text = t("この期間の記録はありません")) {
  return h("p", { class: "empty-note", text });
}

function inlineBar(value, max, text, color) {
  const pct = max > 0 ? Math.max(1, (value / max) * 100) : 0;
  return h(
    "div",
    { class: "inline-bar" },
    h(
      "div",
      { class: "track" },
      value > 0 ? h("div", { class: "fill", style: `width:${pct}%;--fill:${color}` }) : null,
    ),
    h("span", { class: "value", text }),
  );
}

function renderProjects(projects) {
  const root = document.getElementById("projects");
  if (!projects.length) return root.replaceChildren(emptyNote());
  const max = Math.max(...projects.map((p) => p.busyMs));
  root.replaceChildren(
    tableWrap(
      "table",
      {},
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          h("th", { text: t("プロジェクト") }),
          h("th", { class: "num", text: t("セッション数") }),
          h("th", { class: "num", text: t("プロンプト送信回数") }),
          h("th", { text: t("処理時間") }),
          h("th", { class: "num", text: t("承認待ち時間") }),
        ),
      ),
      h(
        "tbody",
        {},
        projects.map((p) =>
          h(
            "tr",
            {},
            h("td", { class: "name-cell", title: p.project, text: p.project }),
            h("td", { class: "num", text: formatCount(p.sessions) }),
            h("td", { class: "num", text: formatCount(p.prompts) }),
            h("td", {}, inlineBar(p.busyMs, max, formatDuration(p.busyMs), SERIES.busy.color)),
            h("td", { class: "num", text: formatDuration(p.waitMs) }),
          ),
        ),
      ),
    ),
  );
}

function renderTools(tools) {
  const root = document.getElementById("tools");
  if (!tools.length) return root.replaceChildren(emptyNote());
  const rows = foldOthers(tools, 12, "tool");
  const max = Math.max(...rows.map((r) => r.count));
  root.replaceChildren(
    h(
      "table",
      { class: "tools-table" },
      h(
        "tbody",
        {},
        rows.map((r) =>
          h(
            "tr",
            {},
            h("td", { class: "name-cell", title: r.tool, text: r.tool }),
            h(
              "td",
              {},
              inlineBar(
                r.count,
                max,
                t("{n}回", { n: formatCount(r.count) }),
                "var(--series-count)",
              ),
            ),
          ),
        ),
      ),
    ),
  );
}

function renderAgents(agents) {
  const root = document.getElementById("agents");
  if (!agents.length)
    return root.replaceChildren(emptyNote(t("この期間はサブエージェントを使っていません")));
  root.replaceChildren(
    h(
      "table",
      {},
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          h("th", { text: t("種類") }),
          h("th", { class: "num", text: t("起動回数") }),
          h("th", { class: "num", text: t("平均実行時間") }),
        ),
      ),
      h(
        "tbody",
        {},
        agents.map((a) =>
          h(
            "tr",
            {},
            h("td", { class: "name-cell", title: a.type, text: a.type }),
            h("td", { class: "num", text: formatCount(a.count) }),
            h("td", {
              class: "num",
              text: a.avgDurationMs === null ? "-" : formatDuration(a.avgDurationMs),
            }),
          ),
        ),
      ),
    ),
  );
}

function sessionName(sess) {
  return sess.title || sess.project;
}

function renderSessions(sessions) {
  const table = document.getElementById("sessions");
  const detail = document.getElementById("detail");
  if (!sessions.length) {
    table.replaceChildren(h("tbody", {}, h("tr", {}, h("td", {}, emptyNote()))));
    detail.hidden = true;
    return;
  }
  sessionList = sessions;
  drawSessionTable();
  if (selectedId && sessions.some((x) => x.id === selectedId)) selectSession(selectedId, false);
  else detail.hidden = true;
}

/** Sort by a column; clicking the same column again reverses the order. */
function sortSessions(key) {
  // Numbers and dates start descending, names ascending
  const first = SESSION_COLUMNS[key].text ? 1 : -1;
  sessionSort = { key, dir: sessionSort?.key === key ? -sessionSort.dir : first };
  drawSessionTable();
}

function drawSessionTable() {
  // shortcut: only the latest 300 fetched sessions are sorted. If a period has more, sort on the server
  const rows = sessionSort
    ? sortRows(sessionList, SESSION_COLUMNS[sessionSort.key].value, sessionSort.dir)
    : sessionList;
  const head = h(
    "thead",
    {},
    h(
      "tr",
      {},
      SESSION_COLUMNS.map((c, i) => {
        const sorted = sessionSort?.key === i;
        return h(
          "th",
          {
            class: c.num ? "num" : undefined,
            "aria-sort": sorted ? (sessionSort.dir > 0 ? "ascending" : "descending") : undefined,
          },
          h("button", { type: "button", class: "sort", onclick: () => sortSessions(i) }, c.label),
        );
      }),
    ),
  );
  const body = h(
    "tbody",
    {},
    rows.map((sess) => {
      const row = h(
        "tr",
        {
          tabindex: "0",
          "aria-selected": String(sess.id === selectedId),
          "data-id": sess.id,
          onclick: () => selectSession(sess.id),
          onkeydown: (e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              selectSession(sess.id);
            }
          },
        },
        h("td", { class: "time", text: formatTime(sess.startedAt) }),
        h(
          "td",
          { class: "name-cell", title: `${sessionName(sess)}\n${sess.cwd}` },
          sessionName(sess),
          sess.title ? h("span", { class: "name-sub", text: sess.project }) : null,
        ),
        h("td", { class: "num", text: formatDuration(sess.durationMs) }),
        h("td", { class: "num", text: formatDuration(sess.busyMs) }),
        h("td", { class: "num", text: formatDuration(sess.waitMs) }),
        h("td", { class: "num", text: formatCount(sess.prompts) }),
        h("td", { class: "num", text: formatCount(sess.toolCalls) }),
        h("td", { class: "num", text: formatCount(sess.subagents) }),
      );
      return row;
    }),
  );
  document.getElementById("sessions").replaceChildren(head, body);
}

async function selectSession(id, scroll = true) {
  selectedId = id;
  for (const tr of document.querySelectorAll("#sessions tbody tr")) {
    tr.setAttribute("aria-selected", String(tr.dataset.id === id));
  }
  const detail = document.getElementById("detail");
  try {
    const data = await getJson(`api/history/sessions/${encodeURIComponent(id)}`);
    if (selectedId !== id) return;
    renderDetail(data);
    detail.hidden = false;
    if (scroll) detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) {
    detail.hidden = false;
    detail.replaceChildren(
      emptyNote(t("詳細を読み込めませんでした（{msg}）", { msg: err.message })),
    );
  }
}

function track(segments, start, end, small = false) {
  const node = h("div", { class: "timeline" });
  for (const seg of timelineLayout(segments, start, end, small ? 0.6 : 0.3)) {
    const key = seg.state.toLowerCase();
    const label = STATE_LABELS[seg.state] || seg.state;
    const el = h("div", {
      class: "seg",
      tabindex: "0",
      "data-series": key,
      style: `left:${seg.left}%;width:${seg.width}%`,
      "aria-label": t("{label} {time}から{d}", {
        label,
        time: formatTime(seg.start),
        d: formatDuration(seg.end - seg.start),
      }),
    });
    withTip(el, () => ({
      title: `${formatTime(seg.start)} 〜 ${formatTime(seg.end)}`,
      rows: [{ value: formatDuration(seg.end - seg.start), label, color: `var(--series-${key})` }],
    }));
    node.append(el);
  }
  return node;
}

function renderDetail(data) {
  const sess = data.session;
  const start = sess.startedAt;
  const lastSeg = data.segments.length ? data.segments[data.segments.length - 1].end : start;
  const end = Math.max(sess.endedAt || 0, sess.lastEventAt, lastSeg, start + 1);
  const meta = [
    `${formatTime(start)} 〜 ${sess.endedAt ? formatTime(sess.endedAt) : t("継続中")}`,
    t("セッション時間 {d}", { d: formatDuration(sess.durationMs) }),
    t("処理時間 {d}", { d: formatDuration(sess.busyMs) }),
    t("承認待ち時間 {d}", { d: formatDuration(sess.waitMs) }),
    `ID ${sess.id.slice(0, 8)}`,
  ];
  if (sess.endReason) meta.push(t("終了理由 {reason}", { reason: sess.endReason }));

  const sections = [
    h("h3", { text: sessionName(sess) }),
    h("p", {
      class: "detail-meta",
      text: `${sess.title ? `${sess.project} · ` : ""}${meta.join(" · ")}`,
    }),
    h(
      "div",
      { class: "detail-section" },
      h(
        "div",
        { class: "card-head" },
        h("h4", { text: t("状態の移り変わり") }),
        h(
          "ul",
          { class: "legend" },
          ["idle", "busy", "wait"].map((k) =>
            h(
              "li",
              {},
              h("span", { class: "key", "data-series": k }),
              STATE_LABELS[k.toUpperCase()],
            ),
          ),
        ),
      ),
      track(data.segments, start, end),
      h(
        "div",
        { class: "axis-row" },
        h("span", { text: formatTime(start) }),
        h("span", { text: formatTime(end) }),
      ),
    ),
  ];

  if (data.subagents.length) {
    sections.push(
      h(
        "div",
        { class: "detail-section" },
        h("h4", { text: t("サブエージェント（{n}件）", { n: data.subagents.length }) }),
        data.subagents.map((a) => {
          const aEnd = a.endedAt || end;
          return h(
            "div",
            {
              class: "lane",
              title: `${a.type}\n${a.description || ""}\n${t("ツール実行 {n}回", { n: a.toolCount })}`,
            },
            h("div", { class: "lane-label" }, h("b", { text: a.type }), a.description || ""),
            track([{ state: "BUSY", start: a.startedAt, end: aEnd }], start, end, true),
          );
        }),
      ),
    );
  }
  if (data.tools.length) {
    sections.push(
      h(
        "div",
        { class: "detail-section" },
        h("h4", { text: t("使ったツール") }),
        h(
          "ul",
          { class: "chips" },
          data.tools.map((t) => h("li", {}, t.tool, h("b", { text: formatCount(t.count) }))),
        ),
      ),
    );
  }
  if (data.tasks.length) {
    sections.push(
      h(
        "div",
        { class: "detail-section" },
        h("h4", { text: t("完了したタスク（{n}件）", { n: data.tasks.length }) }),
        h(
          "ul",
          { class: "chips" },
          data.tasks.map((t) => h("li", { title: formatTime(t.completedAt), text: t.subject })),
        ),
      ),
    );
  }
  document.getElementById("detail").replaceChildren(...sections);
}

// ===== Period switching and resizing =====

function setPeriod(next) {
  period = next;
  history.replaceState(null, "", `#${periodQuery(period)}`);
  load();
}

/** Show the period's first and last day in the date inputs. */
function showDates() {
  const { from, to } = periodDates(period);
  fromInput.value = from;
  toInput.value = to;
  fromInput.max = toInput.max = periodDates({ days: 1 }).to;
  datesGroup.classList.toggle("active", !period.days);
}

for (const button of rangeButtons) {
  button.addEventListener("click", () => setPeriod({ days: Number(button.dataset.days) }));
}

for (const input of [fromInput, toInput]) {
  input.addEventListener("change", () => {
    // Do not load while a date is missing mid-input
    if (!fromInput.value || !toInput.value) return;
    // If the start and end dates are reversed, move the other one to the date just changed
    if (fromInput.value > toInput.value) {
      (input === fromInput ? toInput : fromInput).value = input.value;
    }
    setPeriod({ from: fromInput.value, to: toInput.value });
  });
}

let resizeTimer = 0;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (summary) {
      renderDaily(summary.daily);
      renderHourly(summary.hourly);
    }
  }, 150);
});

load();
