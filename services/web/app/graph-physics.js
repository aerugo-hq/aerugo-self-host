// The knowledge map's physics, framework-free so it runs the same on the main thread or in a Web Worker.
// Nodes join the d3-force simulation one at a time (the entry: they start above the canvas and fall in),
// and leave or rejoin when filters change. Nothing tweens to a precomputed layout: forces do the landing.
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from "d3-force";

export const STAGGER_MS = 30; // node i is released at i × 30 ms...
export const STAGGER_CAP_MS = 1500; // ...but releasing everything never takes longer than this
export const COOL_ALPHA = 0.05; // below this the graph has settled and the loop can stop

// The release schedule, shared with the renderer so edges can fade in once both ends are falling.
export function releaseTimes(order, animate) {
  const times = new Float64Array(order.length);
  const step = order.length > 1 ? Math.min(STAGGER_MS, STAGGER_CAP_MS / (order.length - 1)) : 0;
  order.forEach((node, rank) => { times[node] = animate ? rank * step : 0; });
  return times;
}

// count: nodes; links: [[a, b]] by index; radii: per node; order: release order (hubs first).
export function createPhysics({ count, links, radii, order, teams, extents, width, height, animate, seed = 7 }) {
  let s = seed;
  const rand = () => (s = (s * 16807) % 2147483647) / 2147483647; // deterministic, so reloads look alike
  const nodes = Array.from({ length: count }, (_, id) => ({
    id, r: radii[id], team: teams ? teams[id] : 0, vx: 0, vy: 0, fx: null, fy: null,
    x: (rand() - 0.5) * width * 0.9, // a random spot across the viewport...
    y: animate ? -height / 2 - 50 : (rand() - 0.5) * height * 0.8, // ...starting just above it
  }));
  const releaseAt = releaseTimes(order, animate);
  const released = new Uint8Array(count);
  const visible = new Uint8Array(count).fill(1);
  const member = new Uint8Array(count); // in the simulation: released and not filtered out
  let pending = count;
  const linkObjects = links.map(([a, b]) => ({ source: nodes[a], target: nodes[b], a, b }));
  // Spacing follows the room each node gets: small graphs spread out, large ones stay compact.
  const spacing = Math.min(80, Math.max(26, Math.sqrt((width * height) / Math.max(count, 1)) * 0.45));
  // Small maps name every node, so they get more air: longer links, stronger repulsion, and a collision box
  // around each dot and its name (extents: half each name's width). See boxCollide below.
  const roomy = count <= 40;
  const linkForce = forceLink([]).distance(roomy ? spacing * 1.3 : spacing * 0.75); // large maps pack into regions of bubbles
  // Teams become regions: each node is drawn gently toward its team's spot on a circle, so a team's
  // knowledge gathers even where nothing links it together yet.
  const teamCount = teams ? Math.max(...teams) + 1 : 1;
  const ring = teamCount > 1 ? Math.min(spacing * (roomy ? 3.4 : 2.4) * Math.sqrt(teamCount), roomy ? Math.min(width, height) * 0.3 : Infinity) : 0;
  const aspect = roomy ? Math.sqrt(width / Math.max(height, 1)) : 1; // regions follow the canvas's shape
  const anchor = (t) => [Math.cos((2 * Math.PI * t) / teamCount - Math.PI / 2) * ring * aspect, Math.sin((2 * Math.PI * t) / teamCount - Math.PI / 2) * ring / aspect];
  // On large maps, collision is half of each tick's cost; every other tick keeps nodes apart just as well,
  // since charge holds them off in between. Small maps use boxCollide, every tick, instead.
  const collide = forceCollide((d) => d.r + 1.5).iterations(1);
  let ticks = 0;
  const collideEveryOtherTick = Object.assign((alpha) => { if (ticks++ % 2 === 0) collide(alpha); },
    { initialize: (all, random) => collide.initialize(all, random) });
  // On small maps each node's footprint is a box: its dot, with its name centered underneath, plus 8 px of
  // air on every side, so neighbors always keep a 16 px gap. Boxes fit names far better than circles do.
  const PAD = 8;
  const halfW = (d) => Math.max(d.r, extents ? extents[d.id] : d.r) + PAD;
  const halfH = (d) => d.r + 10 + PAD;
  let boxed = [];
  const boxCollide = Object.assign((alpha) => {
    for (let i = 0; i < boxed.length; i++) {
      const a = boxed[i];
      for (let j = i + 1; j < boxed.length; j++) {
        const b = boxed[j];
        const dx = b.x + b.vx - (a.x + a.vx) || 0.01;
        const dy = b.y + b.vy - (a.y + a.vy) || 0.01;
        const ox = halfW(a) + halfW(b) - Math.abs(dx);
        const oy = halfH(a) + halfH(b) - Math.abs(dy);
        if (ox <= 0 || oy <= 0) continue;
        if (ox < oy * 1.8) { const push = Math.sign(dx) * ox * 0.5; a.vx -= push; b.vx += push; } // sideways, unless that's far deeper
        else { const push = Math.sign(dy) * oy * 0.5; a.vy -= push; b.vy += push; }
      }
    }
  }, { initialize: (all) => { boxed = all; } });
  const sim = forceSimulation([]).stop()
    .force("charge", forceManyBody().strength(roomy ? -spacing * 1.5 : -spacing * 0.5).distanceMax(roomy ? 600 : 400).theta(1.2)) // coarse, invisibly so
    .force("link", linkForce)
    .force("collide", roomy ? boxCollide : collideEveryOtherTick)
    .force("center", forceCenter(0, 0).strength(0.08))
    // Toward the team's region. Small maps pull harder vertically than sideways, so they spread wide.
    .force("x", forceX((d) => anchor(d.team)[0]).strength(roomy ? 0.02 : 0.08))
    .force("y", forceY((d) => anchor(d.team)[1]).strength(roomy ? 0.06 : 0.08))
    .alphaDecay(0.03);
  let dirty = false;

  function rebuild() {
    sim.nodes(nodes.filter((n) => member[n.id]));
    linkForce.links(linkObjects.filter((l) => member[l.a] && member[l.b]));
    dirty = false;
  }

  // One frame of physics at `now` ms since the start. Returns whether anything is still moving.
  function step(now) {
    if (pending) {
      for (let i = 0; i < count; i++) {
        if (!released[i] && releaseAt[i] <= now) {
          released[i] = 1;
          pending--;
          if (visible[i]) {
            member[i] = 1;
            nodes[i].vy = animate ? 2.5 : 0; // a small push to start the fall
            dirty = true;
          }
        }
      }
      if (dirty) sim.alpha(Math.max(sim.alpha(), 0.6));
    }
    if (dirty) rebuild();
    const moving = sim.alpha() >= COOL_ALPHA || sim.alphaTarget() > 0;
    if (moving) sim.tick();
    return moving || pending > 0;
  }

  // Reduced motion: release everything and let the simulation settle before the first frame.
  if (!animate) {
    step(0);
    for (let i = 0; i < 300 && sim.alpha() >= 0.002; i++) sim.tick();
    sim.alpha(0);
  } else {
    sim.alpha(1);
  }

  return {
    nodes, releaseAt,
    step,
    alpha: () => sim.alpha(),
    positions() { // a copy, for a worker to transfer
      const xy = new Float32Array(count * 2);
      for (let i = 0; i < count; i++) { xy[2 * i] = nodes[i].x; xy[2 * i + 1] = nodes[i].y; }
      return xy;
    },
    setVisible(mask) { // filters: removed nodes leave the simulation, the rest reheat and close up
      for (let i = 0; i < count; i++) {
        visible[i] = mask[i];
        member[i] = released[i] && mask[i] ? 1 : 0;
      }
      dirty = true;
      sim.alpha(Math.max(sim.alpha(), 0.3));
    },
    dragStart(i) { sim.alphaTarget(0.3); nodes[i].fx = nodes[i].x; nodes[i].fy = nodes[i].y; },
    drag(i, x, y) { nodes[i].fx = x; nodes[i].fy = y; },
    dragEnd(i) { nodes[i].fx = null; nodes[i].fy = null; sim.alphaTarget(0); },
  };
}
