"use client";
import { inScope } from "./scope.js";
// The knowledge map: everything the signed-in user can see, as one living graph. The data comes from
// GET /v1/graph, which applies the same permission path as search, so hidden nodes never reach this code
// and every number here counts visible things. The graph itself is in knowledge-graph.js.
import { createElement as h, useEffect, useMemo, useRef, useState } from "react";
import { ago, day, kl } from "../lib/client.js";
import { Icon } from "./icons.js";
import KnowledgeGraph from "./knowledge-graph.js";
import { Alert, TIERS, concept, tierOf } from "./ui.js";

const ms = (iso) => Date.parse(iso);
const REPLAY_MS = 6000; // the whole history, replayed in six seconds
const ENTITY_KINDS = { system: "System", team: "Team", person: "Person", channel: "Channel", dataset: "Dataset", job: "Job" }; // from text (slice N) and lineage (slice K3)
// How a link was made, in words (slices N and K2): what the inspector shows beside each neighbor.
const HOW = (e) => (e.kind === "reference" ? `recorded reference${e.evidence ? ` via ${e.evidence}` : ""}`
  : e.kind === "relation" ? `curated: ${e.relation}` : e.kind === "derived-from" ? "derived from it"
  : e.kind === "semantic" ? "similar meaning" : e.kind === "mentions" ? "named in the text"
  : e.kind === "lineage" ? "data feeds it" : e.kind === "describes" ? "describes it" : e.kind); // lineage: slice K3

export default function MapView({ team = "" } = {}) { // in a team's scope, only that team (slice N1)
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState("");
  const [term, setTerm] = useState("");
  const [tiers, setTiers] = useState({ gold: true, silver: true, evidence: true, entity: true });
  const [teams, setTeams] = useState({});
  const [at, setAt] = useState(null); // replay position in ms; null means live
  const [playing, setPlaying] = useState(false);
  const [selected, setSelected] = useState(null);
  const api = useRef(null);
  const search = useRef(null);

  const load = () => {
    setError(null);
    setData(null);
    kl(`graph?scope=${team ? encodeURIComponent(team) : "org"}&limit=5000`)
      .then((g) => {
        setData(g);
        setTeams(Object.fromEntries(g.clusters.map((c) => [c.workspace, true])));
      })
      .catch((e) => setError(e.message));
  };
  useEffect(load, [team]);
  useEffect(() => { // filter as you type, without refiltering on every keystroke
    const t = setTimeout(() => setTerm(query.trim().toLowerCase()), 150);
    return () => clearTimeout(t);
  }, [query]);
  useEffect(() => { // "/" filters, from anywhere on the map page
    const keys = (e) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement && document.activeElement.tagName);
      if (e.key === "/" && !typing) {
        e.preventDefault();
        if (search.current) search.current.focus();
      }
    };
    window.addEventListener("keydown", keys);
    return () => window.removeEventListener("keydown", keys);
  }, []);

  const span = useMemo(() => {
    if (!data || !data.nodes.length) return null;
    const times = data.nodes.map((n) => ms(n.since));
    return [Math.min(...times), Math.max(...times)];
  }, [data]);

  // Replay on requestAnimationFrame: each step is a filter change, so nodes scale in as history plays.
  useEffect(() => {
    if (!playing || !span) return undefined;
    let raf = 0;
    let last = performance.now();
    const tick = (now) => {
      const dt = now - last;
      last = now;
      setAt((cur) => Math.min((cur ?? span[0]) + (dt / REPLAY_MS) * (span[1] - span[0] || 1), span[1]));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, span]);
  useEffect(() => {
    if (playing && span && at !== null && at >= span[1]) {
      setPlaying(false);
      setAt(null);
    }
  }, [playing, span, at]);

  const passes = (n, ignoreTier) => (ignoreTier || tiers[tierOf(n.tier)]) && n.workspaces.some((w) => teams[w])
    && (at === null || ms(n.since) <= at) && (!term || n.title.toLowerCase().includes(term) || n.id.toLowerCase().includes(term));
  const visible = useMemo(() => (data ? new Set(data.nodes.filter((n) => passes(n)).map((n) => n.id)) : null), [data, tiers, teams, at, term]);
  const tierCounts = useMemo(() => {
    const c = {};
    if (data) for (const n of data.nodes) if (passes(n, true)) c[tierOf(n.tier)] = (c[tierOf(n.tier)] || 0) + 1;
    return c;
  }, [data, teams, at, term]);
  const stats = useMemo(() => {
    if (!data || !visible) return null;
    const pairs = new Map(); // one link per pair of nodes, as the graph draws them
    for (const e of data.edges) {
      if (e.source === e.target || !visible.has(e.source) || !visible.has(e.target)) continue;
      const key = e.source < e.target ? `${e.source} ${e.target}` : `${e.target} ${e.source}`;
      pairs.set(key, Boolean(pairs.get(key)) || Boolean(e.cross_team));
    }
    return { nodes: visible.size, links: pairs.size, cross: [...pairs.values()].filter(Boolean).length };
  }, [data, visible]);
  // The selected node's neighbors, strongest kind of link first: curated, recorded, then inferred.
  const linksOf = useMemo(() => {
    if (!data || !selected) return [];
    const titles = new Map(data.nodes.map((n) => [n.id, n.title]));
    const rank = { relation: 0, "derived-from": 1, reference: 2, mentions: 3, semantic: 4 };
    return data.edges.filter((e) => e.source === selected.id || e.target === selected.id)
      .map((e) => { const other = e.source === selected.id ? e.target : e.source; return { id: other, title: titles.get(other) || other, how: HOW(e), rank: rank[e.kind] ?? 9 }; })
      .filter((l) => !visible || visible.has(l.id))
      .sort((a, b) => a.rank - b.rank || a.title.localeCompare(b.title));
  }, [data, selected, visible]);
  const degree = useMemo(() => {
    const d = new Map();
    if (data) for (const e of data.edges) for (const id of [e.source, e.target]) d.set(id, (d.get(id) || 0) + 1);
    return d;
  }, [data]);
  const clearFilters = () => {
    setQuery("");
    setTerm("");
    setTiers({ gold: true, silver: true, evidence: true, entity: true });
    if (data) setTeams(Object.fromEntries(data.clusters.map((c) => [c.workspace, true])));
    setAt(null);
    setPlaying(false);
  };

  let stage;
  if (error) {
    stage = h("div", { className: "graph-stage" }, h("div", { className: "graph-empty" }, h(Alert, { title: "The map didn't load.", onRetry: load }, error)));
  } else if (!data) {
    stage = h("div", { className: "graph-stage" }, h("div", { className: "graph-skeleton", "aria-busy": "true", "aria-label": "Loading the map" },
      [0, 1, 2, 3, 4].map((i) => h("span", { key: i, style: { animationDelay: `${i * 120}ms` } }))));
  } else if (!data.nodes.length) {
    stage = h("div", { className: "graph-stage" }, h("div", { className: "graph-empty" },
      h("p", null, "No knowledge entries yet"), h("a", { href: "#/admin" }, "Add your first entity →")));
  } else {
    stage = h("div", { className: "graph-stage" },
      h(KnowledgeGraph, { nodes: data.nodes, edges: data.edges, visible, selectedId: selected && selected.id,
                          onSelect: setSelected, onReady: (a) => { api.current = a; } }),
      visible && !visible.size ? h("div", { className: "graph-empty overlay" },
        h("p", null, "Nothing on the map matches these filters."),
        h("button", { type: "button", className: "btn btn-sm", onClick: clearFilters }, "Clear filters")) : null);
  }
  const toggle = (key, pressed, onClick, lead, label, count, title) => h("button", {
    key, type: "button", className: "toggle", "aria-pressed": pressed, onClick, title,
  }, lead, label, h("span", { className: "count" }, count));
  const fact = (label, value, mono) => [h("dt", { key: `${label}-t` }, label), h("dd", { key: `${label}-d`, className: mono ? "mono" : "" }, value)];

  return h("div", { className: "page map-page" },
    h("div", { className: "map-head" },
      h("div", null, h("h1", null, "Map"),
        h("p", { className: "lede" }, at === null ? "Everything you can see, and how it connects." : `As it stood on ${day(at)}.`)),
      stats ? h("dl", { className: "map-stats" },
        h("div", null, h("dt", null, "Nodes"), h("dd", null, stats.nodes.toLocaleString())),
        h("div", null, h("dt", null, "Links"), h("dd", null, stats.links.toLocaleString())),
        h("div", null, h("dt", null, "Across teams"), h("dd", null, stats.cross.toLocaleString()))) : null),
    h("section", { className: "graph-card", "aria-label": "Knowledge map" },
      h("div", { className: "graph-bar" },
        stats ? h("span", { className: "graph-summary" },
          `${stats.nodes.toLocaleString()} entities · ${stats.links.toLocaleString()} relationships`) : null,
        h("label", { className: "searchbox sm graph-search" }, h(Icon, { name: "search" }),
          h("input", { ref: search, value: query, onChange: (e) => setQuery(e.target.value), placeholder: "Filter the map", "aria-label": "Filter the map by name" }),
          h("kbd", null, "/")),
        h("div", { className: "graph-toggles", role: "group", "aria-label": "Tiers" },
          Object.entries(TIERS).map(([key, t]) => toggle(key, tiers[key], () => setTiers({ ...tiers, [key]: !tiers[key] }),
            h("span", { className: `dot ${key}` }), t.label, tierCounts[key] || 0, t.hint))),
        data && data.clusters.length > 1 ? h("div", { className: "graph-toggles", role: "group", "aria-label": "Teams" },
          data.clusters.map((c) => toggle(c.workspace, Boolean(teams[c.workspace]), () => setTeams({ ...teams, [c.workspace]: !teams[c.workspace] }),
            null, c.workspace, c.nodes))) : null,
        h("div", { className: "spacer" }),
        h("div", { className: "btn-group", role: "group", "aria-label": "Zoom" },
          h("button", { type: "button", className: "btn btn-icon btn-sm", onClick: () => api.current && api.current.zoomIn(), "aria-label": "Zoom in" }, h(Icon, { name: "plus" })),
          h("button", { type: "button", className: "btn btn-icon btn-sm", onClick: () => api.current && api.current.zoomOut(), "aria-label": "Zoom out" }, h(Icon, { name: "minus" })),
          h("button", { type: "button", className: "btn btn-icon btn-sm", onClick: () => api.current && api.current.fit(), "aria-label": "Fit to screen" }, h(Icon, { name: "fit" })))),
      stage,
      span ? h("div", { className: "replay" },
        h("button", { type: "button", className: "btn btn-ghost btn-icon", "aria-label": playing ? "Pause replay" : "Replay how the map grew",
                      onClick: () => { if (playing) setPlaying(false); else { setAt(span[0]); setPlaying(true); } } },
          h(Icon, { name: playing ? "pause" : "play" })),
        h("span", { className: "when" }, day(at === null ? span[1] : at)),
        h("input", { type: "range", min: span[0], max: span[1], step: 1, value: at ?? span[1], "aria-label": "Replay position",
                     onChange: (e) => { setPlaying(false); setAt(Number(e.target.value)); } }),
        at === null ? h("span", { className: "live" }, h("span", { className: "dot ok" }), "Live")
          : h("button", { type: "button", className: "btn btn-sm", onClick: () => { setPlaying(false); setAt(null); } }, "Back to live")) : null,
      selected ? h("aside", { className: "graph-inspector", "aria-label": "Selected node" },
        h("div", { className: "inspector-head" },
          h("h2", null, selected.title),
          h("button", { type: "button", className: "btn btn-ghost btn-sm btn-icon", onClick: () => setSelected(null), "aria-label": "Close" }, h(Icon, { name: "x" }))),
        h("div", { className: "inspector-tags" },
          h("span", { className: "graph-badge" }, selected.tier === "entity" ? ENTITY_KINDS[selected.kind] || "Entity" : TIERS[tierOf(selected.tier)].label),
          h("span", { className: "inspector-context" }, selected.workspaces.join(", "))),
        linksOf.length ? h("ul", { className: "inspector-links", "aria-label": "Linked to" },
          linksOf.slice(0, 4).map((l) => h("li", { key: l.id }, h("span", null, l.title), h("small", null, l.how))),
          linksOf.length > 4 ? h("li", { className: "more" }, `and ${linksOf.length - 4} more`) : null)
          : h("p", { className: "faint small" }, "Nothing visible links here yet."),
        selected.tier === "entity"
          ? [h("dl", { key: "facts", className: "facts" },
               fact("Named in", `${degree.get(selected.id) || 0} document${degree.get(selected.id) === 1 ? "" : "s"}`),
               fact("Mentions", selected.mentions, true),
               fact("First seen", `${day(selected.since)} · ${ago(selected.since)}`, true)),
             h("p", { key: "hint", className: "faint small" }, "Found in your documents' text. Select a highlighted document to open it.")]
          : [h("dl", { key: "facts", className: "facts" },
               fact("Classification", selected.classification),
               fact("Source", selected.source),
               fact("First seen", `${day(selected.since)} · ${ago(selected.since)}`, true)),
             h("div", { key: "actions", className: "inspector-actions" },
               h("a", { key: "open", className: "btn btn-primary", href: inScope(concept(selected.id), team) }, "Open page and history", h(Icon, { name: "arrowRight", size: 14 })),
               h("a", { key: "ask", className: "btn", href: inScope(`#/ask/focus:${encodeURIComponent(selected.id)}`, team) }, h(Icon, { name: "ask", size: 14 }), "Ask about this"))]) : null),
    data ? h("p", { className: "faint small map-note" }, data.note) : null);
}
