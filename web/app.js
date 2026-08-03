/* =================================================================== *
 * Beeline — WP6 floor-plan map.
 *
 * Driven ONLY by the frozen SSE contract (BUILD_SPEC §4.6):
 *   frames are {kind, data}; kinds: graph, stats, event, trace,
 *   outbox, energy, reset, error.
 *
 * No external CDNs, no build step, no npm. Everything is inline.
 * Renders against MOCK data by default; a toggle switches to the live
 * EventSource at /api/stream (the real backend arrives in Phase D).
 * =================================================================== */
(() => {
'use strict';

/* ------------------------------------------------------------------ *
 * 0. Tiny dependency-free QR generator (byte mode, EC level M, v1-6).
 *    Enough for a join URL. If it ever throws, we fall back to a
 *    labeled placeholder box — never a CDN.
 * ------------------------------------------------------------------ */
const QR = (() => {
  // GF(256) tables, primitive polynomial 0x11d
  const EXP = new Array(512), LOG = new Array(256);
  (() => { let x = 1; for (let i = 0; i < 255; i++) { EXP[i] = x; LOG[x] = i; x <<= 1; if (x & 0x100) x ^= 0x11d; }
           for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255]; })();
  const gmul = (a, b) => (a === 0 || b === 0) ? 0 : EXP[LOG[a] + LOG[b]];

  function genPoly(n) {
    let g = [1];
    for (let i = 0; i < n; i++) {
      const m = new Array(g.length + 1).fill(0);
      for (let j = 0; j <= g.length; j++) {
        let c = 0;
        if (j < g.length) c ^= g[j];
        if (j > 0) c ^= gmul(g[j - 1], EXP[i]);
        m[j] = c;
      }
      g = m;
    }
    return g; // length n+1, g[0]=1
  }
  function ecBytes(data, ecLen) {
    const g = genPoly(ecLen);
    const res = new Array(ecLen).fill(0);
    for (const d of data) {
      const factor = d ^ res[0];
      res.shift(); res.push(0);
      if (factor !== 0) for (let i = 0; i < ecLen; i++) res[i] ^= gmul(g[i + 1], factor);
    }
    return res;
  }

  // Version tables at EC level M. groups = [[blockCount, dataCwPerBlock], ...]
  const V = {
    1: { ec: 10, groups: [[1, 16]], align: [] },
    2: { ec: 16, groups: [[1, 28]], align: [6, 18] },
    3: { ec: 26, groups: [[1, 44]], align: [6, 22] },
    4: { ec: 18, groups: [[2, 32]], align: [6, 26] },
    5: { ec: 24, groups: [[2, 43]], align: [6, 30] },
    6: { ec: 16, groups: [[4, 27]], align: [6, 34] },
  };
  for (const k in V) V[k].dataCw = V[k].groups.reduce((s, [b, d]) => s + b * d, 0);

  function utf8(str) {
    const out = [];
    for (const ch of unescape(encodeURIComponent(str))) out.push(ch.charCodeAt(0));
    return out;
  }
  function pickVersion(len) {
    for (const v of [1, 2, 3, 4, 5, 6]) if (len <= V[v].dataCw - 2) return v;
    return 6; // clamp; join URLs comfortably fit under this
  }

  function dataCodewords(bytes, version) {
    const info = V[version];
    const cap = info.dataCw * 8;
    const bits = [];
    const push = (val, n) => { for (let i = n - 1; i >= 0; i--) bits.push((val >> i) & 1); };
    push(0b0100, 4);            // byte mode
    push(bytes.length, 8);      // count indicator (8 bits, v1-9)
    for (const b of bytes) push(b, 8);
    for (let i = 0, t = Math.min(4, cap - bits.length); i < t; i++) bits.push(0); // terminator
    while (bits.length % 8) bits.push(0);
    const pads = [0xEC, 0x11];
    for (let i = 0; bits.length < cap; i++) push(pads[i & 1], 8);
    const cw = [];
    for (let i = 0; i < bits.length; i += 8) { let v = 0; for (let j = 0; j < 8; j++) v = (v << 1) | bits[i + j]; cw.push(v); }
    return cw;
  }
  function interleave(dataCw, version) {
    const info = V[version];
    const blocks = [];
    let idx = 0;
    for (const [count, dlen] of info.groups)
      for (let b = 0; b < count; b++) { const seg = dataCw.slice(idx, idx + dlen); idx += dlen; blocks.push({ d: seg, e: ecBytes(seg, info.ec) }); }
    const out = [];
    const maxD = Math.max(...blocks.map(b => b.d.length));
    for (let i = 0; i < maxD; i++) for (const b of blocks) if (i < b.d.length) out.push(b.d[i]);
    for (let i = 0; i < info.ec; i++) for (const b of blocks) out.push(b.e[i]);
    return out;
  }

  const maskFn = (m, r, c) => {
    switch (m) {
      case 0: return (r + c) % 2 === 0;
      case 1: return r % 2 === 0;
      case 2: return c % 3 === 0;
      case 3: return (r + c) % 3 === 0;
      case 4: return (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0;
      case 5: return ((r * c) % 2) + ((r * c) % 3) === 0;
      case 6: return (((r * c) % 2) + ((r * c) % 3)) % 2 === 0;
      case 7: return (((r * c) % 3) + ((r + c) % 2)) % 2 === 0;
    }
    return false;
  };
  const bchDigit = d => { let n = 0; while (d) { n++; d >>>= 1; } return n; };
  function bchTypeInfo(data) {
    let d = data << 10;
    while (bchDigit(d) - bchDigit(0x537) >= 0) d ^= 0x537 << (bchDigit(d) - bchDigit(0x537));
    return ((data << 10) | d) ^ 0x5412;
  }

  function placeFinder(mat, size, top, left) {
    for (let r = -1; r <= 7; r++) for (let c = -1; c <= 7; c++) {
      const rr = top + r, cc = left + c;
      if (rr < 0 || rr >= size || cc < 0 || cc >= size) continue;
      const border = (r >= 0 && r <= 6 && (c === 0 || c === 6)) || (c >= 0 && c <= 6 && (r === 0 || r === 6));
      const inner = (r >= 2 && r <= 4 && c >= 2 && c <= 4);
      mat[rr][cc] = border || inner;
    }
  }
  function placeAlign(mat, r, c) {
    for (let dr = -2; dr <= 2; dr++) for (let dc = -2; dc <= 2; dc++)
      mat[r + dr][c + dc] = Math.max(Math.abs(dr), Math.abs(dc)) !== 1;
  }
  function typeInfo(mat, size, mask) {
    const bits = bchTypeInfo((0 << 3) | mask); // 0 = EC level M
    for (let i = 0; i < 15; i++) {
      const b = ((bits >> i) & 1) === 1;
      if (i < 6) mat[i][8] = b; else if (i < 8) mat[i + 1][8] = b; else mat[size - 15 + i][8] = b;
      if (i < 8) mat[8][size - i - 1] = b; else if (i < 9) mat[8][15 - i - 1 + 1] = b; else mat[8][15 - i - 1] = b;
    }
    mat[size - 8][8] = true; // dark module
  }
  function mapData(mat, size, cw, mask) {
    const total = cw.length * 8;
    const getBit = k => k < total ? ((cw[k >> 3] >> (7 - (k & 7))) & 1) : 0;
    let bi = 0, inc = -1, row = size - 1;
    for (let col = size - 1; col > 0; col -= 2) {
      if (col === 6) col--;
      while (true) {
        for (let c = 0; c < 2; c++) {
          const cc = col - c;
          if (mat[row][cc] === null) {
            let dark = getBit(bi) === 1; bi++;
            if (maskFn(mask, row, cc)) dark = !dark;
            mat[row][cc] = dark;
          }
        }
        row += inc;
        if (row < 0 || row >= size) { row -= inc; inc = -inc; break; }
      }
    }
  }
  function penalty(mat, size) {
    let p = 0;
    const at = (i, j) => mat[i][j] ? 1 : 0;
    // rule 1: runs of >= 5
    for (let i = 0; i < size; i++) {
      for (let dir = 0; dir < 2; dir++) {
        let run = 1, prev = -1;
        for (let j = 0; j < size; j++) {
          const v = dir === 0 ? at(i, j) : at(j, i);
          if (v === prev) run++; else { if (run >= 5) p += 3 + (run - 5); run = 1; prev = v; }
        }
        if (run >= 5) p += 3 + (run - 5);
      }
    }
    // rule 2: 2x2 blocks
    for (let i = 0; i < size - 1; i++) for (let j = 0; j < size - 1; j++) {
      const v = at(i, j);
      if (v === at(i, j + 1) && v === at(i + 1, j) && v === at(i + 1, j + 1)) p += 3;
    }
    // rule 3: finder-like 1011101 with 4-light padding
    const A = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0], B = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1];
    for (let i = 0; i < size; i++) for (let j = 0; j <= size - 11; j++) {
      let ha = true, hb = true, va = true, vb = true;
      for (let k = 0; k < 11; k++) {
        const h = at(i, j + k), v = at(j + k, i);
        if (h !== A[k]) ha = false; if (h !== B[k]) hb = false;
        if (v !== A[k]) va = false; if (v !== B[k]) vb = false;
      }
      if (ha || hb) p += 40; if (va || vb) p += 40;
    }
    // rule 4: dark ratio
    let dark = 0; for (let i = 0; i < size; i++) for (let j = 0; j < size; j++) dark += at(i, j);
    const pct = (dark / (size * size)) * 100;
    p += Math.floor(Math.abs(pct - 50) / 5) * 10;
    return p;
  }

  function build(mat0, size, cw, mask) {
    const mat = mat0.map(row => row.slice());
    typeInfo(mat, size, mask);
    mapData(mat, size, cw, mask);
    return mat;
  }

  // returns { size, isDark(r,c) } or throws
  function encode(text) {
    const bytes = utf8(text);
    const version = pickVersion(bytes.length);
    const size = 17 + 4 * version;
    const info = V[version];
    const cw = interleave(dataCodewords(bytes, version), version);

    // function-pattern skeleton (mask-independent)
    const base = Array.from({ length: size }, () => new Array(size).fill(null));
    placeFinder(base, size, 0, 0);
    placeFinder(base, size, 0, size - 7);
    placeFinder(base, size, size - 7, 0);
    for (let i = 8; i < size - 8; i++) { if (base[6][i] === null) base[6][i] = i % 2 === 0; if (base[i][6] === null) base[i][6] = i % 2 === 0; }
    const first = 6, last = size - 7;
    for (const r of info.align) for (const c of info.align) {
      if ((r === first && c === first) || (r === first && c === last) || (r === last && c === first)) continue;
      placeAlign(base, r, c);
    }

    let best = null, bestP = Infinity;
    for (let m = 0; m < 8; m++) { const mat = build(base, size, cw, m); const p = penalty(mat, size); if (p < bestP) { bestP = p; best = mat; } }
    return { size, isDark: (r, c) => !!best[r][c] };
  }

  return { encode };
})();

function renderQR(text) {
  const slot = document.getElementById('qr-slot');
  const url = document.getElementById('qr-url');
  url.textContent = text;
  try {
    const { size, isDark } = QR.encode(text);
    const quiet = 4, mods = size + quiet * 2;
    const target = 132, dpr = Math.min(devicePixelRatio || 1, 3);
    const px = Math.max(2, Math.floor(target / mods));
    const dim = px * mods;
    const cv = document.createElement('canvas');
    cv.width = dim * dpr; cv.height = dim * dpr;
    cv.style.width = dim + 'px'; cv.style.height = dim + 'px';
    const g = cv.getContext('2d');
    g.scale(dpr, dpr);
    g.fillStyle = '#f7f7f2'; g.fillRect(0, 0, dim, dim);
    g.fillStyle = '#12141a';
    for (let r = 0; r < size; r++) for (let c = 0; c < size; c++)
      if (isDark(r, c)) g.fillRect((c + quiet) * px, (r + quiet) * px, px, px);
    slot.innerHTML = ''; slot.appendChild(cv);
  } catch (e) {
    // graceful, dependency-free fallback — never a CDN
    slot.innerHTML = '<div class="placeholder">QR unavailable<br>join at<br>' +
      String(text).replace(/[&<>]/g, s => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[s])) + '</div>';
    console.warn('[beeline] QR fell back to placeholder:', e);
  }
}

/* ------------------------------------------------------------------ *
 * 1. Floor plan geometry — logical 1000 x 680 space, letterboxed.
 *    The floor plan is now built from whatever areas the backend
 *    reports (SSE graph frame -> payload.zones). We compute an auto
 *    grid for any 1..8 areas so the map re-lays-out when a venue is
 *    reconfigured, scanned, or optimized. Nothing here is hardcoded to
 *    a specific venue.
 * ------------------------------------------------------------------ */
const FLOOR = { x: 34, y: 70, w: 936, h: 566 };   // inner boundary of the floor
let ZONE_LIST = [];                                // current area names (ordered)
let ZONES = {};                                    // name -> { x, y, w, h }

// Fallback set only for offline mock mode; live mode reads real areas.
const MOCK_ZONES = ['Window', 'Coffee', 'Stage', 'Cafe'];

// Auto-grid: columns = 2 for 1..4 areas (1 for a single area), else
// ceil(sqrt(n)). Rectangles fill the floor with a gap; a short final row
// is centered so the plan stays balanced.
function layoutZones(names) {
  const clean = (names || []).map(s => String(s).trim()).filter(Boolean).slice(0, 8);
  ZONE_LIST = clean.length ? clean : ['Room'];
  const n = ZONE_LIST.length;
  const cols = n <= 4 ? Math.min(2, n) : Math.ceil(Math.sqrt(n));
  const rows = Math.ceil(n / cols);
  const gap = 22, padX = 26, padY = 24;
  const innerX = FLOOR.x + padX, innerY = FLOOR.y + padY;
  const innerW = FLOOR.w - padX * 2, innerH = FLOOR.h - padY * 2;
  const cw = (innerW - gap * (cols - 1)) / cols;
  const ch = (innerH - gap * (rows - 1)) / rows;
  const next = {};
  ZONE_LIST.forEach((name, i) => {
    const r = Math.floor(i / cols), c = i % cols;
    const inRow = Math.min(cols, n - r * cols);           // cells in this row
    const rowOff = (cols - inRow) * (cw + gap) / 2;        // center a short row
    next[name] = { x: innerX + rowOff + c * (cw + gap), y: innerY + r * (ch + gap), w: cw, h: ch };
  });
  ZONES = next;
}
function zonesEqual(names) {
  const a = (names || []).map(s => String(s).trim()).filter(Boolean);
  if (a.length !== ZONE_LIST.length) return false;
  return a.every((z, i) => z === ZONE_LIST[i]);
}
// A person may report a zone that no longer exists; fall back gracefully.
function zoneOr(z) { return ZONES[z] ? z : ZONE_LIST[0]; }
function zoneRect(z) { return ZONES[zoneOr(z)]; }

const STATE_COLOR = { open: '#ffd36b', 'heads-down': '#5b8def', recharging: '#b98a3e', 'in-flow': '#b98cf0' };
const STATE_GLOW  = { open: 0.9, 'heads-down': 0.5, recharging: 0.28, 'in-flow': 0.6 };

function zoneCenter(z) { const r = zoneRect(z); return { x: r.x + r.w / 2, y: r.y + r.h / 2 }; }
function randInZone(z, seed) {
  const r = zoneRect(z);
  // padding scales with the cell so people sit inside any grid size, with
  // extra room at the top for the area label.
  const px = Math.max(18, Math.min(46, r.w * 0.15));
  const pyTop = Math.max(38, Math.min(58, r.h * 0.28));
  const pyBot = Math.max(16, Math.min(38, r.h * 0.14));
  return { x: r.x + px + Math.random() * Math.max(1, r.w - px * 2),
           y: r.y + pyTop + Math.random() * Math.max(1, r.h - pyTop - pyBot) };
}

/* Theme-aware palette for canvas-drawn elements (floor, areas, names).
   State + path colors stay constant across themes; the honey accent carries. */
const THEME_CANVAS = {
  light: {
    floorStroke: 'rgba(70,80,110,.28)',
    zoneFill:    'rgba(70,90,150,0.045)',
    zoneStroke:  'rgba(70,90,140,.22)',
    zoneLabel:   'rgba(60,66,88,.78)',
    zoneOpen:    'rgba(200,140,40,.85)',
    nameText:    'rgba(40,44,58,.94)',
    heatBase:    'rgba(230,160,50,',   // + alpha)
  },
  dark: {
    floorStroke: 'rgba(120,130,160,.18)',
    zoneFill:    'rgba(150,160,190,0.028)',
    zoneStroke:  'rgba(140,150,185,.22)',
    zoneLabel:   'rgba(180,190,215,.62)',
    zoneOpen:    'rgba(255,211,107,.5)',
    nameText:    'rgba(238,241,247,.92)',
    heatBase:    'rgba(255,196,96,',   // + alpha)
  },
};
let CV = THEME_CANVAS.light;

/* ------------------------------------------------------------------ *
 * 2. State
 * ------------------------------------------------------------------ */
const people = new Map();       // id -> {id,label,role,zone,state,x,y,tx,ty,born}
let energy = null;              // {zone: {open, 'heads-down', recharging, 'in-flow'}} or null
let activePath = null;          // animated beeline
const stats = { people: 0, open: 0, met: 0, intros: 0 };
let LIVE = false;
let es = null;
let guideActive = false;   // true while the guided demo plays (see §8b)

/* ------------------------------------------------------------------ *
 * 3. Canvas setup + transform
 * ------------------------------------------------------------------ */
const mapEl = document.getElementById('map');
const canvas = document.getElementById('floor');
const ctx = canvas.getContext('2d');
let W = 0, H = 0, scale = 1, offX = 0, offY = 0;
const DPR = Math.min(devicePixelRatio || 1, 2);

function computeView() {
  const r = mapEl.getBoundingClientRect();
  W = r.width; H = r.height;
  canvas.width = Math.round(W * DPR); canvas.height = Math.round(H * DPR);
  scale = Math.min(W / 1000, H / 680);
  offX = (W - 1000 * scale) / 2; offY = (H - 680 * scale) / 2;
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
}
const TX = x => offX + x * scale;
const TY = y => offY + y * scale;
const TS = s => s * scale;
addEventListener('resize', computeView);

/* ------------------------------------------------------------------ *
 * 4. Drawing
 * ------------------------------------------------------------------ */
function roundRect(x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function drawFloor() {
  // outer boundary of the floor
  ctx.lineWidth = Math.max(1, TS(2));
  ctx.strokeStyle = CV.floorStroke;
  roundRect(TX(FLOOR.x), TY(FLOOR.y), TS(FLOOR.w), TS(FLOOR.h), TS(18));
  ctx.stroke();

  const labelPx = ZONE_LIST.length > 6 ? 15 : 19;
  for (const z of ZONE_LIST) {
    const r = ZONES[z];
    if (!r) continue;
    // energy heat tint (stretch; degrades to flat if no energy frames)
    let heat = 0;
    if (energy && energy[z]) {
      const e = energy[z];
      const total = (e.open || 0) + (e['heads-down'] || 0) + (e.recharging || 0) + (e['in-flow'] || 0);
      heat = Math.min(1, (e.open || 0) / 4) * 0.5 + Math.min(1, total / 6) * 0.15;
    }
    roundRect(TX(r.x), TY(r.y), TS(r.w), TS(r.h), TS(14));
    ctx.fillStyle = heat > 0 ? `${CV.heatBase}${0.05 + heat * 0.16})` : CV.zoneFill;
    ctx.fill();
    ctx.lineWidth = Math.max(1, TS(1.4));
    ctx.strokeStyle = CV.zoneStroke;
    ctx.stroke();

    // area label
    ctx.fillStyle = CV.zoneLabel;
    ctx.font = `700 ${TS(labelPx)}px "Nunito", ui-sans-serif, system-ui`;
    ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
    ctx.fillText(z.toUpperCase(), TX(r.x + 20), TY(r.y + 30));
    // count of open people in area
    let openHere = 0; for (const p of people.values()) if (zoneOr(p.zone) === z && p.state === 'open') openHere++;
    if (openHere > 0) {
      ctx.fillStyle = CV.zoneOpen;
      ctx.font = `600 ${TS(12)}px "Nunito", ui-sans-serif, system-ui`;
      ctx.fillText(openHere + ' open', TX(r.x + 20), TY(r.y + 48));
    }
  }
}

function drawPath(now) {
  if (!activePath) return;
  const p = activePath;
  const age = now - p.t0;
  const prog = Math.min(1, age / p.dur);
  const pts = p.pts;

  // cumulative lengths for even-speed travel
  let totalLen = 0; const seg = [];
  for (let i = 1; i < pts.length; i++) { const d = Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y); seg.push(d); totalLen += d; }
  const walked = prog * totalLen;

  // faint full route
  ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  ctx.setLineDash([]);
  ctx.strokeStyle = 'rgba(255,216,132,.22)';
  ctx.lineWidth = TS(3);
  ctx.beginPath(); ctx.moveTo(TX(pts[0].x), TY(pts[0].y));
  for (let i = 1; i < pts.length; i++) ctx.lineTo(TX(pts[i].x), TY(pts[i].y));
  ctx.stroke();

  // bright walked portion
  ctx.strokeStyle = 'rgba(255,216,132,.95)';
  ctx.lineWidth = TS(4.5);
  ctx.shadowColor = 'rgba(255,200,110,.7)'; ctx.shadowBlur = TS(14);
  ctx.beginPath(); ctx.moveTo(TX(pts[0].x), TY(pts[0].y));
  let acc = 0, head = pts[0];
  for (let i = 1; i < pts.length; i++) {
    const d = seg[i - 1];
    if (acc + d <= walked) { ctx.lineTo(TX(pts[i].x), TY(pts[i].y)); acc += d; head = pts[i]; }
    else { const t = Math.max(0, (walked - acc) / d); const hx = pts[i - 1].x + (pts[i].x - pts[i - 1].x) * t; const hy = pts[i - 1].y + (pts[i].y - pts[i - 1].y) * t; ctx.lineTo(TX(hx), TY(hy)); head = { x: hx, y: hy }; acc = walked; break; }
  }
  ctx.stroke();
  ctx.shadowBlur = 0;

  // comet head
  if (prog < 1) {
    ctx.beginPath(); ctx.arc(TX(head.x), TY(head.y), TS(7), 0, 7);
    ctx.fillStyle = '#fff2cf'; ctx.shadowColor = 'rgba(255,210,120,.95)'; ctx.shadowBlur = TS(18); ctx.fill(); ctx.shadowBlur = 0;
  }

  // waypoint markers + labels
  for (let i = 0; i < pts.length; i++) {
    const w = pts[i], px = TX(w.x), py = TY(w.y);
    if (w.kind === 'you') {
      ctx.beginPath(); ctx.arc(px, py, TS(11), 0, 7); ctx.strokeStyle = 'rgba(255,216,132,.9)'; ctx.lineWidth = TS(2.5); ctx.stroke();
      label('YOU · ' + (w.label || ''), px, py - TS(20), '#ffd884');
    } else if (w.kind === 'target') {
      const pulse = 1 + Math.sin(now / 260) * 0.18;
      ctx.beginPath(); ctx.arc(px, py, TS(14) * pulse, 0, 7); ctx.strokeStyle = 'rgba(255,216,132,.85)'; ctx.lineWidth = TS(2.5); ctx.stroke();
      ctx.beginPath(); ctx.arc(px, py, TS(7), 0, 7); ctx.fillStyle = '#ffd884'; ctx.fill();
      label('MEET · ' + (w.label || ''), px, py - TS(24), '#ffe6a6');
    } else {
      // connector / hop
      ctx.save(); ctx.translate(px, py); ctx.rotate(Math.PI / 4);
      ctx.fillStyle = '#bcd0ff'; const s = TS(8); ctx.fillRect(-s, -s, s * 2, s * 2); ctx.restore();
      if (w.reason) label(w.label ? w.label + ' · ' + w.reason : w.reason, px, py - TS(18), '#bcd0ff');
      else if (w.label) label(w.label, px, py - TS(18), '#bcd0ff');
    }
  }
}
function label(text, x, y, color) {
  ctx.font = `700 ${TS(12.5)}px "Nunito", ui-sans-serif, system-ui`;
  ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
  const w = ctx.measureText(text).width;
  ctx.fillStyle = 'rgba(8,9,13,.72)';
  roundRect(x - w / 2 - TS(7), y - TS(14), w + TS(14), TS(19), TS(6)); ctx.fill();
  ctx.fillStyle = color; ctx.fillText(text, x, y);
}

function drawPeople(now) {
  for (const p of people.values()) {
    // ease toward target position
    p.x += (p.tx - p.x) * 0.12;
    p.y += (p.ty - p.y) * 0.12;
    const px = TX(p.x), py = TY(p.y);
    const color = STATE_COLOR[p.state] || '#cfd3df';
    const glow = STATE_GLOW[p.state] ?? 0.5;
    const age = now - p.born, grow = Math.min(1, age / 420);
    const r = TS(8.5) * (0.4 + 0.6 * grow);

    // arrival pulse
    if (age < 900) {
      ctx.beginPath(); ctx.arc(px, py, r + (1 - age / 900) * TS(26), 0, 7);
      ctx.strokeStyle = `rgba(255,211,107,${0.4 * (1 - age / 900)})`; ctx.lineWidth = TS(1.6); ctx.stroke();
    }
    // open pulse ring (routable people breathe)
    if (p.state === 'open') {
      const pulse = 0.5 + 0.5 * Math.sin(now / 520 + (p.phase || 0));
      ctx.beginPath(); ctx.arc(px, py, r + TS(4) + pulse * TS(4), 0, 7);
      ctx.strokeStyle = `rgba(255,211,107,${0.10 + pulse * 0.14})`; ctx.lineWidth = TS(1.4); ctx.stroke();
    }
    // dot
    ctx.beginPath(); ctx.arc(px, py, r, 0, 7);
    ctx.fillStyle = color; ctx.shadowColor = color; ctx.shadowBlur = TS(14) * glow;
    ctx.globalAlpha = grow; ctx.fill(); ctx.globalAlpha = 1; ctx.shadowBlur = 0;

    // name
    ctx.font = `600 ${TS(12.5)}px "Nunito", ui-sans-serif, system-ui`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
    ctx.fillStyle = CV.nameText;
    ctx.fillText(p.label || p.id, px, py - r - TS(7));
  }
}

function frame() {
  const now = performance.now();
  ctx.clearRect(0, 0, W, H);
  drawFloor();
  drawPath(now);
  drawPeople(now);
  if (activePath && now - activePath.t0 > activePath.dur + activePath.hold) activePath = null;
  requestAnimationFrame(frame);
}

/* ------------------------------------------------------------------ *
 * 5. Ingest — the frozen SSE contract §4.6
 * ------------------------------------------------------------------ */
function reconcilePeople(nodes, zones) {
  // Rebuild the floor plan whenever the reported areas change, then reseat
  // anyone whose area moved (or vanished) into a valid area.
  if (zones && zones.length && !zonesEqual(zones)) {
    layoutZones(zones);
    for (const p of people.values()) { const z = zoneOr(p.zone); const pt = randInZone(z); p.tx = pt.x; p.ty = pt.y; }
    refreshConfigZones();
  }
  const seen = new Set();
  for (const n of nodes) {
    if (n.kind !== 'person') continue;   // capabilities/topics are not on the floor plan
    seen.add(n.id);
    const existing = people.get(n.id);
    if (!existing) {
      const pt = randInZone(n.zone);
      people.set(n.id, { id: n.id, label: n.label, role: n.role, zone: n.zone, state: n.state,
        x: pt.x, y: pt.y, tx: pt.x, ty: pt.y, born: performance.now(), phase: Math.random() * 6 });
    } else {
      existing.label = n.label; existing.role = n.role;
      if (n.state) existing.state = n.state;
      if (n.zone && n.zone !== existing.zone) moveTo(existing, n.zone);
    }
  }
  for (const id of [...people.keys()]) if (!seen.has(id)) people.delete(id);
  recomputeOpen();
}
function moveTo(p, zone) { p.zone = zone; const pt = randInZone(zone); p.tx = pt.x; p.ty = pt.y; }
function recomputeOpen() { let o = 0; for (const p of people.values()) if (p.state === 'open') o++; stats.open = o; setCounters(); }

function handleEvent(ev) {
  const t = ev.type, pl = ev.payload || {};
  if (t === 'position' && people.has(pl.id)) moveTo(people.get(pl.id), pl.zone);
  else if (t === 'state' && people.has(pl.id)) { people.get(pl.id).state = pl.state; recomputeOpen(); }
  else if (t === 'checkin' && !people.has(pl.id)) {
    const pt = randInZone(pl.zone);
    people.set(pl.id, { id: pl.id, label: pl.name, role: pl.role, zone: pl.zone, state: pl.state || 'open',
      x: pt.x, y: pt.y, tx: pt.x, ty: pt.y, born: performance.now(), phase: Math.random() * 6 });
    recomputeOpen();
  }
}

function resolveWaypoint(hop) {
  // try to resolve a hop to a real person dot, else fall to its zone center
  if (hop.id && people.has(hop.id)) { const p = people.get(hop.id); return { x: p.x, y: p.y, label: p.label }; }
  if (hop.name) { for (const p of people.values()) if ((p.label || '').toLowerCase() === hop.name.toLowerCase()) return { x: p.x, y: p.y, label: p.label }; }
  const c = zoneCenter(hop.zone); return { x: c.x, y: c.y, label: hop.name || '' };
}

function handleOutbox(n) {
  stats.intros++; setCounters();
  addIntro(n);

  // build the animated beeline: you -> hops/connector -> target
  const you = people.get(n.to_id);
  const target = people.get(n.about_id);
  const pts = [];
  if (you) pts.push({ x: you.x, y: you.y, kind: 'you', label: n.to });
  else { const c = zoneCenter(ZONE_LIST[0]); pts.push({ x: c.x, y: c.y, kind: 'you', label: n.to }); }

  const hops = (n.route && n.route.hops) ? n.route.hops : [];
  if (hops.length) {
    for (const h of hops) { const w = resolveWaypoint(h); pts.push({ x: w.x, y: w.y, kind: 'hop', label: w.label || h.name, reason: h.reason }); }
  } else if (n.connector && n.connector.connector_name) {
    const w = resolveWaypoint({ id: n.connector.connector_id, name: n.connector.connector_name });
    pts.push({ x: w.x, y: w.y, kind: 'hop', label: n.connector.connector_name, reason: 'knows ' + n.about });
  }

  if (target) pts.push({ x: target.x, y: target.y, kind: 'target', label: n.about });
  else { const c = zoneCenter((n.route && n.route.target_zone) || ZONE_LIST[0]); pts.push({ x: c.x, y: c.y, kind: 'target', label: n.about }); }

  activePath = { pts, t0: performance.now(), dur: 2600, hold: 9000 };
  showMatchCard(n);
}

/* ------------------------------------------------------------------ *
 * 6. Panel rendering
 * ------------------------------------------------------------------ */
const esc = s => String(s ?? '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
const traceList = document.getElementById('trace-list');
const introList = document.getElementById('intro-list');
let firstTrace = true, firstIntro = true, traceN = 0, introN = 0;

function stepStatus(s) {
  const v = s.status || (s.approved === true ? 'approved' : s.approved === false ? 'vetoed' : (s.flag || ''));
  return String(v).toLowerCase();
}
function addTrace(rec) {
  if (firstTrace) { traceList.innerHTML = ''; firstTrace = false; }
  const steps = rec.steps || [];
  const empath = steps.find(s => (s.step || '').indexOf('empath') === 0);
  let cls = '';
  if (empath) { const st = stepStatus(empath); cls = st === 'approved' ? 'approved' : st === 'held' ? 'held' : st ? 'vetoed' : ''; }
  const p = (rec.event && rec.event.payload) || {};
  const who = p.name || p.id || p.a || (rec.event && rec.event.type) || '';
  const ms = rec.ms != null ? rec.ms : (rec.latency_ms != null ? rec.latency_ms : '');

  const el = document.createElement('div');
  el.className = 'trace ' + cls;
  el.innerHTML =
    `<div class="ev"><b>${esc(rec.event ? rec.event.type : 'event')}</b> · ${esc(who)}${ms !== '' ? ' · ' + esc(ms) + 'ms' : ''}</div>` +
    steps.map(s => {
      const st = stepStatus(s);
      let tag = '';
      if ((s.step || '').indexOf('empath') === 0 && st) {
        const kind = st === 'approved' ? 'ok' : st === 'held' ? 'hold' : 'no';
        tag = `<span class="tag ${kind}">${esc(st)}</span>`;
      }
      return `<div class="step"><span class="name">${esc(s.step)}</span><span class="detail">${esc(s.detail)}</span>${tag}</div>`;
    }).join('');
  traceList.prepend(el);
  while (traceList.children.length > 40) traceList.lastChild.remove();
  document.getElementById('trace-count').textContent = ++traceN;
}

function addIntro(n) {
  if (firstIntro) { introList.innerHTML = ''; firstIntro = false; }
  const comps = (n.match && n.match.complements) || [];
  const shared = (n.match && n.match.shared_topics) || [];
  const el = document.createElement('div');
  el.className = 'intro';
  el.innerHTML =
    `<div class="to">to <b>${esc(n.to)}</b> · about <b>${esc(n.about)}</b></div>` +
    `<div class="msg">${esc(n.message)}</div>` +
    `<div class="foot">` +
      comps.map(c => `<span class="mchip">${esc(c)}</span>`).join('') +
      shared.map(t => `<span class="mchip via">${esc(t)}</span>`).join('') +
      (n.connector && n.connector.connector_name ? `<span class="mchip via">via ${esc(n.connector.connector_name)}</span>` : '') +
    `</div>`;
  introList.prepend(el);
  while (introList.children.length > 25) introList.lastChild.remove();
  document.getElementById('intro-count').textContent = ++introN;
}

const mc = document.getElementById('matchcard');
let mcTimer = null;
function showMatchCard(n) {
  document.getElementById('mc-head').textContent = `${n.to}, meet ${n.about}`;
  document.getElementById('mc-msg').textContent = n.message || '';
  document.getElementById('mc-why').textContent = n.why || '';
  const comps = (n.match && n.match.complements) || [];
  const shared = (n.match && n.match.shared_topics) || [];
  const matchEl = document.getElementById('mc-match');
  matchEl.innerHTML =
    comps.map(c => `<div class="pair"><span class="chip">${esc(c)}</span><span class="arrow">&#8596;</span><span>what ${esc(n.to)} is looking for</span></div>`).join('') +
    (shared.length ? `<div class="pair">${shared.map(t => `<span class="chip topic">${esc(t)}</span>`).join('')}<span class="arrow">shared</span></div>` : '');
  const route = document.getElementById('mc-route');
  const hops = (n.route && n.route.hops) || [];
  if (n.connector && n.connector.connector_name)
    route.innerHTML = `Go to <b>${esc((n.route && n.route.target_zone) || '')}</b>. Say hi to <b>${esc(n.connector.connector_name)}</b> on the way` +
      (hops[0] && hops[0].reason ? ` &mdash; ${esc(hops[0].reason)}.` : '.');
  else if (n.route && n.route.target_zone)
    route.innerHTML = `Head to <b>${esc(n.route.target_zone)}</b>.`;
  else route.innerHTML = '';
  mc.classList.add('show');
  clearTimeout(mcTimer); mcTimer = setTimeout(() => mc.classList.remove('show'), 11000);
}

function setCounters() {
  document.getElementById('n-people').textContent = stats.people = people.size;
  document.getElementById('n-open').textContent = stats.open;
  document.getElementById('n-met').textContent = stats.met;
  document.getElementById('n-intros').textContent = stats.intros;
}
function setStats(s) {
  if (s.people != null) stats.people = s.people;
  if (s.met != null) stats.met = s.met;
  if (s.nudges != null) stats.intros = s.nudges;
  document.getElementById('n-met').textContent = stats.met;
  document.getElementById('n-intros').textContent = stats.intros;
  document.getElementById('n-people').textContent = people.size || stats.people;
  if (s.backends) {
    const meta = document.getElementById('meta');
    meta.querySelectorAll('.chip-backend').forEach(e => e.remove());
    for (const [k, v] of Object.entries(s.backends)) {
      const c = document.createElement('span'); c.className = 'chip-meta chip-backend'; c.innerHTML = `${esc(k)} <b>${esc(v)}</b>`; meta.appendChild(c);
    }
  }
}
function setEnergy(rows) {
  energy = {};
  for (const r of rows || []) energy[r.zone] = r;
}

/* ------------------------------------------------------------------ *
 * 7. Frame router (shared by live SSE + mock feed)
 * ------------------------------------------------------------------ */
function ingest(kind, data) {
  // While the guided demo runs, ignore live frames so the scripted story
  // stays deterministic. Live resumes automatically once it ends.
  if (guideActive && kind !== 'error') return;
  if (kind === 'graph') reconcilePeople(data.nodes || [], data.zones);
  else if (kind === 'stats') setStats(data);
  else if (kind === 'event') handleEvent(data);
  else if (kind === 'trace') addTrace(data);
  else if (kind === 'outbox') handleOutbox(data);
  else if (kind === 'energy') setEnergy(data);
  else if (kind === 'reset') doReset();
  else if (kind === 'error') console.warn('[beeline] stream error:', data);
}
function doReset() {
  people.clear(); energy = null; activePath = null;
  stats.people = stats.open = stats.met = stats.intros = 0; setCounters();
  traceList.innerHTML = '<div class="empty">Waiting for the first event.</div>';
  introList.innerHTML = '<div class="empty">None yet.</div>';
  firstTrace = firstIntro = true; traceN = introN = 0;
  document.getElementById('trace-count').textContent = '';
  document.getElementById('intro-count').textContent = '';
  mc.classList.remove('show');
}

/* ------------------------------------------------------------------ *
 * 8. MOCK data (matches §4.6 exactly) + a dev choreography
 * ------------------------------------------------------------------ */
const MOCK_SNAPSHOT = {
  zones: MOCK_ZONES,
  nodes: [
    { id: 'a1', kind: 'person', label: 'Priya',  role: 'ml engineer',      zone: 'Window', state: 'open' },
    { id: 'a2', kind: 'person', label: 'Aisha',  role: 'founder',          zone: 'Window', state: 'heads-down' },
    { id: 'a3', kind: 'person', label: 'Chen',   role: 'infra lead',       zone: 'Coffee', state: 'open' },
    { id: 'a4', kind: 'person', label: 'Marcus', role: 'product designer', zone: 'Coffee', state: 'open' },
    { id: 'a5', kind: 'person', label: 'Lin',    role: 'researcher',       zone: 'Stage',  state: 'in-flow' },
    { id: 'a6', kind: 'person', label: 'Dev',    role: 'ios engineer',     zone: 'Stage',  state: 'open' },
    { id: 'a7', kind: 'person', label: 'Sofia',  role: 'design lead',      zone: 'Cafe',   state: 'recharging' },
    { id: 'a8', kind: 'person', label: 'Omar',   role: 'data scientist',   zone: 'Cafe',   state: 'open' },
    { id: 'a9', kind: 'person', label: 'Tara',   role: 'pm',               zone: 'Cafe',   state: 'open' },
    { id: 'a10', kind: 'person', label: 'Noah',  role: 'backend eng',      zone: 'Stage',  state: 'heads-down' },
    { id: 'a11', kind: 'person', label: 'Grace', role: 'gtm',              zone: 'Window', state: 'open' },
    // capability + topic nodes exist in the snapshot (no incident edges by design)
    { id: 'topic:agent memory', kind: 'topic', label: 'agent memory', weight: 4 },
    { id: 'topic:retrieval', kind: 'topic', label: 'retrieval', weight: 3 },
    { id: 'cap:hiring-design', kind: 'capability', label: 'hiring: design' },
    { id: 'cap:looking-design', kind: 'capability', label: 'looking for design work' },
  ],
  edges: [
    { source: 'a1', target: 'topic:agent memory', kind: 'interest' },
    { source: 'a4', target: 'topic:agent memory', kind: 'interest' },
    { source: 'a1', target: 'a3', kind: 'met' },
    { source: 'a3', target: 'a4', kind: 'met' },
  ],
};
const MOCK_STATS = { people: 11, topics: 2, capabilities: 2, met: 2, nudges: 0, feedback: 0,
  backends: { memory: 'local', motion: 'local', stream: 'local', guild: 'local' } };
const MOCK_ENERGY = [
  { zone: 'Window', open: 2, 'heads-down': 1, recharging: 0, 'in-flow': 0 },
  { zone: 'Coffee', open: 2, 'heads-down': 0, recharging: 0, 'in-flow': 0 },
  { zone: 'Stage',  open: 1, 'heads-down': 1, recharging: 0, 'in-flow': 1 },
  { zone: 'Cafe',   open: 2, 'heads-down': 0, recharging: 1, 'in-flow': 0 },
];
// the money-shot introduction (matches §4.6 outbox shape)
const MOCK_OUTBOX = {
  at: Date.now(), to: 'Priya', to_id: 'a1', about: 'Marcus', about_id: 'a4',
  message: 'Marcus is a product designer looking for his next team, and you are hiring for design. Ask him about the agent-memory tool he prototyped.',
  why: 'Your ask (design hire) matches his offer, and you both care about agent memory.',
  connector: { connector_id: 'a3', connector_name: 'Chen', via_topics: ['infra'] },
  route: { target_zone: 'Coffee', hops: [{ id: 'a3', name: 'Chen', zone: 'Coffee', reason: 'knows Marcus' }] },
  match: { complements: ['hiring: design'], shared_topics: ['agent memory'] },
};
const MOCK_TRACE_DELIVER = {
  event: { type: 'checkin', payload: { id: 'a4', name: 'Marcus' } }, ms: 47,
  steps: [
    { step: 'memory.write', detail: 'persisted check-in for Marcus (Coffee, open)' },
    { step: 'matchmaker', detail: 'best of 3: Priya — complementary hiring:design' },
    { step: 'empath', detail: 'Priya and Marcus both open, cleared', status: 'approved' },
    { step: 'icebreaker', detail: 'opener written, names the design ask + Chen' },
    { step: 'router', detail: 'route to Coffee via Chen (knows Marcus)' },
    { step: 'action', detail: 'beeline delivered to Priya' },
  ],
};
const MOCK_TRACE_HOLD = {
  event: { type: 'state', payload: { id: 'a7', name: 'Sofia' } }, ms: 12,
  steps: [
    { step: 'memory.write', detail: 'Sofia set state → recharging' },
    { step: 'matchmaker', detail: 'Omar wanted to meet Sofia (design mentoring)' },
    { step: 'empath', detail: 'Sofia is recharging, holding until she returns to open', status: 'held' },
  ],
};
const MOCK_TRACE_VETO = {
  event: { type: 'checkin', payload: { id: 'a10', name: 'Noah' } }, ms: 9,
  steps: [
    { step: 'memory.write', detail: 'persisted check-in for Noah' },
    { step: 'matchmaker', detail: 'candidate Dev, only generic overlap (AI)' },
    { step: 'empath', detail: 'overlap too generic, vetoed', status: 'vetoed' },
  ],
};
const MOCK_TRACE_FEEDBACK = {
  event: { type: 'feedback', payload: { id: 'a1' } }, ms: 8,
  steps: [
    { step: 'memory.write', detail: 'Priya tapped not-for-me on Dev' },
    { step: 'empath', detail: 'downweight Dev + similar; next match shifts', status: 'approved' },
  ],
};
// a second, different introduction — someone who OFFERS exactly what another
// person is LOOKING FOR, so viewers see the match variety.
const MOCK_OUTBOX_2 = {
  at: Date.now(), to: 'Grace', to_id: 'a11', about: 'Omar', about_id: 'a8',
  message: 'Omar is a data scientist who has shipped the growth analytics you are trying to stand up. Ask him how he sized the first funnel.',
  why: 'You are looking for analytics help, and that is exactly what Omar offers.',
  connector: { connector_id: 'a9', connector_name: 'Tara', via_topics: ['growth'] },
  route: { target_zone: 'Cafe', hops: [{ id: 'a9', name: 'Tara', zone: 'Cafe', reason: 'knows Omar' }] },
  match: { complements: ['offers: growth analytics'], shared_topics: ['retrieval'] },
};
const MOCK_TRACE_DELIVER_2 = {
  event: { type: 'checkin', payload: { id: 'a8', name: 'Omar' } }, ms: 39,
  steps: [
    { step: 'memory.write', detail: 'persisted check-in for Omar (Cafe, open)' },
    { step: 'matchmaker', detail: 'best of 4: Grace needs analytics, Omar offers it' },
    { step: 'empath', detail: 'Grace and Omar both open, cleared', status: 'approved' },
    { step: 'icebreaker', detail: 'opener written, names the analytics ask + Tara' },
    { step: 'router', detail: 'route to Cafe via Tara (knows Omar)' },
    { step: 'action', detail: 'beeline delivered to Grace' },
  ],
};

function mockFill() {
  doReset();
  layoutZones(MOCK_SNAPSHOT.zones);
  refreshConfigZones();
  setStats(MOCK_STATS);
  setEnergy(MOCK_ENERGY);
  // stagger arrivals so the room reads as filling live
  const persons = MOCK_SNAPSHOT.nodes.filter(n => n.kind === 'person');
  let i = 0;
  const timer = setInterval(() => {
    reconcilePeople(persons.slice(0, ++i), MOCK_SNAPSHOT.zones);
    if (i >= persons.length) clearInterval(timer);
  }, 180);
}
function mockMove() {
  const ppl = [...people.values()];
  if (!ppl.length) return;
  const p = ppl[Math.floor(Math.random() * ppl.length)];
  const other = ZONE_LIST.filter(z => z !== p.zone);
  const zone = other[Math.floor(Math.random() * other.length)];
  handleEvent({ type: 'position', payload: { id: p.id, zone } });
}
function mockRecharge() {
  handleEvent({ type: 'state', payload: { id: 'a7', name: 'Sofia', state: 'recharging' } });
  if (people.has('a7')) people.get('a7').state = 'recharging';
  recomputeOpen();
  addTrace(MOCK_TRACE_HOLD);
}
function mockIntro() {
  addTrace(MOCK_TRACE_DELIVER);
  handleOutbox({ ...MOCK_OUTBOX, at: Date.now() });
  stats.met++; setCounters();
}
// deliver the first intro's PATH only (its agent trace is shown a beat earlier
// in the guided demo, so this avoids adding the trace twice).
function mockDeliverPath() {
  handleOutbox({ ...MOCK_OUTBOX, at: Date.now() });
  stats.met++; setCounters();
}
// a second, distinct delivered intro (trace + path together).
function mockIntro2() {
  addTrace(MOCK_TRACE_DELIVER_2);
  handleOutbox({ ...MOCK_OUTBOX_2, at: Date.now() });
  stats.met++; setCounters();
}

// a self-contained "Play demo" that hits every beat, deterministically-ish
function mockDemo() {
  mockFill();
  const seq = [
    [2600, () => addTrace(MOCK_TRACE_VETO)],
    [3400, () => mockMove()],
    [4200, () => mockMove()],
    [5200, () => mockRecharge()],
    [7000, () => mockIntro()],
    [12500, () => { if (people.has('a7')) { people.get('a7').state = 'open'; recomputeOpen(); }
                    addTrace({ event: { type: 'feedback', payload: { id: 'a1' } }, ms: 8,
                      steps: [{ step: 'memory.write', detail: 'Priya tapped not-for-me on Dev' },
                              { step: 'empath', detail: 'downweight Dev + similar; next match shifts', status: 'approved' }] }); }],
  ];
  seq.forEach(([t, fn]) => setTimeout(fn, t));
}

/* ------------------------------------------------------------------ *
 * 8b. Guided, self-narrating demo — the centerpiece.
 *     Reuses the mock sequence engine (mockFill / mockMove / mockRecharge
 *     / mockIntro + the mock traces) so it needs no backend and is
 *     deterministic, and overlays plain-language captions + a progress
 *     bar so a viewer with no narration understands the pitch. Every
 *     caption is synced to the actual beat it describes.
 * ------------------------------------------------------------------ */
let guideTimers = [];
let guideRaf = 0;
let guideStartAt = 0;
let guideStarted = false;        // has the guided demo ever run this load
let guideAutoTimer = null;

// [atMs, caption, action] — the caption describes exactly what the action does.
function guideBeats() {
  return [
    [0,     'The problem: in a room of strangers, you meet whoever is nearby and miss the person you came for.',
            null],
    [4200,  'People check in with what they are looking for and what they can offer. Each check-in is a live event.',
            () => mockFill()],
    [8400,  'On the roadmap, signing in with LinkedIn fills this in from your background and recent work, so you barely type.',
            null],
    [12400, 'Beeline remembers everyone and matches on need versus offer, not just shared interests.',
            () => { mockMove(); setTimeout(() => { if (guideActive) mockMove(); }, 800); }],
    [16400, 'Four specialist agents decide who you should meet, and why.',
            () => addTrace(MOCK_TRACE_DELIVER)],
    [20400, 'It draws you a physical path: go here, and say hi to a mutual on the way.',
            () => mockDeliverPath()],
    [24800, 'Here is another: someone who offers exactly what another person is looking for.',
            () => mockIntro2()],
    [29200, 'One agent can say no. This match shared only a generic interest, so it was vetoed.',
            () => addTrace(MOCK_TRACE_VETO)],
    [33200, 'Someone is recharging, so it holds the intro. Nothing is ever sensed, you tap how you feel.',
            () => mockRecharge()],
    [37200, 'It learns from a thumbs-down and shifts the next suggestion.',
            () => { if (people.has('a7')) { people.get('a7').state = 'open'; recomputeOpen(); }
                    addTrace(MOCK_TRACE_FEEDBACK); }],
    [41200, 'Deploy it in any venue: scan the room with a photo, or type your own areas.',
            null],
    [45200, 'That is Beeline. Scan the QR to add yourself.',
            null],
  ];
}
const GUIDE_TOTAL = 49800;   // total run length; last caption reads to the end

function setGuideCaption(text, idx, total) {
  const cap = document.getElementById('guide-cap');
  const step = document.getElementById('guide-step');
  if (cap) cap.textContent = text;
  if (step) step.textContent = (idx + 1) + ' / ' + total;
}
function runGuideBar() {
  cancelAnimationFrame(guideRaf);
  const tick = () => {
    const el = document.getElementById('guide-bar-fill');
    if (!el) return;
    const pct = Math.min(1, (performance.now() - guideStartAt) / GUIDE_TOTAL);
    el.style.width = (pct * 100) + '%';
    if (pct < 1 && guideActive) guideRaf = requestAnimationFrame(tick);
  };
  guideRaf = requestAnimationFrame(tick);
}
function clearGuideTimers() {
  guideTimers.forEach(clearTimeout); guideTimers = [];
  cancelAnimationFrame(guideRaf);
}
function startGuidedDemo() {
  cancelAutoStart();
  clearGuideTimers();
  guideActive = true;
  guideStarted = true;
  document.body.classList.add('guiding');
  const g = document.getElementById('guide');
  // Un-hide, commit the hidden base state with a forced reflow, then add
  // .show so the slide-up transition runs. Using a reflow instead of rAF
  // keeps the reveal reliable even when rAF is throttled.
  if (g) { g.hidden = false; g.style.opacity = ''; g.style.transform = ''; void g.offsetWidth; g.classList.add('show'); }
  doReset();
  const beats = guideBeats();
  guideStartAt = performance.now();
  beats.forEach(([at, cap, fn], i) => {
    guideTimers.push(setTimeout(() => { setGuideCaption(cap, i, beats.length); if (fn) fn(); }, at));
  });
  guideTimers.push(setTimeout(finishGuidedDemo, GUIDE_TOTAL));
  runGuideBar();
}
function finishGuidedDemo() {
  // hold the closing caption + progress full; live frames resume.
  clearGuideTimers();
  guideActive = false;
  const bar = document.getElementById('guide-bar-fill');
  if (bar) bar.style.width = '100%';
}
function stopGuidedDemo() {
  clearGuideTimers();
  guideActive = false;
  document.body.classList.remove('guiding');
  const g = document.getElementById('guide');
  if (g) { g.classList.remove('show'); setTimeout(() => { g.hidden = true; }, 300); }
}
function cancelAutoStart() { if (guideAutoTimer) { clearTimeout(guideAutoTimer); guideAutoTimer = null; } }

/* ------------------------------------------------------------------ *
 * 9. Live stream + controls
 * ------------------------------------------------------------------ */
const post = (url, body) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }).catch(() => {});

function connectLive() {
  if (es) es.close();
  es = new EventSource('/api/stream');
  es.onmessage = m => { try { const { kind, data } = JSON.parse(m.data); ingest(kind, data); } catch (e) { console.warn(e); } };
  es.onerror = () => {};
  fetch('/api/stats').then(r => r.json()).then(setStats).catch(() => {});
  fetch('/api/energy').then(r => r.json()).then(setEnergy).catch(() => {});
  // seed the floor plan from the venue's real areas before the first graph frame
  fetch('/api/zones').then(r => r.json()).then(d => {
    if (d && d.zones && d.zones.length && !zonesEqual(d.zones)) { layoutZones(d.zones); refreshConfigZones(); }
  }).catch(() => {});
}
function setMode(live) {
  LIVE = live;
  document.getElementById('mode-label').textContent = live ? 'live' : 'mock';
  document.getElementById('meta-mode').textContent = live ? 'live' : 'mock';
  document.getElementById('mode-badge').classList.toggle('live', live);
  document.getElementById('btn-live').textContent = live ? 'use mock data' : 'use live stream';
  if (live) connectLive(); else if (es) { es.close(); es = null; }
}

// wire buttons — in mock mode they drive the mock feed; in live they POST.
// The dominant CTA launches the narrated guided demo (deterministic, no backend).
document.getElementById('btn-fill').onclick = () => { cancelAutoStart(); startGuidedDemo(); };
document.getElementById('btn-demo').onclick = () => { cancelAutoStart(); LIVE ? post('/api/seed', { delay: 0.8 }) : mockFill(); };
document.getElementById('btn-move').onclick = () => { cancelAutoStart(); mockMove(); }; // visual nudge either way
document.getElementById('btn-recharge').onclick = () => { cancelAutoStart(); LIVE ? post('/api/state', { id: 'a7', state: 'recharging' }) : mockRecharge(); };
document.getElementById('btn-intro').onclick = () => { cancelAutoStart(); LIVE ? post('/api/seed', { delay: 0.4 }) : mockIntro(); };
document.getElementById('btn-reset').onclick = () => { cancelAutoStart(); stopGuidedDemo(); LIVE ? post('/api/reset') : doReset(); };
document.getElementById('btn-live').onclick = () => { cancelAutoStart(); setMode(!LIVE); };

// guided-demo overlay controls
document.getElementById('guide-restart').onclick = startGuidedDemo;
document.getElementById('guide-skip').onclick = stopGuidedDemo;

/* ------------------------------------------------------------------ *
 * 9b. Theme — light by default, dark on toggle, choice persisted.
 * ------------------------------------------------------------------ */
const THEME_KEY = 'beeline-theme';
function applyTheme(name) {
  const t = name === 'dark' ? 'dark' : 'light';
  document.documentElement.dataset.theme = t;
  CV = THEME_CANVAS[t];
  const btn = document.getElementById('btn-theme');
  if (btn) {
    btn.textContent = t === 'dark' ? '☀' : '☾';   // sun when dark, moon when light
    btn.title = t === 'dark' ? 'Switch to light' : 'Switch to dark';
  }
  try { localStorage.setItem(THEME_KEY, t); } catch (e) {}
}
function initTheme() {
  let saved = 'light';
  try { saved = localStorage.getItem(THEME_KEY) || 'light'; } catch (e) {}
  applyTheme(saved);
}
document.getElementById('btn-theme').onclick = () =>
  applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');

/* ------------------------------------------------------------------ *
 * 9c. Configure space — areas editor, room scan, workflow optimize.
 *     For deploying the map in a new venue. Areas apply through
 *     POST /api/zones; the map re-lays-out from the next graph frame
 *     (live) or immediately (mock/offline).
 * ------------------------------------------------------------------ */
const cfgEl = document.getElementById('cfg');
const cfgScrim = document.getElementById('cfg-scrim');
const cfgZonesEl = document.getElementById('cfg-zones');
let cfgDraft = [];   // area names being edited

function openConfig() {
  // start from the venue's current areas (fall back to what the map shows)
  fetch('/api/zones').then(r => r.json()).then(d => {
    cfgDraft = (d && d.zones && d.zones.length) ? d.zones.slice() : ZONE_LIST.slice();
    renderConfigZones();
  }).catch(() => { cfgDraft = ZONE_LIST.slice(); renderConfigZones(); });
  cfgEl.hidden = false; cfgScrim.hidden = false;
}
function closeConfig() { cfgEl.hidden = true; cfgScrim.hidden = true; }

function renderConfigZones() {
  cfgZonesEl.innerHTML = '';
  cfgDraft.forEach((name, i) => {
    const row = document.createElement('div');
    row.className = 'cfg-zone';
    const input = document.createElement('input');
    input.type = 'text'; input.value = name; input.placeholder = 'Area name';
    input.oninput = () => { cfgDraft[i] = input.value; };
    const up = document.createElement('button');
    up.className = 'mv'; up.textContent = '↑'; up.title = 'Move up'; up.disabled = i === 0;
    up.onclick = () => { [cfgDraft[i - 1], cfgDraft[i]] = [cfgDraft[i], cfgDraft[i - 1]]; renderConfigZones(); };
    const down = document.createElement('button');
    down.className = 'mv'; down.textContent = '↓'; down.title = 'Move down'; down.disabled = i === cfgDraft.length - 1;
    down.onclick = () => { [cfgDraft[i + 1], cfgDraft[i]] = [cfgDraft[i], cfgDraft[i + 1]]; renderConfigZones(); };
    const rm = document.createElement('button');
    rm.className = 'mv rm'; rm.textContent = '✕'; rm.title = 'Remove';
    rm.onclick = () => { cfgDraft.splice(i, 1); renderConfigZones(); };
    row.append(input, up, down, rm);
    cfgZonesEl.appendChild(row);
  });
}
// keep the panel's list honest if the areas change underneath it (e.g. a scan
// applied, or a live graph frame with new areas arrives while it is open)
function refreshConfigZones() {
  if (cfgEl && !cfgEl.hidden) { cfgDraft = ZONE_LIST.slice(); renderConfigZones(); }
}

// apply a set of areas: POST to the backend when live; also lay out locally so
// mock / offline mode updates right away.
function applyZones(names) {
  const clean = names.map(s => String(s).trim()).filter(Boolean).slice(0, 8);
  if (!clean.length) return;
  layoutZones(clean);
  for (const p of people.values()) { const pt = randInZone(zoneOr(p.zone)); p.tx = pt.x; p.ty = pt.y; }
  cfgDraft = ZONE_LIST.slice(); renderConfigZones();
  post('/api/zones', { zones: clean });   // no-ops gracefully offline
}

document.getElementById('btn-config').onclick = openConfig;
document.getElementById('cfg-close').onclick = closeConfig;
cfgScrim.onclick = closeConfig;
document.getElementById('cfg-add').onclick = () => { cfgDraft.push('New area'); renderConfigZones(); };
document.getElementById('cfg-apply').onclick = () => applyZones(cfgDraft);

// --- scan the room ---------------------------------------------------
const scanOut = document.getElementById('cfg-scan-out');
document.getElementById('cfg-scan-file').onchange = ev => {
  const file = ev.target.files && ev.target.files[0];
  if (!file) return;
  scanOut.innerHTML = '<div class="cfg-busy">Reading the room from your photo.</div>';
  const reader = new FileReader();
  reader.onload = () => {
    fetch('/api/scan', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: reader.result }) })
      .then(r => r.json()).then(renderScan)
      .catch(() => { scanOut.innerHTML = '<div class="cfg-busy">The scan did not come back. Try again.</div>'; });
  };
  reader.readAsDataURL(file);
};
function renderScan(res) {
  const areas = (res && res.areas) || [];
  const landmarks = (res && res.landmarks) || [];
  let html = '<div class="cfg-result">';
  html += `<div class="src">from scan · ${esc(res && res.source || 'suggestion')}</div>`;
  if (res && res.summary) html += `<div class="summary">${esc(res.summary)}</div>`;
  if (areas.length) html += '<div class="cfg-chips">' + areas.map(a => `<span class="cfg-chip">${esc(a)}</span>`).join('') + '</div>';
  if (landmarks.length) html += landmarks.map(l =>
    `<div class="cfg-landmark"><b>${esc(l.name)}</b>${l.note ? ' <span>· ' + esc(l.note) + '</span>' : ''}</div>`).join('');
  if (areas.length) html += '<div class="cfg-row-actions"><button class="primary" id="cfg-scan-apply">Apply these areas</button></div>';
  html += '</div>';
  scanOut.innerHTML = html;
  const applyBtn = document.getElementById('cfg-scan-apply');
  if (applyBtn) applyBtn.onclick = () => { applyZones(areas); };
}

// --- optimize the room -----------------------------------------------
const optOut = document.getElementById('cfg-opt-out');
document.getElementById('cfg-optimize').onclick = () => {
  optOut.innerHTML = '<div class="cfg-busy">Reading the live room.</div>';
  fetch('/api/optimize', { method: 'POST' }).then(r => r.json()).then(res => {
    const sugs = (res && res.suggestions) || [];
    if (!sugs.length) { optOut.innerHTML = '<div class="cfg-busy">No suggestions right now. Fill the room and try again.</div>'; return; }
    optOut.innerHTML = `<div class="cfg-result"><div class="src">optimizer · ${esc(res.source || 'live')}</div>` +
      sugs.map(s => `<div class="cfg-sug"><div class="t">${esc(s.title)}</div><div class="d">${esc(s.detail)}</div></div>`).join('') +
      '</div>';
  }).catch(() => { optOut.innerHTML = '<div class="cfg-busy">The optimizer did not respond. Try again.</div>'; });
};

/* ------------------------------------------------------------------ *
 * 10. Boot
 * ------------------------------------------------------------------ */
function joinURL() {
  try { return location.origin + '/join'; } catch (e) { return '/join'; }
}
initTheme();
layoutZones(MOCK_ZONES);     // a placeholder plan so the map is never blank; real areas override
computeView();
renderQR(joinURL());
requestAnimationFrame(frame);

// default to mock unless ?live=1
const wantLive = new URLSearchParams(location.search).get('live') === '1';
setMode(wantLive);
if (!wantLive) mockFill();   // show a populated room immediately for verification

})();
