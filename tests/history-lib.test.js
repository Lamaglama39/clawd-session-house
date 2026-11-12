import assert from "node:assert/strict";
import { test } from "node:test";

import {
  durationTicks,
  formatCount,
  formatDate,
  formatDuration,
  labelIndices,
  paginate,
  parsePeriod,
  periodDates,
  periodQuery,
  sortRows,
  timelineLayout,
} from "../plugin/web/history-lib.js";
import { setLang, t } from "../plugin/web/i18n.js";

const MIN = 60_000;
const HOUR = 60 * MIN;

// The default language follows the runtime, so pin it for tests that check Japanese output
setLang("ja");

/** Run fn with the UI language switched to English. */
function inEnglish(fn) {
  setLang("en");
  try {
    fn();
  } finally {
    setLang("ja");
  }
}

test("formatDuration", () => {
  assert.equal(formatDuration(0), "0秒");
  assert.equal(formatDuration(45_000), "45秒");
  assert.equal(formatDuration(12 * MIN + 20_000), "12分");
  assert.equal(formatDuration(HOUR + 5 * MIN), "1時間05分");
  assert.equal(formatDuration(-5), "0秒");
});

test("formatCount", () => {
  assert.equal(formatCount(1284), "1,284");
  assert.equal(formatCount(12_900), "1.3万");
  assert.equal(formatCount(250_000), "25万");
});

test("formatDate", () => {
  assert.equal(formatDate("2026-10-25"), "10/25");
  assert.equal(formatDate("2026-10-25", true), "10/25(日)");
});

test("t fills placeholders and falls back to Japanese", () => {
  assert.equal(t("{tool} 実行中", { tool: "Bash" }), "Bash 実行中");
  inEnglish(() => {
    assert.equal(t("{tool} 実行中", { tool: "Bash" }), "Running Bash");
    assert.equal(t("未翻訳の文言"), "未翻訳の文言");
    assert.equal(t("{n}件", {}), "{n}");
  });
});

test("formatters switch to English", () => {
  inEnglish(() => {
    assert.equal(formatDuration(45_000), "45s");
    assert.equal(formatDuration(HOUR + 5 * MIN), "1h 05m");
    assert.equal(formatCount(12_900), "12,900");
    assert.equal(formatDate("2026-10-25", true), "10/25 (Sun)");
    assert.equal(durationTicks(17 * MIN).label(10 * MIN), "10m");
    assert.equal(t("{first}–{last} / {total}件", { first: 1, last: 5, total: 11 }), "1–5 of 11");
  });
});

test("durationTicks picks clean steps", () => {
  const short = durationTicks(17 * MIN);
  assert.deepEqual(short.ticks, [0, 5 * MIN, 10 * MIN, 15 * MIN, 20 * MIN]);
  assert.equal(short.label(10 * MIN), "10分");
  const long = durationTicks(5.5 * HOUR);
  assert.deepEqual(long.ticks, [0, 2 * HOUR, 4 * HOUR, 6 * HOUR]);
  assert.equal(long.label(2 * HOUR), "2時間");
  // Ticks do not collapse even when every value is 0
  assert.ok(durationTicks(0).max > 0);
});

test("labelIndices thins labels from the end", () => {
  assert.deepEqual(labelIndices(7, 700, 40), [0, 1, 2, 3, 4, 5, 6]);
  assert.deepEqual(labelIndices(30, 300, 40), [1, 5, 9, 13, 17, 21, 25, 29]);
  assert.deepEqual(labelIndices(0, 300, 40), []);
});

test("timelineLayout merges START into IDLE and skips OFF", () => {
  const segs = [
    { state: "START", start: 0, end: 10 },
    { state: "IDLE", start: 10, end: 50 },
    { state: "BUSY", start: 50, end: 100 },
    { state: "OFF", start: 100, end: 100 },
  ];
  const out = timelineLayout(segs, 0, 100);
  assert.deepEqual(
    out.map((s) => [s.state, s.left, s.width]),
    [
      ["IDLE", 0, 50],
      ["BUSY", 50, 50],
    ],
  );
  // Keep a minimum width so even very short segments stay visible
  const tiny = timelineLayout([{ state: "WAIT", start: 0, end: 1 }], 0, 100_000, 0.5);
  assert.equal(tiny[0].width, 0.5);
});

test("paginate cuts pages and keeps the page in range", () => {
  const rows = Array.from({ length: 11 }, (_, i) => i + 1);
  assert.deepEqual(paginate(rows, 1, 5), {
    rows: [1, 2, 3, 4, 5],
    page: 1,
    pages: 3,
    first: 1,
    last: 5,
  });
  assert.deepEqual(paginate(rows, 3, 5), { rows: [11], page: 3, pages: 3, first: 11, last: 11 });
  // A page past the end (the list got shorter) shows the last page, and page 0 the first
  assert.equal(paginate(rows, 9, 5).page, 3);
  assert.equal(paginate(rows, 0, 5).page, 1);
  assert.equal(paginate(rows, 1, 20).pages, 1);
  assert.deepEqual(paginate([], 2, 5), { rows: [], page: 1, pages: 1, first: 0, last: 0 });
});

test("sortRows sorts numbers and Japanese text without touching the input", () => {
  const rows = [
    { name: "いぬ", n: 2, id: "a" },
    { name: "Alpha", n: 10, id: "b" },
    { name: "あひる", n: 2, id: "c" },
  ];
  assert.deepEqual(
    sortRows(rows, (r) => r.n, -1).map((r) => r.id),
    ["b", "a", "c"],
  );
  assert.deepEqual(
    sortRows(rows, (r) => r.name, 1).map((r) => r.id),
    ["b", "c", "a"],
  );
  assert.deepEqual(
    rows.map((r) => r.id),
    ["a", "b", "c"],
  );
});

test("parsePeriod, periodQuery and periodDates", () => {
  assert.deepEqual(parsePeriod("#days=30"), { days: 30 });
  assert.deepEqual(parsePeriod(""), { days: 7 });
  assert.deepEqual(parsePeriod("#days=12"), { days: 7 });
  assert.deepEqual(parsePeriod("#from=2026-10-01&to=2026-10-15"), {
    from: "2026-10-01",
    to: "2026-10-15",
  });
  // Fall back to the preset when the start date is after the end date
  assert.deepEqual(parsePeriod("#from=2026-10-15&to=2026-10-01"), { days: 7 });
  assert.equal(periodQuery({ days: 7 }), "days=7");
  assert.equal(
    periodQuery({ from: "2026-10-01", to: "2026-10-15" }),
    "from=2026-10-01&to=2026-10-15",
  );
  const now = new Date(2026, 2, 3, 9, 0);
  assert.deepEqual(periodDates({ days: 1 }, now), { from: "2026-03-03", to: "2026-03-03" });
  assert.deepEqual(periodDates({ days: 7 }, now), { from: "2026-02-25", to: "2026-03-03" });
  assert.deepEqual(periodDates({ from: "2026-01-01", to: "2026-01-31" }, now), {
    from: "2026-01-01",
    to: "2026-01-31",
  });
});
