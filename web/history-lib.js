/**
 * @file Pure helpers for the review page (formatting, axis ticks, timeline layout).
 */

import { lang, t } from "./i18n.js";

const MIN = 60_000;
const HOUR = 60 * MIN;

/**
 * Format a duration for tables and tooltips, e.g. "45秒", "12分", "1時間05分".
 * @param {number} ms
 * @returns {string}
 */
export function formatDuration(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  if (total < 60) return t("{n}秒", { n: total });
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return t("{n}分", { n: minutes });
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return t("{h}時間{m}分", { h, m: String(m).padStart(2, "0") });
}

/**
 * Format a count with thousands separators, switching to "万" from 10,000 in Japanese.
 * @param {number} n
 * @returns {string}
 */
export function formatCount(n) {
  if (lang === "ja" && n >= 10_000) return `${(n / 10_000).toFixed(n >= 100_000 ? 0 : 1)}万`;
  return n.toLocaleString(lang === "ja" ? "ja-JP" : "en-US");
}

/**
 * Short date label "10/25" for an ISO date string.
 * @param {string} iso  "YYYY-MM-DD"
 * @param {boolean} [withWeekday]  Append the weekday, e.g. "10/25(土)".
 * @returns {string}
 */
export function formatDate(iso, withWeekday = false) {
  const [y, m, d] = iso.split("-").map(Number);
  const label = `${m}/${d}`;
  if (!withWeekday) return label;
  const day = new Date(y, m - 1, d).toLocaleDateString(lang, { weekday: "short" });
  return t("{date}({day})", { date: label, day });
}

/**
 * Local "HH:MM" (and "M/D " prefix when not today) for an epoch timestamp.
 * @param {number} ms
 * @param {number} [now]
 * @returns {string}
 */
export function formatTime(ms, now = Date.now()) {
  const d = new Date(ms);
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  const today = new Date(now);
  const sameDay =
    d.getFullYear() === today.getFullYear() &&
    d.getMonth() === today.getMonth() &&
    d.getDate() === today.getDate();
  return sameDay ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}

/**
 * Clean axis ticks for a duration axis.
 *
 * @param {number} maxMs  Largest value to show.
 * @param {number} [target]  Roughly how many intervals to aim for.
 * @returns {{max: number, ticks: number[], label: (ms: number) => string}}
 */
export function durationTicks(maxMs, target = 4) {
  const steps = [
    MIN,
    2 * MIN,
    5 * MIN,
    10 * MIN,
    15 * MIN,
    30 * MIN,
    HOUR,
    2 * HOUR,
    3 * HOUR,
    4 * HOUR,
    6 * HOUR,
    12 * HOUR,
  ];
  const wanted = Math.max(maxMs, MIN) / target;
  const step = steps.find((s) => s >= wanted) || Math.ceil(wanted / (12 * HOUR)) * 12 * HOUR;
  const max = Math.max(step, Math.ceil(maxMs / step) * step);
  const ticks = [];
  for (let v = 0; v <= max; v += step) ticks.push(v);
  const label =
    step >= HOUR ? (ms) => t("{n}時間", { n: ms / HOUR }) : (ms) => t("{n}分", { n: ms / MIN });
  return { max, ticks, label };
}

/**
 * Indices of x-axis labels to show so that labels do not collide.
 *
 * @param {number} count  Number of columns.
 * @param {number} width  Plot width [px].
 * @param {number} labelWidth  Space one label needs [px].
 * @returns {number[]}
 */
export function labelIndices(count, width, labelWidth) {
  if (count <= 0) return [];
  const every = Math.max(1, Math.ceil((count * labelWidth) / Math.max(1, width)));
  const result = [];
  // The last column (today) must always show, so thin out from the end
  for (let i = count - 1; i >= 0; i -= every) result.unshift(i);
  return result;
}

/**
 * Lay out a session's state segments on a 0-100% track.
 *
 * START is drawn as IDLE and OFF is not drawn. Segments narrower than
 * ``minPct`` are widened so they stay visible and hoverable.
 *
 * @param {{state: string, start: number, end: number}[]} segments
 * @param {number} start  Session start [ms].
 * @param {number} end  Session end (or now) [ms].
 * @param {number} [minPct]
 * @returns {{state: string, start: number, end: number, left: number, width: number}[]}
 */
export function timelineLayout(segments, start, end, minPct = 0.3) {
  const span = Math.max(1, end - start);
  const result = [];
  for (const seg of segments || []) {
    if (seg.state === "OFF") continue;
    const state = seg.state === "START" ? "IDLE" : seg.state;
    const s = Math.max(start, seg.start);
    const e = Math.min(end, Math.max(seg.end, seg.start));
    if (e < s) continue;
    const last = result[result.length - 1];
    if (last && last.state === state && last.end === s) {
      last.end = e;
      last.width = ((last.end - last.start) / span) * 100;
      continue;
    }
    result.push({
      state,
      start: s,
      end: e,
      left: ((s - start) / span) * 100,
      width: ((e - s) / span) * 100,
    });
  }
  for (const r of result) r.width = Math.max(r.width, minPct);
  return result;
}

/**
 * Keep the top ``limit`` entries and fold the rest into an "その他" row.
 *
 * @param {{count: number}[]} rows  Sorted by count desc.
 * @param {number} limit
 * @param {string} key  Name field.
 * @returns {object[]}
 */
export function foldOthers(rows, limit, key) {
  if (rows.length <= limit) return rows;
  const head = rows.slice(0, limit - 1);
  const rest = rows.slice(limit - 1).reduce((sum, r) => sum + r.count, 0);
  return [...head, { [key]: t("その他"), count: rest, other: true }];
}

/**
 * Return a sorted copy of rows. Strings are compared in Japanese collation order,
 * numbers by value; ties keep their original order.
 *
 * @param {object[]} rows
 * @param {(row: object) => number|string} value  Sort key of a row.
 * @param {1|-1} dir  1 for ascending, -1 for descending.
 * @returns {object[]}
 */
export function sortRows(rows, value, dir) {
  const cmp = (a, b) => {
    const x = value(a);
    const y = value(b);
    return typeof x === "string" ? x.localeCompare(y, "ja") : x - y;
  };
  return [...rows].sort((a, b) => cmp(a, b) * dir);
}

const PRESET_DAYS = [1, 7, 30, 90];

/**
 * Read the period from the URL hash: "#days=7" for a preset, or "#from=YYYY-MM-DD&to=YYYY-MM-DD".
 * @param {string} hash
 * @returns {{days: number}|{from: string, to: string}}
 */
export function parsePeriod(hash) {
  const range = /from=(\d{4}-\d{2}-\d{2})&to=(\d{4}-\d{2}-\d{2})/.exec(hash);
  if (range && range[1] <= range[2]) return { from: range[1], to: range[2] };
  const days = Number(/days=(\d+)/.exec(hash)?.[1]);
  return { days: PRESET_DAYS.includes(days) ? days : 7 };
}

/**
 * Query string for the history API, also used as the URL hash.
 * @param {{days: number}|{from: string, to: string}} period
 * @returns {string}
 */
export function periodQuery(period) {
  return period.days ? `days=${period.days}` : `from=${period.from}&to=${period.to}`;
}

/**
 * First and last local calendar day (YYYY-MM-DD) that a period covers.
 * @param {{days: number}|{from: string, to: string}} period
 * @param {Date} [now]
 * @returns {{from: string, to: string}}
 */
export function periodDates(period, now = new Date()) {
  if (!period.days) return { from: period.from, to: period.to };
  const start = new Date(now);
  start.setDate(start.getDate() - (period.days - 1));
  return { from: isoDate(start), to: isoDate(now) };
}

function isoDate(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
