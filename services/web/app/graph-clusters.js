// Clusters for the map (slice K7): communities found from the links themselves, so related knowledge shares a color
// the way k-means groups points, without anyone choosing the groups.
//
// The method is Louvain's local moving: each node, hubs first, joins the neighboring community that raises modularity
// most (the share of link weight inside communities, against what chance would give), until no move helps. A
// resolution above 1 favors smaller communities, so one page every other page mentions doesn't pull them all into a
// single color. A link weighs 1 plus the neighbors its two ends share, so links inside a tight group count for more
// than a bridge between groups. Ties keep a node where it is, then go to the smaller community, so the same graph
// always gets the same clusters and colors. Nodes with no links gather by team instead.
export const RESOLUTION = 1.5;

export function communities(neighbors, teams, order, resolution = RESOLUTION, passes = 16) {
  const n = neighbors.length;
  const sets = neighbors.map((list) => new Set(list));
  const shared = (i, j) => {
    const [small, big] = sets[i].size <= sets[j].size ? [sets[i], sets[j]] : [sets[j], sets[i]];
    let count = 0;
    for (const x of small) if (big.has(x)) count++;
    return count;
  };
  const weights = neighbors.map((list, i) => list.map((j) => 1 + shared(i, j)));
  const strength = weights.map((ws) => ws.reduce((a, b) => a + b, 0)); // each node's total link weight
  const total = strength.reduce((a, b) => a + b, 0) || 1; // twice the graph's link weight
  const comm = Int32Array.from({ length: n }, (_, i) => i);
  const sum = Float64Array.from(strength); // each community's total link weight
  for (let pass = 0; pass < passes; pass++) {
    let moved = 0;
    for (const i of order) {
      const list = neighbors[i];
      if (!list.length) continue;
      const own = comm[i];
      const toward = new Map(); // link weight from i into each neighboring community
      list.forEach((j, x) => { if (j !== i) toward.set(comm[j], (toward.get(comm[j]) || 0) + weights[i][x]); });
      sum[own] -= strength[i];
      const gain = (c) => (toward.get(c) || 0) - (resolution * strength[i] * sum[c]) / total;
      let best = own;
      let bestGain = gain(own);
      for (const c of toward.keys()) {
        if (c === own) continue;
        const g = gain(c);
        if (g > bestGain + 1e-12 || (Math.abs(g - bestGain) <= 1e-12 && best !== own && c < best)) { best = c; bestGain = g; }
      }
      sum[best] += strength[i];
      if (best !== own) { comm[i] = best; moved++; }
    }
    if (!moved) break;
  }
  const label = Int32Array.from(comm);
  for (let i = 0; i < n; i++) if (!neighbors[i].length) label[i] = -1 - (teams ? teams[i] : 0);
  // The biggest cluster first, so the first colors go to the biggest groups.
  const sizes = new Map();
  for (const l of label) sizes.set(l, (sizes.get(l) || 0) + 1);
  const ranked = [...sizes.keys()].sort((a, b) => sizes.get(b) - sizes.get(a) || a - b);
  const id = new Map(ranked.map((l, rank) => [l, rank]));
  return { cluster: Uint16Array.from(label, (l) => id.get(l)), count: ranked.length };
}
