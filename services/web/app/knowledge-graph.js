"use client";
// The knowledge map's graph: d3-force physics drawn on a canvas. Nodes fall in from above and the physics
// lands them in one packed disc (slice K7): bubbles colored by cluster, a community found from the links themselves,
// with no lines at rest. Pointing at or clicking a node turns it purple and draws its links in blue. Pan, zoom,
// pinch, drag, keyboard focus, and filters that animate nodes out. Physics moves to a Web Worker past 2,000 nodes,
// and drawing stops once the graph settles, restarting on interaction.
import { createElement as h, useEffect, useMemo, useRef } from "react";
import { communities } from "./graph-clusters.js";
import { createPhysics, releaseTimes } from "./graph-physics.js";
import { createGraphWorker } from "./graph-worker-client.js";
import { TIERS, tierOf } from "./ui.js";

const WORKER_ABOVE = 2000;
const FADE_MS = 200; // edge fade-in
const SCALE_MS = 150; // filter scale in and out
const DIM = 0.15; // everything outside a clicked node's neighborhood
const HOVER_DIM = 0.45; // a gentler preview while pointing at a node
const MIX_MS = 150; // highlights fade in and out
const EGO_ALPHA = 0.9; // a picked node's links; no other lines are drawn
const COLORS = 10; // cluster colors, the --cluster-0 to --cluster-9 tokens, reused round the clusters
const BUBBLE_PX = 14; // fitting never zooms the typical bubble past this radius on screen, however few nodes there are
// The three kinds of link (ADR 0010), never mixed up: curated (a person's own relation) bold, recorded (written in a
// source: references, lineage, mentions) solid, inferred (similar meaning) dashed. A pair linked several ways shows its
// strongest kind.
const CURATED = 0, RECORDED = 1, INFERRED = 2;
export const LINK_STYLE = { relation: CURATED, "derived-from": CURATED, reference: RECORDED, lineage: RECORDED,
                            describes: RECORDED, mentions: RECORDED, semantic: INFERRED };
const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)).join(", ");
// The map paints with the page's own tokens (slice D2), so it follows the theme: light, dark, or the system's. They're
// read once and kept until the theme changes; each falls back to the light value where there's no page to read.
let PALETTE = null;
export function readPalette(get) {
  const hex = (name, fallback) => { const v = String(get(name) || "").trim(); return /^#[0-9a-f]{6}$/i.test(v) ? v : fallback; };
  return {
    edge: rgb(hex("--accent", "#1f6f61")), accent: hex("--accent", "#1f6f61"), ink: hex("--fg", "#121820"),
    paper: rgb(hex("--map-bg", "#f7f8fa")), label: rgb(hex("--fg-2", "#3a4552")), quiet: rgb(hex("--muted", "#5f6b7a")),
    tiers: Object.fromEntries(Object.entries(TIERS).map(([k, t]) => [k, rgb(hex(`--${k}`, t.color))])),
    clusters: ["#bfe3cf", "#f5c9d9", "#d8cdf3", "#c3daf3", "#f1e2ad", "#f6d1bd", "#bfe5e6", "#d5e5c2", "#ecd0e5", "#d3d8de"]
      .map((fallback, i) => rgb(hex(`--cluster-${i}`, fallback))), // pastels, one per cluster (slice K7)
    focus: hex("--map-focus", "#8b5cf6"), ego: rgb(hex("--map-ego", "#2f6bd8")), // the picked node, and its links
  };
}
function palette() {
  if (!PALETTE) {
    const style = typeof document === "undefined" ? null : getComputedStyle(document.documentElement);
    PALETTE = readPalette((name) => (style ? style.getPropertyValue(name) : ""));
  }
  return PALETTE;
}
const TIER_KEYS = Object.keys(TIERS);
const STEPS = 20; // opacity is drawn in 5% steps, one batch per step
// Batches reused every frame, so drawing allocates nothing and the garbage collector stays out of the way.
const nodeBatches = Array.from({ length: COLORS * (STEPS + 1) }, () => []); // one batch per color and step

// Indexes, degrees, sizes (6 to 12 by degree), clusters, and the release order (hubs first).
export function prepare(nodes, edges) {
  const index = new Map(nodes.map((n, i) => [n.id, i]));
  const seen = new Set();
  const links = [];
  const neighbors = nodes.map(() => []);
  const linkStyle = new Map(); // pair key to its strongest kind of link
  for (const e of edges) {
    const a = index.get(e.source);
    const b = index.get(e.target);
    if (a === undefined || b === undefined || a === b) continue;
    const key = a < b ? a * 1e6 + b : b * 1e6 + a;
    const style = LINK_STYLE[e.kind] ?? RECORDED;
    if (seen.has(key)) { linkStyle.set(key, Math.min(linkStyle.get(key), style)); continue; }
    linkStyle.set(key, style);
    seen.add(key);
    links.push([a, b]);
    neighbors[a].push(b);
    neighbors[b].push(a);
  }
  const degree = neighbors.map((list) => list.length);
  const maxDegree = degree.reduce((m, d) => Math.max(m, d), 1);
  const radii = degree.map((d) => 6 + 6 * Math.sqrt(d / maxDegree)); // bubbles, a little larger the more they link
  const order = nodes.map((_, i) => i).sort((p, q) => degree[q] - degree[p] || p - q);
  const tiers = nodes.map((n) => tierOf(n.tier));
  // Half of each name's width at 12 px (about 6.8 px a character).
  const extents = nodes.map((n) => Math.min(90, 8 + String(n.title || "").length * 3.4));
  const teamNames = [...new Set(nodes.map((n) => n.workspaces[0] || ""))];
  const teams = nodes.map((n) => teamNames.indexOf(n.workspaces[0] || ""));
  const { cluster, count: clusterCount } = communities(neighbors, teams, order);
  return { index, links, neighbors, degree, radii, order, tiers, teams, teamNames, extents, cluster, clusterCount, linkStyle,
           tierIndex: Uint8Array.from(tiers, (t) => TIER_KEYS.indexOf(t)) };
}

export default function KnowledgeGraph({ nodes, edges, visible, selectedId, onSelect, onReady, marked, inset = 56 }) {
  const wrap = useRef(null);
  const canvas = useRef(null);
  const tip = useRef(null);
  const live = useRef(null);
  const state = useRef(null);
  const graph = useMemo(() => prepare(nodes, edges), [nodes, edges]);
  const select = useRef(onSelect);
  select.current = onSelect;
  // Ringed nodes (Ask rings an answer's sources), and the room kept clear at the bottom (the map's replay bar).
  const marks = useMemo(() => (marked || []).map((id) => graph.index.get(id)).filter((i) => i !== undefined),
                        [graph, (marked || []).join(",")]);
  const markRef = useRef(marks);
  markRef.current = marks;
  const insetRef = useRef(inset);
  insetRef.current = inset;

  useEffect(() => {
    const el = canvas.current;
    const box = wrap.current;
    if (!el || !box || !nodes.length) return undefined;
    const n = nodes.length;
    const animate = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const rect = box.getBoundingClientRect();
    const options = { count: n, links: graph.links, radii: graph.radii, order: graph.order, groups: graph.cluster, width: rect.width, height: rect.height, animate };
    const s = {
      t0: performance.now(), now: 0, raf: 0, width: rect.width, height: rect.height, dpr: window.devicePixelRatio || 1,
      view: { k: 1, x: rect.width / 2, y: rect.height / 2 },
      releaseAt: releaseTimes(graph.order, animate), physics: null, worker: null, xy: null, moving: true, waiting: false,
      mask: new Uint8Array(n).fill(1), shown: new Float64Array(n).fill(-Infinity), hidden: new Float64Array(n).fill(-Infinity),
      animUntil: 0, hover: -1, focus: -1, highlight: -1, near: null, drag: null, pointers: new Map(), pinch: null,
      mix: 0, lit: -1, litNear: null, litDim: DIM, lastFrame: 0, // the highlight, fading in and out
      font: getComputedStyle(document.body).fontFamily, mode: n > WORKER_ABOVE ? "worker" : "main",
    };
    s.lastRelease = s.releaseAt.reduce((m, t) => Math.max(m, t), 0);
    state.current = s;
    const ctx = el.getContext("2d");
    const labelWidths = new Float32Array(n); // measured once, in screen pixels at 12 px
    const queued = new Uint8Array(n); // labels already queued this frame
    const px = (i) => (s.physics ? s.physics.nodes[i].x : s.xy[2 * i]);
    const py = (i) => (s.physics ? s.physics.nodes[i].y : s.xy[2 * i + 1]);
    const clock = () => performance.now() - s.t0;
    const scale = (i) => (s.mask[i] ? Math.min(1, (s.now - s.shown[i]) / SCALE_MS) : Math.max(0, 1 - (s.now - s.hidden[i]) / SCALE_MS));

    if (s.mode === "worker") {
      s.worker = createGraphWorker();
      s.worker.onmessage = ({ data }) => {
        s.xy = data.xy;
        s.moving = data.moving;
        s.waiting = false;
        kick();
      };
      s.worker.postMessage({ type: "init", options });
    } else {
      s.physics = createPhysics(options);
    }
    const physics = (type, extra) => {
      s.moving = true; // a drag reheats the simulation, wherever it runs
      if (s.worker) s.worker.postMessage({ type, ...extra });
      else s.physics[type](...Object.values(extra || {}));
    };

    function size() {
      const r = box.getBoundingClientRect();
      s.view.x += (r.width - s.width) / 2;
      s.view.y += (r.height - s.height) / 2;
      s.width = r.width;
      s.height = r.height;
      el.width = Math.round(r.width * s.dpr);
      el.height = Math.round(r.height * s.dpr);
      if (s.fitted && !s.userMoved) fit(); // the panel changed size: frame the disc again, unless you've moved it
      kick();
    }

    function draw() {
      const { k, x: tx, y: ty } = s.view;
      const d = s.dpr;
      ctx.setTransform(d, 0, 0, d, 0, 0);
      ctx.clearRect(0, 0, s.width, s.height);
      if (!s.physics && !s.xy) return;
      ctx.setTransform(d * k, 0, 0, d * k, d * tx, d * ty);
      const now = s.now;
      const hl = s.lit; // the clicked node, or the one being pointed at
      const near = s.litNear;
      const mix = s.mix; // 0 = no highlight, 1 = fully applied
      const fade = (inside, base) => (inside ? base + (1 - base) * mix : base + (base * s.litDim - base) * mix);
      // Lines: none at rest, so the map reads as packed bubbles; the lit node's links, in blue, as it lights up, each
      // drawn as its kind: curated bold, recorded solid, inferred dashed (inferred first, so stronger kinds sit on top).
      if (hl >= 0 && mix > 0 && scale(hl)) {
        for (const style of [INFERRED, RECORDED, CURATED]) {
          ctx.beginPath();
          let any = false;
          for (const b of graph.neighbors[hl]) {
            if (now < Math.max(s.releaseAt[hl], s.releaseAt[b]) || !scale(b)) continue;
            if ((graph.linkStyle.get(hl < b ? hl * 1e6 + b : b * 1e6 + hl) ?? RECORDED) !== style) continue;
            ctx.moveTo(px(hl), py(hl));
            ctx.lineTo(px(b), py(b));
            any = true;
          }
          if (!any) continue;
          ctx.setLineDash(style === INFERRED ? [4 / k, 3 / k] : []);
          ctx.strokeStyle = `rgba(${palette().ego}, ${EGO_ALPHA * mix})`;
          ctx.lineWidth = (style === CURATED ? 2.6 : 1.6) / k;
          ctx.stroke();
        }
        ctx.setLineDash([]);
      }
      // Nodes, grouped by color and opacity.
      for (const batch of nodeBatches) batch.length = 0;
      for (let i = 0; i < n; i++) {
        if (now < s.releaseAt[i]) continue;
        const sc = scale(i);
        if (!sc) continue;
        const alpha = hl < 0 ? 1 : fade(i === hl || near.has(i), 1);
        const step = Math.round(alpha * STEPS);
        if (step) nodeBatches[(graph.cluster[i] % COLORS) * (STEPS + 1) + step].push(i, sc);
      }
      for (let key = 0; key < nodeBatches.length; key++) {
        const list = nodeBatches[key];
        if (!list.length) continue;
        ctx.fillStyle = `rgba(${palette().clusters[Math.floor(key / (STEPS + 1))]}, ${(key % (STEPS + 1)) / STEPS})`;
        ctx.beginPath();
        for (let j = 0; j < list.length; j += 2) {
          const i = list[j];
          const r = graph.radii[i] * list[j + 1];
          ctx.moveTo(px(i) + r, py(i));
          ctx.arc(px(i), py(i), r, 0, Math.PI * 2);
        }
        ctx.fill();
        ctx.strokeStyle = `rgba(${palette().paper}, 0.85)`; // a hairline of paper between bubbles, so they read as packed
        ctx.lineWidth = 1.25 / k;
        ctx.stroke();
      }
      const ring = (i, gap, color, width) => {
        ctx.beginPath();
        ctx.arc(px(i), py(i), graph.radii[i] + gap / k, 0, Math.PI * 2);
        ctx.strokeStyle = color;
        ctx.lineWidth = width / k;
        ctx.stroke();
      };
      // The lit node, purple and a little larger, over its neighbors.
      if (hl >= 0 && mix > 0 && scale(hl)) {
        ctx.beginPath();
        ctx.arc(px(hl), py(hl), graph.radii[hl] * (1 + 0.25 * mix) * scale(hl), 0, Math.PI * 2);
        ctx.globalAlpha = mix;
        ctx.fillStyle = palette().focus;
        ctx.fill();
        ctx.strokeStyle = `rgba(${palette().paper}, 0.85)`;
        ctx.lineWidth = 1.5 / k;
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
      if (s.highlight >= 0 && scale(s.highlight)) ring(s.highlight, 2.5 + 0.25 * graph.radii[s.highlight] * k, palette().ink, 1.5);
      if (s.focus >= 0 && scale(s.focus)) ring(s.focus, 3.5, palette().accent, 2);
      for (const i of markRef.current) if (s.mask[i] && now >= s.releaseAt[i] && scale(i)) ring(i, 2, palette().ink, 1.25);
      // Labels: none at rest, so the disc reads as clusters, not as scattered names. Lighting a node (pointing at it or
      // clicking it) names it and the nodes it links to, fading in with its lines; the tooltip names the one pointed
      // at. Names sit centered under their node and never overlap each other; a name with no room is left out.
      const labels = [];
      queued.fill(0);
      const queue = (i) => { if (!queued[i]) { queued[i] = 1; labels.push(i); } };
      if (hl >= 0 && mix > 0) { queue(hl); graph.neighbors[hl].forEach(queue); }
      const placed = [];
      s.named = 0;
      const free = (b) => !placed.some((p) => b[0] < p[2] && b[2] > p[0] && b[1] < p[3] && b[3] > p[1]);
      ctx.font = `500 ${11 / k}px ${s.font}`;
      ctx.textBaseline = "middle";
      for (const i of labels) {
        if (!s.mask[i] || now < s.releaseAt[i] || scale(i) < 1 || i === s.hover) continue; // the tooltip names the hovered node
        const sx = px(i) * k + tx;
        const sy = py(i) * k + ty;
        if (sx < -160 || sx > s.width + 160 || sy < -40 || sy > s.height + 40) continue; // off screen: no name to place
        if (!labelWidths[i]) labelWidths[i] = ctx.measureText(nodes[i].title).width * k; // in screen pixels
        const w = labelWidths[i] / k;
        const y = py(i) + graph.radii[i] + 10 / k;
        const box = [px(i) - w / 2 - 3 / k, y - 8 / k, px(i) + w / 2 + 3 / k, y + 8 / k];
        if (!free(box)) continue;
        placed.push(box);
        if (s.timings) s.named = (s.named || 0) + 1; // only with the debug hook
        const quiet = graph.tiers[i] === "evidence" ? 0.75 : 1; // evidence names sit a step back
        const a = quiet * mix; // they fade in and out with the highlight
        ctx.lineJoin = "round"; // a halo of paper behind each name, so it reads over the bubbles it crosses
        ctx.lineWidth = 3 / k;
        ctx.strokeStyle = `rgba(${palette().paper}, ${0.85 * a})`;
        ctx.strokeText(nodes[i].title, px(i) - w / 2, y);
        ctx.fillStyle = `rgba(${palette().label}, ${a})`; // --fg-2
        ctx.fillText(nodes[i].title, px(i) - w / 2, y);
      }
      placeTip();
    }

    function busy() {
      return s.moving || s.drag || s.waiting || s.viewAnim || s.now < s.lastRelease + FADE_MS || s.now < s.animUntil
        || (!s.fitted && !s.userMoved) || s.mix !== (target() >= 0 ? 1 : 0);
    }
    // The highlight follows a click first, else the pointer, and fades between states.
    const target = () => (s.highlight >= 0 ? s.highlight : s.hover);
    function updateLit(dt) {
      const want = target();
      if (want >= 0 && want !== s.lit) {
        s.lit = want;
        s.litNear = new Set(graph.neighbors[want]);
      }
      s.litDim = s.highlight >= 0 ? DIM : HOVER_DIM;
      const goal = want >= 0 ? 1 : 0;
      const stepMix = dt / MIX_MS;
      s.mix = goal > s.mix ? Math.min(goal, s.mix + stepMix) : Math.max(goal, s.mix - stepMix);
      if (s.mix === 0) { s.lit = -1; s.litNear = null; }
    }
    function frame(ts) {
      s.raf = 0;
      s.now = ts - s.t0;
      const t = performance.now();
      if (s.physics) s.moving = s.physics.step(s.now);
      else if (!s.waiting && (s.moving || s.now < s.lastRelease)) { // ask only while something can move
        s.waiting = true;
        s.worker.postMessage({ type: "step", now: s.now });
      }
      const t2 = performance.now();
      const dt = s.lastFrame ? Math.min(50, ts - s.lastFrame) : 16; // capped, so a fade after idle still fades
      s.lastFrame = ts;
      updateLit(dt);
      if (s.viewAnim) stepView(ts);
      else if (!s.fitted && !s.moving && s.now > s.lastRelease + FADE_MS && !s.userMoved) { s.fitted = true; fit(); }
      draw();
      if (s.timings) s.timings.push([t2 - t, performance.now() - t2]); // only with the debug hook
      if (busy()) s.raf = requestAnimationFrame(frame);
    }
    function kick() {
      if (!s.raf) s.raf = requestAnimationFrame(frame);
    }
    s.kick = kick;
    // A new theme, chosen or the system's: forget the colors and paint again.
    const repaint = () => { PALETTE = null; kick(); };
    const scheme = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    window.addEventListener("aerugo:theme", repaint);
    if (scheme) scheme.addEventListener("change", repaint);

    // The hover (or keyboard) tooltip follows its node while the graph moves.
    function placeTip() {
      const i = s.hover >= 0 ? s.hover : s.focus;
      const t = tip.current;
      if (!t) return;
      if (i < 0 || (!s.physics && !s.xy) || !scale(i)) {
        t.classList.remove("on");
        return;
      }
      if (t.dataset.i !== String(i)) {
        t.textContent = nodes[i].title;
        t.dataset.i = String(i);
      }
      const x = px(i) * s.view.k + s.view.x;
      const y = py(i) * s.view.k + s.view.y;
      t.style.transform = `translate(${Math.round(x + graph.radii[i] * s.view.k + 8)}px, ${Math.round(y - 12)}px)`;
      t.classList.add("on");
    }

    function toGraph(sx, sy) {
      return [(sx - s.view.x) / s.view.k, (sy - s.view.y) / s.view.k];
    }
    function pick(sx, sy) {
      if (!s.physics && !s.xy) return -1;
      const [gx, gy] = toGraph(sx, sy);
      let best = -1;
      let bestD = Infinity;
      for (let i = 0; i < n; i++) {
        if (!s.mask[i] || s.now < s.releaseAt[i]) continue;
        const dx = px(i) - gx;
        const dy = py(i) - gy;
        const d2 = dx * dx + dy * dy;
        const r = graph.radii[i] + 4 / s.view.k;
        if (d2 < r * r && d2 < bestD) { best = i; bestD = d2; }
      }
      return best;
    }
    function highlight(i) {
      s.highlight = i;
      s.near = i >= 0 ? new Set(graph.neighbors[i]) : null;
      select.current(i >= 0 ? nodes[i] : null);
      s.now = clock();
      kick();
    }
    s.highlightById = (id) => highlight(id == null ? -1 : graph.index.get(id) ?? -1);
    function zoomAt(sx, sy, factor) {
      s.userMoved = true;
      s.viewAnim = null;
      const k = Math.min(6, Math.max(0.25, s.view.k * factor));
      s.view.x = sx - (sx - s.view.x) * (k / s.view.k);
      s.view.y = sy - (sy - s.view.y) * (k / s.view.k);
      s.view.k = k;
      s.now = clock();
      kick();
    }
    // Fit: frame every visible node, easing there over 400 ms. Never zooms the typical bubble past BUBBLE_PX on screen,
    // so a small map stays bubbles, not balloons; a large one fills the panel.
    function fit() {
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (let i = 0; i < n; i++) {
        if (!s.mask[i] || s.now < s.releaseAt[i]) continue;
        const r = graph.radii[i];
        x0 = Math.min(x0, px(i) - r); x1 = Math.max(x1, px(i) + r);
        y0 = Math.min(y0, py(i) - r); y1 = Math.max(y1, py(i) + r + 16); // room for a lit name on the bottom row
      }
      if (!Number.isFinite(x0)) return;
      const room = { w: s.width - 64, h: s.height - 64 - insetRef.current }; // margins, and the map's replay bar
      const typical = [...graph.radii].sort((a, b) => a - b)[Math.floor(graph.radii.length / 2)] || 1;
      const k = Math.max(0.25, Math.min(BUBBLE_PX / typical, room.w / (x1 - x0 || 1), room.h / (y1 - y0 || 1)));
      const to = { k, x: s.width / 2 - ((x0 + x1) / 2) * k, y: (s.height - insetRef.current) / 2 - ((y0 + y1) / 2) * k };
      s.viewAnim = { from: { ...s.view }, to, start: performance.now() };
      kick();
    }
    function stepView(ts) {
      const a = s.viewAnim;
      const t = Math.min(1, (ts - a.start) / 400);
      const e = 1 - (1 - t) ** 3; // ease out
      s.view = { k: a.from.k + (a.to.k - a.from.k) * e, x: a.from.x + (a.to.x - a.from.x) * e, y: a.from.y + (a.to.y - a.from.y) * e };
      if (t >= 1) s.viewAnim = null;
    }
    if (onReady) onReady({ zoomIn: () => zoomAt(s.width / 2, s.height / 2, 1.3), zoomOut: () => zoomAt(s.width / 2, s.height / 2, 1 / 1.3), fit });

    const local = (e) => { const r = el.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    function down(e) {
      el.setPointerCapture(e.pointerId);
      const [x, y] = local(e);
      s.pointers.set(e.pointerId, [x, y]);
      if (s.pointers.size === 2) { // pinch
        const [[ax, ay], [bx, by]] = [...s.pointers.values()];
        s.pinch = { dist: Math.hypot(ax - bx, ay - by), k: s.view.k };
        s.drag = null;
        return;
      }
      const hit = pick(x, y);
      s.drag = { i: hit, x, y, vx: s.view.x, vy: s.view.y, moved: false };
      if (hit >= 0) physics("dragStart", { i: hit });
      s.now = clock();
      kick();
    }
    function move(e) {
      const [x, y] = local(e);
      if (s.pointers.has(e.pointerId)) s.pointers.set(e.pointerId, [x, y]);
      if (s.pinch && s.pointers.size === 2) {
        const [[ax, ay], [bx, by]] = [...s.pointers.values()];
        zoomAt((ax + bx) / 2, (ay + by) / 2, (s.pinch.k * Math.hypot(ax - bx, ay - by)) / s.pinch.dist / s.view.k);
        return;
      }
      if (s.drag) {
        if (Math.hypot(x - s.drag.x, y - s.drag.y) > 3) s.drag.moved = true;
        if (s.drag.i >= 0) {
          const [gx, gy] = toGraph(x, y);
          physics("drag", { i: s.drag.i, x: gx, y: gy });
        } else if (s.drag.moved) {
          s.userMoved = true;
          s.viewAnim = null;
          s.view.x = s.drag.vx + (x - s.drag.x);
          s.view.y = s.drag.vy + (y - s.drag.y);
        }
        s.now = clock();
        kick();
        return;
      }
      const hit = pick(x, y);
      if (hit !== s.hover) {
        s.hover = hit;
        el.style.cursor = hit >= 0 ? "pointer" : "";
        placeTip();
        s.now = clock();
        kick();
      }
    }
    function up(e) {
      s.pointers.delete(e.pointerId);
      if (s.pinch) {
        if (s.pointers.size < 2) s.pinch = null;
        return;
      }
      const d = s.drag;
      s.drag = null;
      if (!d) return;
      if (d.i >= 0) physics("dragEnd", { i: d.i });
      if (!d.moved) highlight(d.i);
      s.now = clock();
      kick();
    }
    function leave() {
      s.hover = -1;
      placeTip();
      kick();
    }
    function wheel(e) {
      e.preventDefault();
      const [x, y] = local(e);
      zoomAt(x, y, Math.exp(-e.deltaY * 0.0015));
    }
    // Keyboard: Tab and Shift+Tab move between nodes (most connected first), then out of the graph.
    function focusable() {
      return graph.order.filter((i) => s.mask[i] && s.now >= s.releaseAt[i]);
    }
    function focusNode(i) {
      s.focus = i;
      if (i >= 0) {
        const x = px(i) * s.view.k + s.view.x;
        const y = py(i) * s.view.k + s.view.y;
        if (x < 40 || y < 40 || x > s.width - 40 || y > s.height - 40) { // bring it into view
          s.view.x += s.width / 2 - x;
          s.view.y += s.height / 2 - y;
        }
        const t = TIERS[graph.tiers[i]].label;
        if (live.current) live.current.textContent = `${nodes[i].title}, ${t}, ${graph.degree[i]} connection${graph.degree[i] === 1 ? "" : "s"}`;
      }
      s.now = clock();
      kick();
    }
    function key(e) {
      if (e.key === "Tab") {
        const list = focusable();
        const at = list.indexOf(s.focus);
        const next = e.shiftKey ? at - 1 : at + 1;
        if (next < 0 || next >= list.length) { focusNode(-1); return; } // let focus leave the graph
        e.preventDefault();
        focusNode(list[next]);
      } else if (e.key === "Enter" && s.focus >= 0) {
        e.preventDefault();
        highlight(s.focus);
      } else if (e.key === "Escape") {
        highlight(-1);
      }
    }
    function focusIn() {
      if (s.focus < 0) {
        const list = focusable();
        if (list.length) focusNode(list[0]);
      }
    }
    function focusOut() { focusNode(-1); }

    el.addEventListener("pointerdown", down);
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
    el.addEventListener("pointerleave", leave);
    el.addEventListener("wheel", wheel, { passive: false });
    el.addEventListener("keydown", key);
    el.addEventListener("focus", focusIn);
    el.addEventListener("blur", focusOut);
    const observer = new ResizeObserver(size);
    observer.observe(box);
    size();
    if (window.__klGraphDebug) {
      s.timings = [];
      window.__klGraphDebug(s, { pick, toGraph, px, py, graph });
    }
    return () => {
      window.removeEventListener("aerugo:theme", repaint);
      if (scheme) scheme.removeEventListener("change", repaint);
      observer.disconnect();
      if (s.raf) cancelAnimationFrame(s.raf);
      if (s.worker) s.worker.terminate();
      el.removeEventListener("pointerdown", down);
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("pointercancel", up);
      el.removeEventListener("pointerleave", leave);
      el.removeEventListener("wheel", wheel);
      el.removeEventListener("keydown", key);
      el.removeEventListener("focus", focusIn);
      el.removeEventListener("blur", focusOut);
      state.current = null;
    };
  }, [graph]);

  // Filters: nodes that drop out scale to nothing, nodes that return scale back in, the rest reheat.
  useEffect(() => {
    const s = state.current;
    if (!s) return;
    const mask = new Uint8Array(nodes.length);
    nodes.forEach((node, i) => { mask[i] = !visible || visible.has(node.id) ? 1 : 0; });
    s.now = performance.now() - s.t0;
    let changed = false;
    for (let i = 0; i < mask.length; i++) {
      if (mask[i] === s.mask[i]) continue;
      changed = true;
      if (mask[i]) s.shown[i] = s.now;
      else s.hidden[i] = s.now;
    }
    if (!changed) return;
    s.mask = mask;
    s.animUntil = s.now + SCALE_MS;
    s.moving = true;
    if (s.worker) s.worker.postMessage({ type: "visible", mask });
    else s.physics.setVisible(mask);
    if (s.highlight >= 0 && !mask[s.highlight]) s.highlightById(null);
    if (s.focus >= 0 && !mask[s.focus]) s.focus = -1;
    s.kick();
  }, [visible, graph]);

  // The page can clear the highlight (closing the inspector, say).
  useEffect(() => {
    const s = state.current;
    if (s && selectedId == null && s.highlight >= 0) s.highlightById(null);
  }, [selectedId]);

  const counts = useMemo(() => {
    const on = (id) => !visible || visible.has(id);
    const shownNodes = nodes.filter((nd) => on(nd.id)).length;
    const shownEdges = graph.links.filter(([a, b]) => on(nodes[a].id) && on(nodes[b].id)).length;
    return [shownNodes, shownEdges];
  }, [visible, graph]);
  const top = graph.order.slice(0, 20);

  return h("div", { ref: wrap, className: "graph-fill" },
    h("canvas", {
      ref: canvas, className: "graph-canvas2d", tabIndex: 0, role: "application", "aria-roledescription": "knowledge graph",
      "aria-label": `Knowledge graph with ${counts[0]} nodes and ${counts[1]} edges. Tab moves between nodes, Enter highlights a node's connections, Escape clears.`,
    }),
    h("div", { ref: tip, className: "graph-tip", "aria-hidden": true }),
    h("p", { ref: live, className: "sr-only", "aria-live": "polite" }),
    h("table", { className: "sr-only" },
      h("caption", null, "The 20 most connected nodes and their connections"),
      h("thead", null, h("tr", null, h("th", null, "Node"), h("th", null, "Tier"), h("th", null, "Connections"))),
      h("tbody", null, top.map((i) => h("tr", { key: nodes[i].id },
        h("td", null, nodes[i].title),
        h("td", null, TIERS[graph.tiers[i]].label),
        h("td", null, graph.neighbors[i].slice(0, 8).map((j) => nodes[j].title).join(", ") || "None"))))));
}
