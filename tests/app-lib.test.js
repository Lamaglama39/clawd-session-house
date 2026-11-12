import assert from "node:assert/strict";
import { test } from "node:test";

import {
  agentsBeside,
  arrange,
  BESIDE_MIN_SCALE,
  backgroundSummary,
  buildLabels,
  chooseGrid,
  detectAlerts,
  formatElapsed,
  groupByFolder,
  moveTile,
  rowsNeeded,
  splitUnits,
  taskSummary,
  visibleAgents,
} from "../plugin/web/app-lib.js";
import { setLang } from "../plugin/web/i18n.js";

// The default language follows the runtime, so pin it for tests that check Japanese output
setLang("ja");

// Tile area of a full-screen 1920x1080 browser (minus the 64px header and 16px x 2 margins)
const W = 1920 - 32;
const H = 1080 - 64 - 32;
const GAP = 16;
const singles = (n) => Array(n).fill(1);

test("chooseGrid picks layouts that keep tiles large in a full HD window", () => {
  const expected = {
    1: [1, 1],
    2: [2, 1],
    3: [3, 1],
    4: [4, 1],
    6: [3, 2],
    8: [4, 2],
    10: [5, 2],
  };
  for (const [count, [cols, rows]] of Object.entries(expected)) {
    const g = chooseGrid(singles(Number(count)), W, H, GAP);
    assert.deepEqual([g.cols, g.rows], [cols, rows], `count=${count}`);
    assert.ok(g.cols * g.rows >= Number(count));
  }
});

test("chooseGrid stacks tiles in a tall, narrow window", () => {
  // A 600x900 window (minus the 56px narrow-screen header and 16px x 2 margins)
  const w = 600 - 32;
  const h = 900 - 56 - 32;
  const shape = (count) => {
    const g = chooseGrid(singles(count), w, h, GAP);
    return [g.cols, g.rows];
  };
  assert.deepEqual(shape(2), [1, 2]);
  assert.deepEqual(shape(3), [2, 2]);
  assert.deepEqual(shape(4), [2, 2]);
});

test("chooseGrid handles empty and tiny areas", () => {
  assert.deepEqual(chooseGrid([], W, H, GAP), { cols: 0, rows: 0, tileW: 0, tileH: 0 });
  const g = chooseGrid(singles(3), 10, 10, 16);
  assert.ok(g.cols >= 1 && g.rows >= 1);
});

test("chooseGrid keeps a folder group in one rectangle", () => {
  // A group of three plus three single tiles: the group takes one row and the singles the next
  const g = chooseGrid([3, 1, 1, 1], W, H, GAP);
  assert.deepEqual([g.cols, g.rows], [3, 2]);
});

test("rowsNeeded places blocks like CSS grid auto-placement", () => {
  assert.equal(rowsNeeded(singles(5), 3), 2);
  // A group of three spans two rows in two columns, leaving one empty cell inside the frame
  assert.equal(rowsNeeded([3, 2], 2), 3);
  // A block that does not fit moves to the next row and never goes back to an earlier empty cell
  assert.equal(rowsNeeded([1, 3], 3), 2);
  assert.equal(rowsNeeded([1, 2, 1], 2), 3);
});

test("arrange keeps the saved order and puts new sessions last", () => {
  const sessions = [{ id: "a" }, { id: "b" }, { id: "c" }, { id: "d" }];
  const ids = (list) => list.map((s) => s.id);
  assert.deepEqual(ids(arrange(sessions, ["c", "a", "gone"])), ["c", "a", "b", "d"]);
  assert.deepEqual(ids(arrange(sessions, [])), ["a", "b", "c", "d"]);
});

test("groupByFolder gathers sessions at the place of the first one", () => {
  const groups = groupByFolder([
    { id: "a", cwd: "/x" },
    { id: "b", cwd: "/y" },
    { id: "c", cwd: "/x" },
    { id: "d", cwd: "" },
    { id: "e", cwd: "" },
  ]);
  assert.deepEqual(
    groups.map((g) => g.map((s) => s.id)),
    [["a", "c"], ["b"], ["d"], ["e"]],
  );
});

test("moveTile moves a tile inside its group and a whole group across groups", () => {
  const groups = [["a", "b", "c"], ["d"], ["e", "f"]];
  // Within the same group, the dragged tile swaps into the drop position
  assert.deepEqual(moveTile(groups, "a", "c"), ["b", "c", "a", "d", "e", "f"]);
  assert.deepEqual(moveTile(groups, "c", "a"), ["c", "a", "b", "d", "e", "f"]);
  // Dropping on a tile of another group moves the whole group
  assert.deepEqual(moveTile(groups, "b", "f"), ["d", "e", "f", "a", "b", "c"]);
  assert.deepEqual(moveTile(groups, "f", "d"), ["a", "b", "c", "e", "f", "d"]);
  assert.deepEqual(moveTile(groups, "d", "a"), ["d", "a", "b", "c", "e", "f"]);
  assert.deepEqual(moveTile(groups, "a", "missing"), ["a", "b", "c", "d", "e", "f"]);
});

test("formatElapsed formats seconds, minutes, and hours", () => {
  assert.equal(formatElapsed(-5), "0秒");
  assert.equal(formatElapsed(12_345), "12秒");
  assert.equal(formatElapsed(185_000), "3分05秒");
  assert.equal(formatElapsed(3_725_000), "1時間02分");
  setLang("en");
  try {
    assert.equal(formatElapsed(185_000), "3m 05s");
    assert.equal(formatElapsed(3_725_000), "1h 02m");
    assert.deepEqual(taskSummary({ total: 5, done: 2 }), { text: "Tasks 2/5", current: null });
  } finally {
    setLang("ja");
  }
});

test("buildLabels numbers sessions that share a project name", () => {
  const labels = buildLabels([
    { id: "a", project: "api" },
    { id: "b", project: "web" },
    { id: "c", project: "api" },
  ]);
  assert.deepEqual(labels.get("a"), { name: "api", suffix: "#1", folder: "" });
  assert.deepEqual(labels.get("b"), { name: "web", suffix: "", folder: "" });
  assert.deepEqual(labels.get("c"), { name: "api", suffix: "#2", folder: "" });
});

test("buildLabels prefers the session title and keeps the folder as a sub label", () => {
  const labels = buildLabels([
    { id: "a", project: "api", title: "認証の修正" },
    { id: "b", project: "api", title: null },
    { id: "c", project: "web", title: "認証の修正" },
    { id: "d", project: "api" },
  ]);
  // Sessions with a title are numbered among titles, with the folder name as the subtitle
  assert.deepEqual(labels.get("a"), { name: "認証の修正", suffix: "#1", folder: "api" });
  assert.deepEqual(labels.get("c"), { name: "認証の修正", suffix: "#2", folder: "web" });
  // Sessions without a title are numbered by folder name as before
  assert.deepEqual(labels.get("b"), { name: "api", suffix: "#1", folder: "" });
  assert.deepEqual(labels.get("d"), { name: "api", suffix: "#2", folder: "" });
});

test("visibleAgents shows running agents before recently finished ones", () => {
  const agents = [
    { id: "d1", state: "done", startedAt: 1, endedAt: 10 },
    { id: "r2", state: "running", startedAt: 5, endedAt: null },
    { id: "d2", state: "done", startedAt: 2, endedAt: 20 },
    { id: "r1", state: "running", startedAt: 3, endedAt: null },
  ];
  const { shown, hidden } = visibleAgents(agents, 3);
  assert.deepEqual(
    shown.map((a) => a.id),
    ["r1", "r2", "d2"],
  );
  assert.equal(hidden, 1);
  assert.deepEqual(visibleAgents(undefined, 3), { shown: [], hidden: 0 });
});

test("backgroundSummary leaves subagents out", () => {
  assert.deepEqual(backgroundSummary({ shell: 2, subagent: 1, monitor: 1 }), {
    count: 3,
    text: "shell 2, monitor 1",
  });
  assert.deepEqual(backgroundSummary(undefined), { count: 0, text: "" });
});

test("taskSummary formats progress", () => {
  assert.equal(taskSummary({ total: 0, done: 0, current: null }), null);
  assert.equal(taskSummary(undefined), null);
  assert.deepEqual(taskSummary({ total: 5, done: 2, current: "実装" }), {
    text: "タスク 2/5",
    current: "実装",
  });
});

test("detectAlerts reports waits and long finished turns only after the first snapshot", () => {
  const s = (state, since) => ({ id: "a", state, since });
  // Do not notify on the first snapshot even if it is already waiting for approval
  let r = detectAlerts(new Map(), [s("WAIT", 0)], 60_000);
  assert.equal(r.alerts.length, 0);
  r = detectAlerts(r.next, [s("BUSY", 1_000)], 60_000);
  assert.equal(r.alerts.length, 0);
  r = detectAlerts(r.next, [s("IDLE", 2_000)], 60_000);
  // A turn already running when first seen is measured from its start (too short here to notify)
  assert.deepEqual(
    r.alerts.map((a) => a.kind),
    [],
  );
  r = detectAlerts(r.next, [s("BUSY", 10_000)], 60_000);
  r = detectAlerts(r.next, [s("WAIT", 20_000)], 60_000);
  assert.deepEqual(
    r.alerts.map((a) => a.kind),
    ["wait"],
  );
  r = detectAlerts(r.next, [s("BUSY", 30_000)], 60_000);
  r = detectAlerts(r.next, [s("IDLE", 80_000)], 60_000);
  assert.deepEqual(
    r.alerts.map((a) => [a.kind, a.turnMs]),
    [["done", 70_000]],
  );
  // Short turns are not notified
  r = detectAlerts(r.next, [s("BUSY", 90_000)], 60_000);
  r = detectAlerts(r.next, [s("IDLE", 95_000)], 60_000);
  assert.equal(r.alerts.length, 0);
});

test("splitUnits separates numbers from their units", () => {
  assert.deepEqual(splitUnits("3分05秒"), [
    { text: "3", unit: false },
    { text: "分", unit: true },
    { text: "05", unit: false },
    { text: "秒", unit: true },
  ]);
  // Thousands separators and decimal points count as part of the number
  assert.deepEqual(splitUnits("1,106回"), [
    { text: "1,106", unit: false },
    { text: "回", unit: true },
  ]);
  assert.deepEqual(splitUnits("1.5万"), [
    { text: "1.5", unit: false },
    { text: "万", unit: true },
  ]);
  assert.deepEqual(splitUnits("-"), [{ text: "-", unit: true }]);
});

test("agentsBeside keeps the parent Clawd at least 90% of its size", () => {
  const fit = (width, height, items = 2) => ({ width, height, items });
  // 1920x958 with 2: placing below lowers the room, so the parent is larger on the right
  assert.equal(agentsBeside(fit(504, 620), fit(932, 486)), true);
  // 1920x958 with 9: on the right it shrinks to 92%, still 90% or more, so use the right
  assert.equal(agentsBeside(fit(196, 290), fit(361, 235)), true);
  // 3840x2000 with 2: the row below is small for the room, so on the right it shrinks to 79%
  assert.equal(agentsBeside(fit(1022, 1520), fit(1892, 1419)), false);
  // In tall, narrow tiles, placing on the right shrinks the parent to almost half
  assert.equal(agentsBeside(fit(170, 760), fit(314, 690)), false);
});

test("agentsBeside treats exactly the minimum scale as enough", () => {
  const below = { width: 400, height: 220, items: 1 };
  const beside = { width: 200 * BESIDE_MIN_SCALE, height: 300, items: 1 };
  assert.equal(agentsBeside(beside, below), true);
  assert.equal(agentsBeside({ ...beside, width: beside.width - 1 }, below), false);
});

test("agentsBeside keeps subagents below when fewer of them would fit beside", () => {
  // In narrow tiles only one fits on the right, hiding the rest behind +N
  assert.equal(
    agentsBeside({ width: 196, height: 290, items: 1 }, { width: 361, height: 235, items: 2 }),
    false,
  );
  assert.equal(
    agentsBeside({ width: 196, height: 290, items: 1 }, { width: 361, height: 235, items: 1 }),
    true,
  );
});

test("agentsBeside keeps subagents below while the tile is not laid out", () => {
  const none = { width: 0, height: 0, items: 1 };
  assert.equal(agentsBeside(none, none), false);
  assert.equal(
    agentsBeside({ width: 100, height: 100, items: 1 }, { ...none, height: 300 }),
    false,
  );
});
