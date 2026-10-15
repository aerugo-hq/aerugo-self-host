// The knowledge map's physics, framework-free so it runs the same on the main thread or in a Web Worker.
// Nodes join the d3-force simulation one at a time (the entry: they start above the canvas and fall in),
// and leave or rejoin when filters change. Nothing tweens to a precomputed layout: forces do the landing.
//
// The layout (slice K7) is one packed disc, like a circle-packing chart: every bubble is drawn inward, none overlap,
// and each cluster gathers at its own spot inside the disc, so clusters read as neighborhoods.
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from "d3-force";

export const STAGGER_MS = 30; // node i is released at i × 30 ms...
export const STAGGER_CAP_MS = 1500; // ...but releasing everything never takes longer than this
export const COOL_ALPHA = 0.05; // below this the graph has settled and the loop can stop
const GAP = 1.5; // between neighboring bubbles, in graph units

// The release schedule, shared with the renderer so edges can fade in once both ends are falling.
export function releaseTimes(order, animate) {
  const times = new Float64Array(order.length);
  const step = order.length > 1 ? Math.min(STAGGER_MS, STAGGER_CAP_MS / (order.length - 1)) : 0;
  order.forEach((node, rank) => { times[node] = animate ? rank * step : 0; });
  return times;
}

// Where each cluster gathers. The disc holds every bubble at about 80% packing; each cluster takes a wedge in
// proportion to its size, and gathers at the wedge's middle, nearer the center the bigger it is.
export function clusterSpots(radii, groups) {
  const count = radii.length;
  const area = radii.reduce((sum, r) => sum + Math.PI * (r + GAP) ** 2, 0);
  const R = Math.sqrt(area / (Math.PI * 0.8));
  const sizes = new Map();
  for (let i = 0; i < count; i++) {
    const g = groups ? groups[i] : 0;
    sizes.set(g, (sizes.get(g) || 0) + 1);
  }
  const spots = new Map();
  let turned = -Math.PI / 2;
  for (const [g, size] of [...sizes].sort((a, b) => a[0] - b[0])) {
    const share = (2 * Math.PI * size) / Math.max(count, 1);
    const mid = turned + share / 2;
    const dist = sizes.size > 1 ? R * 0.55 * Math.sqrt(1 - size / count) : 0;
    spots.set(g, [Math.cos(mid) * dist, Math.sin(mid) * dist]);
    turned += share;
  }
  return { R, spots };
}

// count: nodes; links: [[a, b]] by index; radii: per node; order: release order (hubs first); groups: cluster per node.
export function createPhysics({ count, links, radii, order, groups, width, height, animate, seed = 7 }) {
  let s = seed;
  const rand = () => (s = (s * 16807) % 2147483647) / 2147483647; // deterministic, so reloads look alike
  const { R, spots } = clusterSpots(radii, groups);
  const nodes = Array.from({ length: count }, (_, id) => ({
    id, r: radii[id], group: groups ? groups[id] : 0, vx: 0, vy: 0, fx: null, fy: null,
    x: (rand() - 0.5) * R * 2, // somewhere across the disc...
    y: animate ? -Math.max(height / 2, R) - 50 : (rand() - 0.5) * R * 2, // ...starting just above it
  }));
  const releaseAt = releaseTimes(order, animate);
  const released = new Uint8Array(count);
  const visible = new Uint8Array(count).fill(1);
  const member = new Uint8Array(count); // in the simulation: released and not filtered out
  let pending = count;
  const linkObjects = links.map(([a, b]) => ({ source: nodes[a], target: nodes[b], a, b }));
  // Links only nudge: the disc stays even, and linked bubbles still end up near each other.
  const linkForce = forceLink([]).distance((l) => l.source.r + l.target.r + 4).strength(0.04);
  const spot = (d) => spots.get(d.group) || [0, 0];
  const sim = forceSimulation([]).stop()
    .force("charge", forceManyBody().strength(-2).distanceMax(R / 2))
    .force("link", linkForce)
    .force("collide", forceCollide((d) => d.r + GAP).iterations(2))
    .force("x", forceX((d) => spot(d)[0]).strength(0.07))
    .force("y", forceY((d) => spot(d)[1]).strength(0.07))
    .alphaDecay(0.025);
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
