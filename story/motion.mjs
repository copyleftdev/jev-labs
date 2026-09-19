// Deterministic motion primitives. Every function is a pure function of time,
// so evaluate(t) stays exact under seeking and frame-stepped capture.

export const clamp01 = v => Math.max(0, Math.min(1, v));

// Damped harmonic spring from 0 to 1. Closed form, so no integration state.
//   zeta < 1 overshoots (underdamped); zeta = 1 is critical.
// Returns position at time t (seconds since release).
export function spring(t, { omega = 12, zeta = 0.55 } = {}) {
  if (t <= 0) return 0;
  if (zeta >= 1) { const e = Math.exp(-omega * t); return 1 - e * (1 + omega * t); }
  const wd = omega * Math.sqrt(1 - zeta * zeta);
  const e = Math.exp(-zeta * omega * t);
  return 1 - e * (Math.cos(wd * t) + (zeta * omega / wd) * Math.sin(wd * t));
}

// Spring that begins at local time `at` (in the same units as t).
export const springFrom = (t, at, opts) => spring(t - at, opts);

// Snappy ease with a little overshoot, cheaper than a spring when you only
// need the shape. Back-out.
export function backOut(v, s = 1.70158) {
  const t = clamp01(v) - 1;
  return t * t * ((s + 1) * t + s) + 1;
}

export function easeOutExpo(v) { const t = clamp01(v); return t === 1 ? 1 : 1 - Math.pow(2, -10 * t); }
export function easeInOut(v) { const t = clamp01(v); return t * t * (3 - 2 * t); }

// Ballistic drop with restitution, closed form. A point released at height h
// (scene units above the floor, y grows downward) at time 0 under gravity g.
// Returns its height above the floor at time t, bouncing with coefficient e,
// coming to rest after `maxBounces`.
export function drop(t, h, { g = 2400, e = 0.42, maxBounces = 4 } = {}) {
  if (t <= 0) return h;
  let v0 = 0, height = h, elapsed = 0;
  // first fall
  const tf = Math.sqrt(2 * h / g);
  if (t < tf) return h - 0.5 * g * t * t;
  elapsed = tf; let v = Math.sqrt(2 * g * h) * e;
  for (let b = 0; b < maxBounces; b++) {
    const tb = 2 * v / g;                     // time in the air for this bounce
    if (t < elapsed + tb) { const dt = t - elapsed; return v * dt - 0.5 * g * dt * dt; }
    elapsed += tb; v *= e;
    if (v < 4) break;
  }
  return 0;
}

// Small deterministic hash -> [0,1). For per-element jitter that must not
// change between frames or renders.
export function hash01(i, salt = 0) {
  let x = (i * 374761393 + salt * 668265263) | 0;
  x = (x ^ (x >>> 13)) * 1274126177 | 0;
  return ((x ^ (x >>> 16)) >>> 0) / 4294967296;
}

// Perlin-ish 1D noise: smooth, deterministic, zero-mean. For jostle.
export function noise1(t, seed = 0) {
  const i = Math.floor(t), f = t - i;
  const a = hash01(i, seed) * 2 - 1, b = hash01(i + 1, seed) * 2 - 1;
  const u = f * f * (3 - 2 * f);
  return a + (b - a) * u;
}

// Point on a quadratic curve a->b with control bend.
export function onCurve(a, b, bend, p) {
  const t = clamp01(p), u = 1 - t;
  const m = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 + bend };
  return { x: u * u * a.x + 2 * u * t * m.x + t * t * b.x, y: u * u * a.y + 2 * u * t * m.y + t * t * b.y };
}

// Stagger helper: element i of n gets a local time offset.
export const stagger = (t, i, n, spread) => t - (i / Math.max(1, n - 1)) * spread;
