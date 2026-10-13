"use client";
// Documents (slices R2, N2, and D3). In a team's scope, the team's space, laid out like Datavine's documentation: its
// sources as a tree of folders and documents down the left, and on the right either the space's home or one document,
// opened in place in a reading column at its own address (#/t/<team>/documents/<id>), so links and Back work. At the
// org, the directory of your teams' spaces, then every document you can open, with filters. All of it reads through
// search's gates, so a space holds exactly what you may open.
import { createElement as h, useEffect, useState } from "react";
import { ago, kl } from "../lib/client.js";
import { Icon } from "./icons.js";
import { at, inScope } from "./scope.js";
import { toolName } from "./terraform.js";
import { Alert, Empty, FRONT, SkeletonRows, Table, Tier, concept, tierOf } from "./ui.js";

const enc = encodeURIComponent;
const CLASSES = ["public", "internal", "confidential", "restricted"];
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
// Only web addresses become links: a source address can come from an admin's settings, and "javascript:" must not run.
export const linkable = (uri) => typeof uri === "string" && /^https?:\/\//i.test(uri);

export function query(filters, offset = 0) {
  const p = new URLSearchParams({ limit: "50" });
  for (const [k, v] of Object.entries(filters)) if (v) p.set(k, v);
  if (offset) p.set("offset", String(offset));
  return `documents?${p.toString()}`;
}

const KINDS = { github: "GitHub", gdrive: "Google Drive", google_drive: "Google Drive", confluence: "Confluence", notion: "Notion" };
const kindLabel = (kind) => KINDS[kind] || cap(kind || "source");
const decode = (part) => { try { return decodeURIComponent(part); } catch { return part; } };

// Where a document sits in its source: a group (the source itself, such as a repository) and the folders inside it.
export function placeOf(doc) {
  const kind = doc.source || "source";
  let url = null;
  try { url = linkable(doc.source_uri) ? new URL(doc.source_uri) : null; } catch { url = null; }
  if (!url) return { group: kindLabel(kind), folders: [] };
  const path = url.pathname.split("/").filter(Boolean).map(decode);
  if (url.hostname === "github.com" && path.length >= 2) {
    const [owner, repo, mode, , ...rest] = path; // owner/repo/blob/<ref>/folders.../file
    return { group: `GitHub: ${owner}/${repo}`, folders: mode === "blob" || mode === "tree" ? rest.slice(0, -1) : [] };
  }
  if (kind === "gdrive" || kind === "google_drive" || /(^|\.)google\.com$/.test(url.hostname)) return { group: "Google Drive", folders: [] };
  if (kind === "confluence" || kind === "notion") return { group: `${kindLabel(kind)}: ${url.hostname}`, folders: [] }; // page IDs, not folders
  return { group: `${kindLabel(kind)}: ${url.hostname}`, folders: path.slice(0, -1) };
}

// A space's tree: each source a group, its folders nested, its documents at the folder they sit in.
export function spaceTree(docs) {
  const groups = new Map();
  const node = (name) => ({ name, folders: new Map(), docs: [], count: 0 });
  for (const doc of docs) {
    const { group, folders } = placeOf(doc);
    if (!groups.has(group)) groups.set(group, node(group));
    let at = groups.get(group);
    at.count += 1;
    for (const folder of folders) {
      if (!at.folders.has(folder)) at.folders.set(folder, node(folder));
      at = at.folders.get(folder);
      at.count += 1;
    }
    at.docs.push(doc);
  }
  return [...groups.values()].sort((a, b) => a.name.localeCompare(b.name));
}

export const docAt = (team, id) => at(team, "documents", enc(id)); // a document in its space: an address of its own

function Branch({ node, depth, selected, team }) {
  const folders = [...node.folders.values()].sort((a, b) => a.name.localeCompare(b.name));
  return h("details", { className: "branch", open: depth === 0 },
    h("summary", null, h("span", null, node.name), h("small", null, String(node.count))),
    folders.map((f) => h(Branch, { key: f.name, node: f, depth: depth + 1, selected, team })),
    node.docs.length ? h("ul", null, node.docs.map((d) => h("li", { key: d.id },
      h("a", { href: docAt(team, d.id), "aria-current": selected === d.id ? "page" : undefined, title: d.title },
        h("span", { className: `dot ${tierOf(d.tier)}`, "aria-hidden": true }), h("span", { className: "branch-title" }, d.title))))) : null);
}

// OKF keeps metadata in front matter: the reading column shows the body.
function bodyOf(okf) {
  const m = (okf || "").match(FRONT);
  return m ? okf.slice(m[0].length).replace(/^\s+/, "") : okf || "";
}

// One document, read in place: where it sits in its source, its title, what it is, then the text and what relates to it.
export function Reader({ id, team, doc = null }) {
  const [state, setState] = useState({ loading: true });
  const load = () => {
    const key = enc(id);
    setState({ loading: true });
    Promise.all([kl(`concepts/${key}`), kl(`concepts/${key}/related`).catch(() => ({ related: [] }))])
      .then(([c, related]) => setState({ c, related: related.related || [] }))
      .catch((e) => setState({ error: e.message }));
  };
  useEffect(load, [id]);
  if (state.loading) {
    return h("article", { className: "reader", "aria-busy": "true" },
      h("span", { className: "skeleton", style: { width: "55%", height: 26, marginBottom: 16 } }), h(SkeletonRows, { rows: 8, widths: [95, 88, 92] }));
  }
  if (state.error) return h("article", { className: "reader" }, h(Alert, { title: "This document isn't available.", onRetry: load }, state.error));
  const { c, related } = state;
  const place = doc && doc.source ? placeOf(doc) : null;
  return h("article", { className: "reader" },
    place ? h("p", { className: "reader-path" }, [place.group, ...place.folders].join(" › ")) : null,
    h("h1", { className: "reader-title" }, c.title),
    h("div", { className: "reader-meta" },
      h(Tier, { tier: tierOf(c.tier) }),
      doc && doc.classification ? h("span", null, cap(doc.classification)) : null,
      doc && doc.created_at ? h("span", null, `Added ${ago(doc.created_at)}`) : null,
      doc && linkable(doc.source_uri) ? h("a", { className: "a", href: doc.source_uri, target: "_blank", rel: "noreferrer" }, `Open in ${kindLabel(doc.source)} `, h(Icon, { name: "external", size: 12 })) : null,
      h("div", { className: "actions reader-actions" },
        h("a", { className: "btn btn-sm", href: inScope(concept(id), team) }, "Full page and history"),
        h("a", { className: "btn btn-primary btn-sm", href: inScope(`#/ask/focus:${enc(id)}`, team) }, h(Icon, { name: "ask", size: 14 }), "Ask about this"))),
    h("div", { className: "prose" }, bodyOf(c.okf)),
    related.length ? h("section", { className: "reader-related", "aria-labelledby": "related-h" },
      h("h2", { id: "related-h" }, "Related"),
      h("ul", { className: "linklist" }, related.map((r) => h("li", { key: r.id },
        h("a", { href: docAt(team, r.id) }, h("span", { className: `dot ${tierOf(r.tier)}`, "aria-hidden": true }), h("span", null, r.title),
          h("small", null, r.relation)))))) : null);
}

function Detail({ doc, team = "", onClose }) {
  return h("aside", { className: "panel doc-detail", "aria-label": `About ${doc.title}` },
    h("div", { className: "panel-head" }, h("h2", null, doc.title),
      h("button", { type: "button", className: "btn btn-ghost btn-sm", "aria-label": "Close", onClick: onClose }, h(Icon, { name: "x", size: 14 }))),
    h("dl", { className: "facts" },
      h("dt", null, "Tier"), h("dd", null, h(Tier, { tier: doc.tier })),
      h("dt", null, "Classification"), h("dd", null, cap(doc.classification)),
      h("dt", null, "Teams"), h("dd", null, doc.workspaces.join(", ")),
      h("dt", null, "Source"), h("dd", null, linkable(doc.source_uri)
        ? h("a", { className: "a", href: doc.source_uri, target: "_blank", rel: "noreferrer" }, cap(doc.source), " ", h(Icon, { name: "external", size: 12 }))
        : cap(doc.source)),
      h("dt", null, "Added"), h("dd", null, ago(doc.created_at))),
    h("div", { className: "actions" },
      h("a", { className: "btn btn-primary btn-sm", href: inScope(concept(doc.id), team) }, "Open"),
      h("a", { className: "btn btn-sm", href: inScope(`#/ask/focus:${enc(doc.id)}`, team) }, h(Icon, { name: "ask", size: 14 }), "Ask about this")));
}

// A team's space: up to 500 documents, enough for its whole tree, through the same gates as everything else.
export function SpaceView({ me, team, teamName = "", doc: docId = "" }) {
  const [docs, setDocs] = useState(null);
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("");
  const load = () => {
    let alive = true;
    setDocs(null);
    setError(null);
    (async () => {
      const all = [];
      let offset = 0;
      for (let page = 0; page < 5; page += 1) {
        const r = await kl(`documents?scope=${enc(team)}&limit=100${offset ? `&offset=${offset}` : ""}`);
        all.push(...r.documents);
        if (r.next_offset === null || r.next_offset === undefined) break;
        offset = r.next_offset;
      }
      if (alive) setDocs(all);
    })().catch((e) => { if (alive) setError(e.message); });
    kl(`workspaces/${enc(team)}`).then((t) => { if (alive) setInfo(t); }).catch(() => {});
    return () => { alive = false; };
  };
  useEffect(load, [team]);
  const name = teamName || (info && info.name) || team;
  const term = filter.trim().toLowerCase();
  const shown = docs && term ? docs.filter((d) => d.title.toLowerCase().includes(term)) : docs;
  const tree = shown ? spaceTree(shown) : [];
  const sources = docs ? spaceTree(docs).length : 0;
  const admin = Boolean(me && (me.org_admin || me.admin_of.includes(team)));
  const settings = `#/t/${enc(team)}/settings/sources`;
  const open = docId && docs ? docs.find((d) => d.id === docId) || null : null;

  const side = h("aside", { className: "space-nav" },
    h("div", { className: "space-nav-head" },
      h("a", { className: "space-title", href: at(team, "documents") }, name),
      h("span", { className: "muted small" }, docs === null ? "Loading…" : `${plural(docs.length, "document")} · ${plural(sources, "source")}`),
      h("input", { className: "input", type: "search", value: filter, onChange: (e) => setFilter(e.target.value),
                   placeholder: "Filter this space", "aria-label": "Filter this space by title" })),
    h("nav", { className: "space-tree", "aria-label": `Documents in ${name}` },
      docs === null ? (error ? null : h(SkeletonRows, { rows: 8, widths: [70, 50, 60] }))
        : tree.length ? tree.map((g) => h(Branch, { key: g.name, node: g, depth: 0, selected: docId, team }))
          : h("p", { className: "muted small", style: { padding: 8 } }, docs.length ? "No document's title matches." : "Nothing here yet.")));

  const home = h("div", { className: "space-home" },
    h("div", { className: "page-head" },
      h("div", null, h("h1", null, name),
        h("p", { className: "lede" }, docs === null ? "This team's space." : `${plural(docs.length, "document")} from ${plural(sources, "source")}`,
          info && info.managed_by ? ` · Managed by ${toolName(info.managed_by.by)}` : "")),
      h("div", { className: "actions" },
        admin ? h("a", { className: "btn", href: settings }, "Add a source") : null,
        h("a", { className: "btn btn-primary", href: inScope("#/ask", team) }, h(Icon, { name: "ask", size: 14 }), "Ask this space"))),
    error ? h(Alert, { onRetry: load }, error) : null,
    docs === null ? (error ? null : h("section", { className: "panel" }, h(SkeletonRows, { rows: 6, widths: [50, 20, 14, 10] })))
      : !docs.length ? h("section", { className: "panel" }, h(Empty, { title: "This space is empty", icon: "docs",
          action: admin ? h("a", { className: "btn btn-sm", href: settings }, "Add a source") : null },
          "Its documents arrive from the sources its admins connect."))
        : h("section", { className: "panel", "aria-labelledby": "space-recent-h" },
            h("div", { className: "panel-head" }, h("h2", { id: "space-recent-h" }, "Recently added"), h("span", { className: "faint small" }, "Newest first")),
            h(Table, null,
              h("thead", null, h("tr", null, h("th", null, "Title"), h("th", { className: "hide-sm" }, "Source"), h("th", null, "Tier"), h("th", { className: "num" }, "Added"))),
              h("tbody", null, docs.slice(0, 12).map((d) => h("tr", { key: d.id },
                h("td", { className: "ellipsis" }, h("a", { className: "title-link", href: docAt(team, d.id) }, d.title)),
                h("td", { className: "hide-sm muted ellipsis" }, placeOf(d).group),
                h("td", null, h(Tier, { tier: tierOf(d.tier) })),
                h("td", { className: "num muted" }, ago(d.created_at))))))));

  return h("div", { className: "space-shell" }, side,
    h("div", { className: "space-main" }, docId ? h(Reader, { key: docId, id: docId, team, doc: open }) : home));
}

// The org's directory of spaces: one per team you're in, with what you can open in it, as a table (slice D3).
export function Spaces({ names = {} }) {
  const [home, setHome] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { kl("home").then(setHome).catch((e) => setError(e.message)); }, []);
  if (error) return h(Alert, null, error);
  if (!home) return h("section", { className: "panel" }, h(SkeletonRows, { rows: 3, widths: [40, 12, 20, 12] }));
  if (!home.teams.length) return h("section", { className: "panel" }, h(Empty, { title: "No spaces yet", icon: "docs" }, "You aren't in a team yet, so there are no spaces to show you."));
  return h("section", { className: "panel" }, h(Table, null,
    h("thead", null, h("tr", null, h("th", null, "Space"), h("th", { className: "num" }, "Documents"), h("th", { className: "hide-sm" }, "Last brought in"), h("th", null, "Your role"))),
    h("tbody", null, home.teams.map((t) => h("tr", { key: t.slug },
      h("td", { className: "ellipsis" }, h("a", { className: "title-link", href: at(t.slug, "documents") }, names[t.slug] || t.slug),
        h("div", { className: "sub mono" }, t.slug)),
      h("td", { className: "num" }, t.documents.toLocaleString()),
      h("td", { className: "hide-sm muted" }, t.last_sync ? ago(t.last_sync) : "Nothing yet"),
      h("td", null, t.role === "admin" ? "Admin" : "Member"))))));
}

// Every document you can open, from every source, newest first. Filters only narrow the list.
export function AllDocuments({ me }) {
  const [filters, setFilters] = useState({ scope: "", source: "", classification: "", tier: "", q: "" });
  const [typed, setTyped] = useState("");
  const [docs, setDocs] = useState(null);
  const [sources, setSources] = useState([]);
  const [next, setNext] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = (f, offset = 0) => {
    setBusy(true);
    return kl(query(f, offset)).then((r) => {
      setDocs((d) => (offset && d ? [...d, ...r.documents] : r.documents));
      setSources(r.sources || []);
      setNext(r.next_offset);
      setError(null);
    }).catch((e) => setError(e.message)).finally(() => setBusy(false));
  };
  useEffect(() => { setDocs(null); setOpen(null); load(filters); }, [JSON.stringify(filters)]);
  const set = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }));
  const narrowed = Object.values(filters).some(Boolean);

  return h("div", { className: "every-docs" },
    h("div", { className: "filters", role: "group", "aria-label": "Filter documents" },
      h("form", { role: "search", onSubmit: (e) => { e.preventDefault(); setFilters((f) => ({ ...f, q: typed.trim().length >= 2 ? typed.trim() : "" })); } },
        h("input", { className: "input", type: "search", value: typed, onChange: (e) => setTyped(e.target.value),
                     placeholder: "Filter by title", "aria-label": "Filter by title" })),
      h("select", { className: "select", value: filters.scope, onChange: set("scope"), "aria-label": "Team" },
        h("option", { value: "" }, "All my teams"), ...(me ? me.workspaces : []).map((ws) => h("option", { key: ws, value: ws }, ws))),
      h("select", { className: "select", value: filters.source, onChange: set("source"), "aria-label": "Source" },
        h("option", { value: "" }, "Every source"), ...sources.map((s) => h("option", { key: s, value: s }, cap(s)))),
      h("select", { className: "select", value: filters.classification, onChange: set("classification"), "aria-label": "Classification" },
        h("option", { value: "" }, "Any classification"), ...CLASSES.map((c) => h("option", { key: c, value: c }, cap(c)))),
      h("select", { className: "select", value: filters.tier, onChange: set("tier"), "aria-label": "Tier" },
        h("option", { value: "" }, "Any tier"), h("option", { value: "gold" }, "Gold"), h("option", { value: "silver" }, "Silver"),
        h("option", { value: "evidence" }, "Evidence"))),
    error ? h(Alert, { onRetry: () => load(filters) }, error) : null,
    docs === null ? h(SkeletonRows, { rows: 6, widths: [60, 30, 20, 20] })
      : !docs.length ? h(Empty, null, narrowed ? "No document you can open matches these filters." : "Nothing here yet. Connect a source or import an export under Admin.")
        : h("div", { className: open ? "docs with-detail" : "docs" },
            h("ul", { className: "doc-rows", "aria-label": "Documents" }, docs.map((d) => h("li", { key: d.id, className: open && open.id === d.id ? "on" : "" },
              h("button", { type: "button", className: "doc-row", "aria-pressed": Boolean(open && open.id === d.id), onClick: () => setOpen(d) },
                h("span", { className: `dot ${tierOf(d.tier)}`, "aria-hidden": true }),
                h("span", { className: "doc-title" }, d.title),
                h("span", { className: "doc-meta" }, `${d.workspaces.join(", ")} · ${cap(d.source)} · ${cap(d.classification)}`),
                h("span", { className: "doc-when" }, ago(d.created_at)))))),
            open ? h(Detail, { doc: open, onClose: () => setOpen(null) }) : null),
    next !== null && docs && docs.length ? h("div", { className: "actions" },
      h("button", { type: "button", className: "btn", disabled: busy, onClick: () => load(filters, next) }, busy ? "Loading…" : "Show more")) : null);
}

// The org's Documents: the spaces first, then everything.
export default function DocumentsView({ me, names = {} }) {
  return h("div", { className: "page" },
    h("div", { className: "page-head" }, h("div", null, h("h1", null, "Documents"),
      h("p", { className: "lede" }, "Every team's space, and every document you can open."))),
    h("section", { "aria-labelledby": "spaces-h" }, h("h2", { id: "spaces-h", className: "section-title" }, "Spaces"), h(Spaces, { names })),
    h("section", { "aria-labelledby": "every-h" }, h("h2", { id: "every-h", className: "section-title" }, "Every document"), h(AllDocuments, { me })));
}
