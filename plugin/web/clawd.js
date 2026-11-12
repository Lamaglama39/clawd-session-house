/**
 * @file Clawd renderer for the status dashboard.
 *
 * Each frame is rasterized as pixel art into a 200x220 RGBA buffer using
 * integer coordinates, then scaled onto the destination canvas without smoothing.
 */

export const CANVAS_W = 200;
export const CANVAS_H = 220;

/** Scene identifiers. */
export const Scene = Object.freeze({
  START: "START",
  IDLE: "IDLE",
  PROCESSING: "PROCESSING",
  APPROVAL: "APPROVAL",
  END: "END",
});

/** Duration of the wake-up scene before switching to IDLE [ms]. */
export const START_DURATION_MS = 1600;

const COLOR_BG = [18, 18, 22];
const COLOR_CLAWD = [209, 116, 85];
const COLOR_BLACK = [0, 0, 0];
const COLOR_WHITE = [255, 255, 255];
const COLOR_EXCLAM = [255, 90, 70];
const COLOR_ZZZ = [150, 190, 255];
const COLOR_BANNER = [255, 180, 40];

// Part dimensions measured on the reference image (144x116px), in its own coordinates
const O_TORSO_X = 18;
const O_TORSO_Y = 6;
const O_TORSO_W = 108;
const O_TORSO_H = 82;
const O_NUB_Y = 33;
const O_NUB_H = 24;
const O_NUB_W = 12;
const O_NUB_LX = 6;
const O_NUB_RX = 126;
const O_LEG_Y = 88;
const O_LEG_W = 12;
const O_LEG_H = 21;
const O_LEG_X = [18, 42, 90, 114];
const O_EYE_LX = 30;
const O_EYE_RX = 102;
const O_EYE_W = 12;
const O_EYE_CY = 38;
const O_EYE_H = 13;

// Reference -> canvas coordinates: scale by 1.5, center horizontally, and clear the text area on top
const SCALE_N = 15;
const SCALE_D = 10;
const BASE_X = -8;
const BASE_Y = 48;

// Only the characters used here, from the standard Adafruit GFX font (5x7, column-major, LSB on top)
const GLYPHS = {
  "!": [0x00, 0x00, 0x5f, 0x00, 0x00],
  ".": [0x00, 0x60, 0x60, 0x00, 0x00],
  "?": [0x02, 0x01, 0x51, 0x09, 0x06],
  E: [0x7f, 0x49, 0x49, 0x49, 0x41],
  H: [0x7f, 0x08, 0x08, 0x08, 0x7f],
  L: [0x7f, 0x40, 0x40, 0x40, 0x40],
  P: [0x7f, 0x09, 0x09, 0x09, 0x06],
  Z: [0x61, 0x51, 0x49, 0x45, 0x43],
  z: [0x44, 0x64, 0x54, 0x4c, 0x44],
};
const TEXT_SIZE = 3;
const CHAR_ADVANCE = 6 * TEXT_SIZE;

/** Bottom edge of the feet in the 200x220 buffer; the rows below it are free for a floor. */
export const FEET_Y = Math.trunc(((O_LEG_Y + O_LEG_H) * SCALE_N) / SCALE_D) + BASE_Y;

// Dot positions always truncate toward zero, so negative offsets (jumps, sway) move symmetrically
const toInt = Math.trunc;

/**
 * Integer division that truncates toward zero, like C.
 * @param {number} a
 * @param {number} b
 * @returns {number}
 */
const idiv = (a, b) => toInt(a / b);

/**
 * Minimal RGBA framebuffer with rectangle, line, and 5x7 text primitives.
 */
export class PixelBuffer {
  /**
   * @param {number} width
   * @param {number} height
   */
  constructor(width, height) {
    this.width = width;
    this.height = height;
    this.data = new Uint8ClampedArray(width * height * 4);
  }

  /**
   * Fill the whole buffer with one color.
   * @param {number[]} rgb
   * @param {number} [alpha=255]  0 makes the buffer fully transparent.
   */
  clear(rgb, alpha = 255) {
    const d = this.data;
    for (let i = 0; i < d.length; i += 4) {
      d[i] = rgb[0];
      d[i + 1] = rgb[1];
      d[i + 2] = rgb[2];
      d[i + 3] = alpha;
    }
  }

  /**
   * Set one pixel, ignoring coordinates outside the buffer.
   * @param {number} x
   * @param {number} y
   * @param {number[]} rgb
   */
  setPixel(x, y, rgb) {
    if (x < 0 || y < 0 || x >= this.width || y >= this.height) return;
    const i = (y * this.width + x) * 4;
    this.data[i] = rgb[0];
    this.data[i + 1] = rgb[1];
    this.data[i + 2] = rgb[2];
    this.data[i + 3] = 255;
  }

  /**
   * Fill a rectangle (clipped to the buffer).
   * @param {number} x
   * @param {number} y
   * @param {number} w
   * @param {number} h
   * @param {number[]} rgb
   */
  fillRect(x, y, w, h, rgb) {
    const x0 = Math.max(0, x);
    const y0 = Math.max(0, y);
    const x1 = Math.min(this.width, x + w);
    const y1 = Math.min(this.height, y + h);
    for (let yy = y0; yy < y1; yy++) {
      for (let xx = x0; xx < x1; xx++) this.setPixel(xx, yy, rgb);
    }
  }

  /**
   * Draw a 1px line with Bresenham's algorithm (same as Adafruit GFX writeLine).
   * @param {number} x0
   * @param {number} y0
   * @param {number} x1
   * @param {number} y1
   * @param {number[]} rgb
   */
  drawLine(x0, y0, x1, y1, rgb) {
    const steep = Math.abs(y1 - y0) > Math.abs(x1 - x0);
    if (steep) {
      [x0, y0] = [y0, x0];
      [x1, y1] = [y1, x1];
    }
    if (x0 > x1) {
      [x0, x1] = [x1, x0];
      [y0, y1] = [y1, y0];
    }
    const dx = x1 - x0;
    const dy = Math.abs(y1 - y0);
    let err = idiv(dx, 2);
    const ystep = y0 < y1 ? 1 : -1;
    for (; x0 <= x1; x0++) {
      if (steep) this.setPixel(y0, x0, rgb);
      else this.setPixel(x0, y0, rgb);
      err -= dy;
      if (err < 0) {
        y0 += ystep;
        err += dx;
      }
    }
  }

  /**
   * Print text with the classic 5x7 GFX font at text size 3 (transparent background).
   * @param {string} text
   * @param {number} x
   * @param {number} y
   * @param {number[]} rgb
   */
  print(text, x, y, rgb) {
    for (const ch of text) {
      const glyph = GLYPHS[ch];
      if (glyph) {
        for (let col = 0; col < 5; col++) {
          let line = glyph[col];
          for (let row = 0; row < 8; row++, line >>= 1) {
            if (line & 1) {
              this.fillRect(x + col * TEXT_SIZE, y + row * TEXT_SIZE, TEXT_SIZE, TEXT_SIZE, rgb);
            }
          }
        }
      }
      x += CHAR_ADVANCE;
    }
  }
}

/**
 * @typedef {object} Pose
 * @property {number} jumpY      Whole-body vertical offset including the feet (negative = up).
 *                               Only used for a real jump; otherwise the feet stay on the floor.
 * @property {number} bodyY      Offset of the body above the legs (positive = down). The feet
 *                               stay on the floor and the legs stretch or shrink to follow.
 * @property {number} shakeX     Whole-body horizontal offset.
 * @property {number[]} legDy    Per-leg vertical offset (negative = lifted).
 * @property {number} eyeH       Eye height (smaller = more closed).
 * @property {number} eyeDX      Horizontal eye shift (gaze).
 * @property {"normal"|"troubled"} eye  Eye style.
 * @property {"none"|"dots"|"question"|"exclam"|"zzz"} marker  Head-top marker.
 * @property {number} markPhase  Marker animation phase [s].
 * @property {string|null} banner  Top banner text, or null to hide it.
 */

/**
 * Return a neutral standing pose.
 * @returns {Pose}
 */
function neutralPose() {
  return {
    jumpY: 0,
    bodyY: 0,
    shakeX: 0,
    legDy: [0, 0, 0, 0],
    eyeH: O_EYE_H,
    eyeDX: 0,
    eye: "normal",
    marker: "none",
    markPhase: 0,
    banner: null,
  };
}

/**
 * Apply a short blink to eyeH given a phase timer.
 * @param {Pose} p
 * @param {number} phase   Elapsed time [ms].
 * @param {number} period  Blink period [ms].
 * @param {number} base    Eye height when open.
 */
function applyBlink(p, phase, period, base) {
  const b = phase % period;
  if (b < 150) {
    const f = b < 75 ? b / 75 : (150 - b) / 75;
    p.eyeH = base - toInt((base - 2) * f);
  } else {
    p.eyeH = base;
  }
}

/**
 * Resolve the scene actually shown, applying the automatic START -> IDLE switch.
 * @param {string} scene
 * @param {number} elapsedMs  Time since the scene was entered [ms].
 * @returns {{scene: string, elapsedMs: number}}
 */
export function resolveScene(scene, elapsedMs) {
  if (scene === Scene.START && elapsedMs > START_DURATION_MS) {
    return { scene: Scene.IDLE, elapsedMs: elapsedMs - START_DURATION_MS };
  }
  return { scene, elapsedMs };
}

/**
 * Build the pose for a scene at the given elapsed time.
 * @param {string} scene
 * @param {number} el  Elapsed time in the scene [ms].
 * @returns {Pose}
 */
export function computePose(scene, el) {
  const p = neutralPose();
  const t = el / 1000;
  switch (scene) {
    case Scene.START: {
      // Crouch with the feet on the floor, open the eyes, and end with a small hop
      if (el < 400) {
        p.eyeH = 2;
        p.bodyY = 4;
      } else if (el < 800) {
        const f = (el - 400) / 400;
        p.eyeH = 2 + toInt(11 * f);
        p.bodyY = 4 - toInt(4 * f);
      } else {
        const ph = ((el - 800) / 700) * Math.PI;
        p.jumpY = -toInt(18 * Math.sin(ph));
      }
      break;
    }
    case Scene.IDLE: {
      // Standing on the floor, so the body stays put; only blinking and glances
      applyBlink(p, el, 2600, O_EYE_H);
      p.eyeDX = toInt(2 * Math.sin((t * 2 * Math.PI) / 5));
      break;
    }
    case Scene.PROCESSING: {
      const ph = ((el / 650) % 1) * 2 * Math.PI;
      const lift = 8;
      const s0 = Math.sin(ph);
      const s1 = Math.sin(ph + Math.PI);
      const up0 = s0 > 0 ? toInt(lift * s0 + 0.5) : 0;
      const up1 = s1 > 0 ? toInt(lift * s1 + 0.5) : 0;
      p.legDy[0] = p.legDy[2] = -up0;
      p.legDy[1] = p.legDy[3] = -up1;
      // The body bounces a little with each step. The leg on the floor stretches, so feet never float
      p.bodyY = -toInt(2 * Math.abs(Math.sin(2 * ph)) + 0.5);
      p.eyeH = 8;
      p.eyeDX = toInt(2 * Math.sin(el / 400));
      p.marker = "dots";
      p.markPhase = el / 550;
      break;
    }
    case Scene.APPROVAL: {
      p.shakeX = toInt(2 * Math.sin(el / 55));
      p.bodyY = toInt(1 * Math.sin(el / 47));
      p.eye = "troubled";
      p.banner = "HELP!";
      break;
    }
    case Scene.END: {
      const f = el < 1200 ? el / 1200 : 1;
      p.eyeH = O_EYE_H - toInt(11 * f);
      // Fold the legs and sit down on the floor (the body drops and the visible legs get shorter)
      p.bodyY = toInt(12 * f);
      p.marker = el > 600 ? "zzz" : "none";
      p.markPhase = t;
      break;
    }
    default:
      break;
  }
  return p;
}

/**
 * Rasterize one Clawd frame described by pose into buf.
 * @param {PixelBuffer} buf  Buffer of CANVAS_W x CANVAS_H.
 * @param {Pose} p
 * @param {{transparent?: boolean}} [opts]  transparent leaves the background see-through.
 */
export function drawClawd(buf, p, { transparent = false } = {}) {
  const sx = (ox) => idiv(ox * SCALE_N, SCALE_D) + BASE_X + p.shakeX;
  // Treat the foot height (sy) and the body height above the legs (by) separately
  const sy = (oy) => idiv(oy * SCALE_N, SCALE_D) + BASE_Y + p.jumpY;
  const by = (oy) => sy(oy) + p.bodyY;
  const fillO = (ox, oy, ow, oh, rgb) => {
    const x1 = sx(ox);
    const x2 = sx(ox + ow);
    const y1 = by(oy);
    const y2 = by(oy + oh);
    buf.fillRect(x1, y1, x2 - x1, y2 - y1, rgb);
  };
  const drawBracketEye = (cxo, cyo, pointRight) => {
    const cx = sx(cxo);
    const cy = by(cyo);
    // Thick brackets modeled on the worried face of the reference image (half width, half height, thickness)
    const hw = 11;
    const hh = 14;
    const thick = 13;
    const apexX = pointRight ? cx + hw : cx - hw;
    const backX = pointRight ? cx - hw : cx + hw;
    for (let i = 0; i < thick; i++) {
      const off = pointRight ? i : -i;
      buf.drawLine(backX + off, cy - hh, apexX + off, cy, COLOR_BLACK);
      buf.drawLine(backX + off, cy + hh, apexX + off, cy, COLOR_BLACK);
    }
  };

  buf.clear(COLOR_BG, transparent ? 0 : 255);

  // Torso and arms
  fillO(O_TORSO_X, O_TORSO_Y, O_TORSO_W, O_TORSO_H, COLOR_CLAWD);
  fillO(O_NUB_LX, O_NUB_Y, O_NUB_W, O_NUB_H, COLOR_CLAWD);
  fillO(O_NUB_RX, O_NUB_Y, O_NUB_W, O_NUB_H, COLOR_CLAWD);
  // Legs: stretch so the root meets the body and the tip meets the floor (raised legs stop higher)
  for (let i = 0; i < 4; i++) {
    const x1 = sx(O_LEG_X[i]);
    const x2 = sx(O_LEG_X[i] + O_LEG_W);
    const y1 = by(O_LEG_Y);
    const y2 = sy(O_LEG_Y + O_LEG_H) + p.legDy[i];
    buf.fillRect(x1, y1, x2 - x1, y2 - y1, COLOR_CLAWD);
  }
  // Eyes
  if (p.eye === "troubled") {
    drawBracketEye(O_EYE_LX + idiv(O_EYE_W, 2), O_EYE_CY, true);
    drawBracketEye(O_EYE_RX + idiv(O_EYE_W, 2), O_EYE_CY, false);
  } else {
    const top = O_EYE_CY - idiv(p.eyeH, 2);
    fillO(O_EYE_LX + p.eyeDX, top, O_EYE_W, p.eyeH, COLOR_BLACK);
    fillO(O_EYE_RX + p.eyeDX, top, O_EYE_W, p.eyeH, COLOR_BLACK);
  }
  // Marker above the head
  drawMarker(buf, p, by);
  // Alert banner
  if (p.banner) {
    const tw = p.banner.length * CHAR_ADVANCE;
    const bx = idiv(CANVAS_W, 2) - idiv(tw, 2);
    const pad = 8;
    buf.fillRect(bx - pad, 0, tw + pad * 2, 32, COLOR_BANNER);
    buf.print(p.banner, bx, 5, COLOR_BLACK);
  }
}

/**
 * Draw the head-top marker that bobs gently.
 * @param {PixelBuffer} buf
 * @param {Pose} p
 * @param {(oy: number) => number} sy  Reference-to-canvas Y transform of the body.
 */
function drawMarker(buf, p, sy) {
  let txt;
  let rgb = COLOR_WHITE;
  switch (p.marker) {
    case "dots":
      // Thinking, shown as "." growing from one to three dots
      txt = ".".repeat(1 + (toInt(p.markPhase * 3) % 3));
      break;
    case "question":
      txt = "?";
      break;
    case "exclam":
      txt = "!";
      rgb = COLOR_EXCLAM;
      break;
    case "zzz":
      txt = "Zzz";
      rgb = COLOR_ZZZ;
      break;
    default:
      return;
  }
  const w = txt.length * CHAR_ADVANCE;
  const x = idiv(CANVAS_W, 2) - idiv(w, 2);
  const y = sy(O_TORSO_Y) - 36 + toInt(3 * Math.sin(p.markPhase * 2 * Math.PI));
  buf.print(txt, x, y, rgb);
}

/**
 * Stateful helper that renders Clawd for one tile onto a visible canvas.
 */
export class ClawdView {
  /**
   * @param {HTMLCanvasElement} canvas  Destination canvas sized by CSS.
   * @param {{transparent?: boolean, anchor?: "center"|"bottom"}} [opts]
   *   transparent lets the element behind the canvas show through; anchor "bottom"
   *   puts the drawing on the bottom edge so that Clawd stands on a floor drawn by CSS.
   */
  constructor(canvas, { transparent = false, anchor = "center" } = {}) {
    this.canvas = canvas;
    this.transparent = transparent;
    this.anchor = anchor;
    /** Height below the feet in CSS pixels (where the floor goes) for the last frame. */
    this.floorPx = 0;
    this.ctx = canvas.getContext("2d");
    this.buf = new PixelBuffer(CANVAS_W, CANVAS_H);
    this.image = new ImageData(this.buf.data, CANVAS_W, CANVAS_H);
    this.offscreen = new OffscreenCanvas(CANVAS_W, CANVAS_H);
    this.offCtx = this.offscreen.getContext("2d");
  }

  /**
   * Render the scene at the given elapsed time.
   * @param {string} scene
   * @param {number} elapsedMs
   */
  render(scene, elapsedMs) {
    const resolved = resolveScene(scene, elapsedMs);
    drawClawd(this.buf, computePose(resolved.scene, resolved.elapsedMs), {
      transparent: this.transparent,
    });
    this.offCtx.putImageData(this.image, 0, 0);

    const dpr = window.devicePixelRatio || 1;
    const cssW = this.canvas.clientWidth;
    const cssH = this.canvas.clientHeight;
    const w = Math.max(1, Math.round(cssW * dpr));
    const h = Math.max(1, Math.round(cssH * dpr));
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }

    // Clawd is made of large rectangles, so it stays crisp at fractional scales without smoothing. Fill the frame
    const scale = Math.min(w / CANVAS_W, h / CANVAS_H);
    const dw = Math.round(CANVAS_W * scale);
    const dh = Math.round(CANVAS_H * scale);
    const ctx = this.ctx;
    ctx.imageSmoothingEnabled = false;
    if (this.transparent) {
      ctx.clearRect(0, 0, w, h);
    } else {
      ctx.fillStyle = `rgb(${COLOR_BG.join(",")})`;
      ctx.fillRect(0, 0, w, h);
    }
    const top = this.anchor === "bottom" ? h - dh : Math.floor((h - dh) / 2);
    ctx.drawImage(this.offscreen, Math.floor((w - dw) / 2), top, dw, dh);
    // Height from the feet to the bottom edge. A floor band this tall makes Clawd stand right on it
    this.floorPx = (h - top - Math.round(FEET_Y * scale)) / dpr;
  }
}
