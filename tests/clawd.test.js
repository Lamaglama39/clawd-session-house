import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CANVAS_H,
  CANVAS_W,
  FEET_Y,
  PixelBuffer,
  Scene,
  START_DURATION_MS,
  computePose,
  drawClawd,
  resolveScene,
} from "../plugin/web/clawd.js";

const CLAWD = [209, 116, 85];
const BG = [18, 18, 22];
const BANNER = [255, 180, 40];

function pixel(buf, x, y) {
  const i = (y * buf.width + x) * 4;
  return [buf.data[i], buf.data[i + 1], buf.data[i + 2]];
}

function render(scene, el) {
  const buf = new PixelBuffer(CANVAS_W, CANVAS_H);
  drawClawd(buf, computePose(scene, el));
  return buf;
}

test("START switches to IDLE after the wake-up animation", () => {
  assert.deepEqual(resolveScene(Scene.START, 1000), { scene: Scene.START, elapsedMs: 1000 });
  assert.deepEqual(resolveScene(Scene.START, START_DURATION_MS + 250), {
    scene: Scene.IDLE,
    elapsedMs: 250,
  });
  assert.deepEqual(resolveScene(Scene.PROCESSING, 99_999), {
    scene: Scene.PROCESSING,
    elapsedMs: 99_999,
  });
});

test("poses follow the expected formulas", () => {
  // IDLE: no vertical movement, only blinking (eyes narrow for the first 75 ms of each 2.6 s cycle)
  for (const el of [0, 750, 1500, 2250]) {
    const idle = computePose(Scene.IDLE, el);
    assert.equal(idle.jumpY, 0);
    assert.equal(idle.bodyY, 0);
  }
  assert.ok(computePose(Scene.IDLE, 2600 + 75).eyeH < computePose(Scene.IDLE, 1000).eyeH);
  // START: right after waking, eyes closed and crouching with the feet on the floor
  const wake = computePose(Scene.START, 100);
  assert.equal(wake.eyeH, 2);
  assert.equal(wake.bodyY, 4);
  assert.equal(wake.jumpY, 0);
  // PROCESSING: narrowed eyes and the "..." marker
  const busy = computePose(Scene.PROCESSING, 0);
  assert.equal(busy.eyeH, 8);
  assert.equal(busy.marker, "dots");
  // APPROVAL: worried eyes and the HELP! banner
  const wait = computePose(Scene.APPROVAL, 0);
  assert.equal(wait.eye, "troubled");
  assert.equal(wait.banner, "HELP!");
  // END: sits down on the floor over 1.2 s and shows Zzz
  const end = computePose(Scene.END, 5000);
  assert.equal(end.bodyY, 12);
  assert.deepEqual(end.legDy, [0, 0, 0, 0]);
  assert.equal(end.marker, "zzz");
});

test("drawClawd paints the body in Clawd color on the dark background", () => {
  const buf = render(Scene.IDLE, 0);
  assert.deepEqual(pixel(buf, 0, 0), BG);
  // Lower middle of the torso (clear of the eyes and legs)
  assert.deepEqual(pixel(buf, 100, 153), CLAWD);
});

test("transparent rendering leaves the background see-through", () => {
  const buf = new PixelBuffer(CANVAS_W, CANVAS_H);
  drawClawd(buf, computePose(Scene.IDLE, 0), { transparent: true });
  assert.equal(buf.data[3], 0);
  const body = (153 * CANVAS_W + 100) * 4;
  assert.deepEqual([...buf.data.slice(body, body + 4)], [...CLAWD, 255]);
});

test("the feet of a standing Clawd end at FEET_Y, leaving room for a floor", () => {
  assert.equal(FEET_Y, 211);
  const buf = render(Scene.IDLE, 0);
  // Leftmost leg (canvas x = 19 to 36)
  const legX = 30;
  assert.deepEqual(pixel(buf, legX, FEET_Y - 1), CLAWD);
  assert.deepEqual(pixel(buf, legX, FEET_Y), BG);
});

/** Lowest row that contains Clawd's color, or -1. */
function lowestBodyRow(buf) {
  for (let y = buf.height - 1; y >= 0; y--) {
    for (let x = 0; x < buf.width; x++) {
      if (pixel(buf, x, y).every((v, i) => v === CLAWD[i])) return y;
    }
  }
  return -1;
}

test("Clawd never floats above the floor except for the wake-up hop", () => {
  const scenes = [Scene.IDLE, Scene.PROCESSING, Scene.APPROVAL, Scene.END];
  for (const scene of scenes) {
    for (let el = 0; el < 6000; el += 37) {
      assert.equal(lowestBodyRow(render(scene, el)), FEET_Y - 1, `${scene} at ${el}ms`);
    }
  }
  // The wake-up crouch (before the jump) also keeps the feet on the floor
  for (let el = 0; el < 800; el += 37) {
    assert.equal(lowestBodyRow(render(Scene.START, el)), FEET_Y - 1, `START at ${el}ms`);
  }
});

test("APPROVAL draws the HELP! banner at the top", () => {
  const buf = render(Scene.APPROVAL, 0);
  assert.deepEqual(pixel(buf, 100 - 45 - 4, 2), BANNER);
  assert.deepEqual(pixel(render(Scene.IDLE, 0), 100 - 45 - 4, 2), BG);
});

test("PixelBuffer clips drawing outside the canvas", () => {
  const buf = new PixelBuffer(4, 4);
  buf.clear(BG);
  buf.fillRect(-10, -10, 100, 100, CLAWD);
  buf.drawLine(-5, 2, 10, 2, BG);
  assert.deepEqual(pixel(buf, 3, 3), CLAWD);
  assert.deepEqual(pixel(buf, 0, 2), BG);
});
