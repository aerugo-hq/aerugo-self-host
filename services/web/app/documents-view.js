"use client";
// Documents (slices R2, N2, and D3). In a team's scope, the team's space, laid out like Datavine's documentation: its
// sources as a tree of folders and documents down the left, and on the right either the space's home or one document,
// opened in place in a reading column at its own address (#/t/<team>/documents/<id>), so links and Back work. At the
// org, the directory of your teams' spaces, then every document you can open, with filters. All of it reads through
// search's gates, so a space holds exactly what you may open.
import { createElement as h, useEffect, useState } from "react";
import { ago, kl } from "../lib/client.js";
import { SourceIcon, sourceName } from "./brand.js";
import { Icon } from "./icons.js";
import { markdown } from "./markdown.js";
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

const kindLabel = (kind) => sourceName(kind || "source"); // one name for a source, the same everywhere (brand.js)
// The sidebar's order: curated knowledge pages first, then each source the way people think of them.
const KIND_ORDER = ["okf", "confluence", "jira", "github", "gdrive", "google_drive", "notion", "slack", "microsoft"];
const rankOf = (kind) => { const i = KIND_ORDER.indexOf(kind); return i < 0 ? KIND_ORDER.length : i; };
const decode = (part) => { try { return decodeURIComponent(part); } catch { return part; } };

// Where a document sits in its source: a group (the source itself, such as a repository) and the folders inside it.
export function placeOf(doc) {
  const kind = doc.source || "source";
  const named = doc.container && doc.container.name; // a space, project, drive, repository, or import, by its own name
  // A live Confluence page sits under its ancestors: its path is their titles, then its own (an export has no path).
  const tree = kind === "confluence" && typeof doc.path === "string" && doc.path ? doc.path.split(" › ").slice(0, -1) : null;
  if (named && kind !== "github") {
    const { folders } = placeFromAddress(doc);
    return { group: `${kindLabel(kind)}: ${named}`, folders: tree || (folders[0] === named ? folders.slice(1) : folders) };
  }
  return placeFromAddress(doc);
}

function placeFromAddress(doc) {
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

// A document's source, for its mark: its kind, and its place without the kind's name in front, since the mark says that.
export function sourceOf(doc) {
  const { group } = placeOf(doc);
  const cut = group.indexOf(": ");
  return { kind: doc.source || "source", label: cut >= 0 ? group.slice(cut + 2) : group };
}

// A space's tree: each source a group, its folders nested, its documents at the folder they sit in.
export function spaceTree(docs) {
  const groups = new Map();
  const node = (name) => ({ name, folders: new Map(), docs: [], count: 0 });
  for (const doc of docs) {
    const { group, folders } = placeOf(doc);
    if (!groups.has(group)) groups.set(group, { ...node(group), ...sourceOf(doc) }); // a source's group carries its mark
    let at = groups.get(group);
    at.count += 1;
    for (const folder of folders) {
      if (!at.folders.has(folder)) at.folders.set(folder, node(folder));
      at = at.folders.get(folder);
      at.count += 1;
    }
    at.docs.push(doc);
  }
  return [...groups.values()].sort((a, b) => rankOf(a.kind) - rankOf(b.kind) || a.name.localeCompare(b.name));
}

export const docAt = (team, id) => at(team, "documents", enc(id)); // a document in its space: an address of its own

// Whether a folder holds the open page, anywhere inside it.
const holds = (node, id) => Boolean(id) && (node.docs.some((d) => d.id === id) || [...node.folders.values()].some((f) => holds(f, id)));

// The folds of the folders a page sits in, by their keys in the tree, so opening a page unfolds its way there.
export function foldsTo(doc) {
  const { group, folders } = placeOf(doc);
  return folders.reduce((keys, f) => [...keys, `${keys[keys.length - 1]}/${f}`], [`g:${group}`]);
}

// The tree remembers what's open: a source starts unfolded and its folders folded; opening a page unfolds its folders;
// nothing folds unless you fold it. A search unfolds everything it matches without changing what's remembered.
function Branch({ node, depth, path, selected, team, expand = false, folds = {}, onFold = () => {} }) {
  const folders = [...node.folders.values()].sort((a, b) => a.name.localeCompare(b.name));
  const open = expand || (folds[path] ?? (depth === 0 || holds(node, selected)));
  return h("details", { className: "branch", open },
    h("summary", { title: node.name, onClick: (e) => { e.preventDefault(); onFold(path, !open); } },
      node.kind ? h(SourceIcon, { kind: node.kind }) : null, h("span", null, node.label || node.name), h("small", null, String(node.count))),
    folders.map((f) => h(Branch, { key: f.name, node: f, depth: depth + 1, path: `${path}/${f.name}`, selected, team, expand, folds, onFold })),
    node.docs.length ? h("ul", null, node.docs.map((d) => h("li", { key: d.id },
      h("a", { href: docAt(team, d.id), "aria-current": selected === d.id ? "page" : undefined, title: d.title,
               onPointerDown: (e) => { if (e.button === 0) prefetch(d.id, d); } }, // opening has begun: start reading
        h("span", { className: `dot ${tierOf(d.tier)}`, "aria-hidden": true }), h("span", { className: "branch-title" }, d.title))))) : null);
}

// OKF keeps metadata in front matter: the reading column shows the body.
function bodyOf(okf) {
  const m = (okf || "").match(FRONT);
  return m ? okf.slice(m[0].length).replace(/^\s+/, "") : okf || "";
}

// One document, read in place, as in Datavine: a bar with where it sits and what you can do, then a reading column with
// its title, what it is, and its text, rendered, embedded videos playing in place; then what relates to it. A curated
// page reads through its page, with who to ask; any other document (a file, an issue, a page from a source) through
// its sections, which come only if you may open it.
// What's been read this session, so going back to a page is instant; at most READ_LIMIT pages, the oldest dropped.
const READ = new Map();
const READ_LIMIT = 50;
const reading = new Map(); // id -> the read in flight, so a press and the click that follows share one
function remember(id, state) {
  READ.delete(id);
  READ.set(id, state);
  if (READ.size > READ_LIMIT) READ.delete(READ.keys().next().value);
}
function readDocument(id, doc) {
  if (reading.has(id)) return reading.get(id);
  const key = enc(id);
  const sections = () => kl(`documents/${key}/content`).then((d) => ({ d }));
  const page = () => Promise.all([kl(`concepts/${key}`), kl(`concepts/${key}/related`).catch(() => ({ related: [] })),
               kl(`concepts/${key}/people`).catch(() => ({ people: [], teams: [] }))]) // who to ask (slice K6)
    .then(([c, related, people]) => ({ c, related: related.related || [], people }));
  const read = (doc && doc.tier === "evidence" ? sections() : page().catch((e) => (/not found/i.test(e.message) ? sections() : Promise.reject(e))))
    .then((state) => { remember(id, state); return state; })
    .finally(() => reading.delete(id));
  reading.set(id, read);
  return read;
}
// Pressing a page's link starts reading it, a moment before the click lands. Never on hover: a read is audited.
export const prefetch = (id, doc) => { if (!READ.has(id)) readDocument(id, doc).catch(() => {}); };

export function Reader({ id, team, doc = null, spaceName = "" }) {
  const [state, setState] = useState(() => READ.get(id) || { loading: true });
  const [busy, setBusy] = useState(false); // the next page is on its way; the current one stays until it's here
  const load = () => {
    let alive = true;
    if (READ.has(id)) setState(READ.get(id)); // read before: shown at once, then read again in case it changed
    setBusy(true);
    readDocument(id, doc)
      .then((next) => { if (alive) { setState(next); setBusy(false); } })
      .catch((e) => { if (alive) { setState({ error: e.message }); setBusy(false); } });
    return () => { alive = false; };
  };
  useEffect(load, [id]);
  if (state.loading) {
    return h("article", { className: "reader", "aria-busy": "true" },
      h("span", { className: "skeleton", style: { width: "55%", height: 26, marginBottom: 16 } }), h(SkeletonRows, { rows: 8, widths: [95, 88, 92] }));
  }
  if (state.error) return h("article", { className: "reader" }, h(Alert, { title: "This document isn't available.", onRetry: load }, state.error));
  const { c, d, related = [], people } = state;
  const shown = c || d; // the page, or the document's sections
  const about = doc || d; // where it sits, what it is
  const askable = people ? [...people.people.slice(0, 5).map((p) => ({ key: p.user, name: p.user, why: p.reasons.join(" · ") })),
    ...people.teams.filter((t) => !t.members_shown).map((t) => ({ key: `team:${t.team}`, name: `The ${t.team} team`, why: t.reason }))] : [];
  const place = about && about.source ? placeOf(about) : null;
  const source = about && about.source ? sourceOf(about) : null;
  const body = c ? markdown(bodyOf(c.okf), { media: true })
    : d.sections.flatMap((x, i) => [x.heading ? h("h2", { key: `h${i}` }, x.heading) : null, ...markdown(x.body, { media: true })]);
  return h("div", { className: "doc-view", "aria-busy": busy ? "true" : undefined },
    busy ? h("div", { className: "doc-progress", role: "progressbar", "aria-label": "Opening the page" }) : null,
    h("div", { className: "doc-bar" },
      h("nav", { className: "doc-crumbs", "aria-label": "Where it sits" },
        h("a", { href: at(team, "documents") }, spaceName || team || "Documentation"),
        place ? h("p", { className: "reader-path", title: place.group }, h(SourceIcon, { kind: source.kind, label: kindLabel(source.kind) }),
          [source.label, ...place.folders].join(" › ")) : null),
      h("div", { className: "actions reader-actions" },
        about && linkable(about.source_uri) ? h("a", { className: "btn btn-sm btn-ghost", href: about.source_uri, target: "_blank", rel: "noreferrer" },
          h(SourceIcon, { kind: about.source }), `Open in ${kindLabel(about.source)}`, h(Icon, { name: "external", size: 12 })) : null,
        c ? h("a", { className: "btn btn-sm", href: inScope(concept(id), team) }, "Full page and history") : null,
        h("a", { className: "btn btn-primary btn-sm", href: inScope(`#/ask/focus:${enc(id)}`, team) }, h(Icon, { name: "ask", size: 14 }), "Ask about this"))),
    h("article", { className: "reader" },
    h("h1", { className: "reader-title" }, shown.title),
    h("div", { className: "reader-meta" },
      h(Tier, { tier: tierOf(shown.tier) }),
      about && about.classification ? h("span", null, cap(about.classification)) : null,
      about && about.source ? h("span", null, `From ${kindLabel(about.source)}`) : null,
      about && about.created_at ? h("span", null, `Added ${ago(about.created_at)}`) : null),
    h("div", { className: "prose doc-body" }, body.length ? body : h("p", { className: "muted" }, "This document has no text to show.")),
    related.length ? h("section", { className: "reader-related", "aria-labelledby": "related-h" },
      h("h2", { id: "related-h" }, "Related"),
      h("ul", { className: "linklist" }, related.map((r) => h("li", { key: r.id },
        h("a", { href: docAt(team, r.id) }, h("span", { className: `dot ${tierOf(r.tier)}`, "aria-hidden": true }), h("span", null, r.title),
          h("small", null, r.relation)))))) : null,
    askable.length ? h("section", { className: "reader-related", "aria-labelledby": "people-h" },
      h("h2", { id: "people-h" }, "Who to ask"),
      h("ul", { className: "linklist" }, askable.map((a) => h("li", { key: a.key }, h("span", null, a.name), h("small", null, a.why))))) : null));
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
  const [folds, setFolds] = useState({}); // what's open in the tree, remembered (index 4)
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
  const fold = (key, on) => setFolds((f) => ({ ...f, [key]: on }));
  useEffect(() => { if (open) setFolds((f) => ({ ...f, ...Object.fromEntries(foldsTo(open).map((k) => [k, true])) })); }, [open && open.id]);

  // Datavine's sidebar: every space you're in, the open one unfolded into its tree, and a search over its pages.
  const spaces = [...new Set([team, ...((me && me.workspaces) || [])])].sort((a, b) => a.localeCompare(b));
  const side = h("aside", { className: "space-nav" },
    h("div", { className: "space-nav-head" },
      h("span", { className: "space-nav-label" }, h(Icon, { name: "docs", size: 14 }), "Documentation"),
      h("input", { className: "input", type: "search", value: filter, onChange: (e) => setFilter(e.target.value),
                   placeholder: "Search pages…", "aria-label": `Search ${name}'s pages by title` })),
    h("nav", { className: "space-tree", "aria-label": "Your spaces" }, spaces.map((s) => s !== team
      ? h("a", { key: s, className: "space-title", href: at(s, "documents") }, h("span", { className: "space-dot", "aria-hidden": true }), s)
      : h("div", { key: s, className: "space-group" },
          h("a", { className: "space-title on", href: at(team, "documents"), "aria-current": docId ? undefined : "page" },
            h("span", { className: "space-dot", "aria-hidden": true }), name,
            h("small", null, docs === null ? "" : `${plural(docs.length, "page")} · ${plural(sources, "source")}`)),
          h("nav", { className: "space-pages", "aria-label": `Documents in ${name}` },
            docs === null ? (error ? null : h(SkeletonRows, { rows: 8, widths: [70, 50, 60] }))
              : tree.length ? tree.map((g) => h(Branch, { key: g.name, node: g, depth: 0, path: `g:${g.name}`, selected: docId, team, expand: Boolean(term), folds, onFold: fold }))
                : h("p", { className: "muted small", style: { padding: 8 } }, docs.length ? "No page's title matches." : "Nothing here yet."))))));

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
                h("td", { className: "hide-sm muted ellipsis", title: placeOf(d).group }, h(SourceIcon, { kind: d.source, label: kindLabel(d.source) }), " ", sourceOf(d).label),
                h("td", null, h(Tier, { tier: tierOf(d.tier) })),
                h("td", { className: "num muted" }, ago(d.created_at))))))));

  return h("div", { className: "space-shell" }, side,
    h("div", { className: "space-main" }, docId ? h(Reader, { id: docId, team, doc: open, spaceName: name }) : home));
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

// The org's Documents, laid out like Datavine's documentation: every space you're in down the left, each unfolding into
// its tree when you open it (its pages load then; a search unfolds them all), and on the right the documentation home or
// one document, opened in place at its own address (#/documents/<id>), its space unfolded to show where it sits. It all
// reads through search's gates, so a space holds exactly what you may open.
export default function DocumentsView({ me, names = {}, doc: docId = "" }) {
  const spaces = [...new Set((me && me.workspaces) || [])].sort((a, b) => a.localeCompare(b));
  const [open, setOpen] = useState(new Set(spaces.slice(0, 1))); // the spaces unfolded: the first, to start
  const [docs, setDocs] = useState({}); // each unfolded space's documents
  const [recent, setRecent] = useState(null); // across every space, for the home
  const [filter, setFilter] = useState("");
  const [error, setError] = useState(null);
  const [found, setFound] = useState(null); // the document opened by its address, before its space has loaded
  const [folds, setFolds] = useState({}); // what's open in each space's tree, remembered (index 6)
  const label = (x) => names[x] || x;
  const term = filter.trim().toLowerCase();
  async function loadSpace(space) {
    const all = [];
    let offset = 0;
    for (let page = 0; page < 5; page += 1) {
      const r = await kl(`documents?scope=${enc(space)}&limit=100${offset ? `&offset=${offset}` : ""}`);
      all.push(...r.documents);
      if (r.next_offset === null || r.next_offset === undefined) break;
      offset = r.next_offset;
    }
    setDocs((d) => ({ ...d, [space]: all }));
  }
  useEffect(() => { kl("documents?scope=org&limit=12").then((r) => setRecent(r.documents)).catch((e) => setError(e.message)); }, []);
  useEffect(() => {
    for (const x of term ? spaces : open) if (!(x in docs)) loadSpace(x).catch((e) => setError(e.message));
  }, [open, term]);
  const known = docId ? [...Object.values(docs).flat(), ...(recent || [])].find((d) => d.id === docId) || null : null;
  useEffect(() => {
    if (docId && !known) kl(`documents?scope=org&key=${enc(docId)}&limit=1`).then((r) => setFound(r.documents[0] || null)).catch(() => setFound(null));
  }, [docId, Boolean(known)]);
  const current = known || (found && found.id === docId ? found : null);
  const home = current ? (current.workspaces || [])[0] || "" : "";
  useEffect(() => { if (home && !open.has(home)) setOpen((o) => new Set([...o, home])); }, [home]);
  const toggle = (x) => setOpen((o) => { const n = new Set(o); if (n.has(x)) n.delete(x); else n.add(x); return n; });
  const fold = (key, on) => setFolds((f) => ({ ...f, [key]: on }));
  useEffect(() => { if (current) setFolds((f) => ({ ...f, ...Object.fromEntries(foldsTo(current).map((k) => [`${home}:${k}`, true])) })); }, [current && current.id]);

  const side = h("aside", { className: "space-nav" },
    h("div", { className: "space-nav-head" },
      h("span", { className: "space-nav-label" }, h(Icon, { name: "docs", size: 14 }), "Documentation"),
      h("input", { className: "input", type: "search", value: filter, onChange: (e) => setFilter(e.target.value),
                   placeholder: "Search pages…", "aria-label": "Search every space's pages by title" })),
    h("nav", { className: "space-tree", "aria-label": "Your spaces" }, spaces.length ? spaces.map((x) => {
      const unfolded = open.has(x) || Boolean(term);
      const list = docs[x];
      const shown = list && term ? list.filter((d) => d.title.toLowerCase().includes(term)) : list;
      return h("div", { key: x, className: "space-group" },
        h("button", { type: "button", className: `space-title${unfolded ? " on" : ""}`, "aria-expanded": unfolded, onClick: () => toggle(x) },
          h("span", { className: "space-chevron", "aria-hidden": true }, h(Icon, { name: "chevron", size: 12 })),
          h("span", { className: "space-dot", "aria-hidden": true }), label(x), h("small", null, list ? String(list.length) : "")),
        unfolded ? h("nav", { className: "space-pages", "aria-label": `Documents in ${label(x)}` },
          !list ? h(SkeletonRows, { rows: 4, widths: [70, 50, 60] })
            : shown.length ? spaceTree(shown).map((g) => h(Branch, { key: g.name, node: g, depth: 0, path: `${x}:g:${g.name}`, selected: docId, team: "", expand: Boolean(term), folds, onFold: fold }))
              : h("p", { className: "muted small", style: { padding: 8 } }, list.length ? "No page's title matches." : "Nothing here yet.")) : null);
    }) : h("p", { className: "muted small", style: { padding: 8 } }, "You aren't in a team yet, so there's no space to show.")));

  const start = h("div", { className: "space-home" },
    h("div", { className: "page-head" }, h("div", null, h("h1", null, "Documentation"),
      h("p", { className: "lede" }, spaces.length ? `${plural(spaces.length, "space")} you're in. Open one on the left to see its pages.` : "Join a team to see its space."))),
    error ? h(Alert, { onRetry: () => { setError(null); setRecent(null); kl("documents?scope=org&limit=12").then((r) => setRecent(r.documents)).catch((e) => setError(e.message)); } }, error) : null,
    recent === null ? (error ? null : h("section", { className: "panel" }, h(SkeletonRows, { rows: 6, widths: [50, 20, 14, 10] })))
      : !recent.length ? h("section", { className: "panel" }, h(Empty, { title: "Nothing to read yet", icon: "docs" },
          "Documents arrive from the sources each team's admins connect."))
        : h("section", { className: "panel", "aria-labelledby": "docs-recent-h" },
            h("div", { className: "panel-head" }, h("h2", { id: "docs-recent-h" }, "Recently added"), h("span", { className: "faint small" }, "Across your spaces")),
            h(Table, null,
              h("thead", null, h("tr", null, h("th", null, "Title"), h("th", { className: "hide-sm" }, "Space"), h("th", null, "Tier"), h("th", { className: "num" }, "Added"))),
              h("tbody", null, recent.map((d) => h("tr", { key: d.id },
                h("td", { className: "ellipsis" }, h("a", { className: "title-link", href: docAt("", d.id) }, d.title)),
                h("td", { className: "hide-sm muted ellipsis" }, (d.workspaces || []).map(label).join(", ")),
                h("td", null, h(Tier, { tier: tierOf(d.tier) })),
                h("td", { className: "num muted" }, ago(d.created_at))))))));

  return h("div", { className: "space-shell" }, side,
    h("div", { className: "space-main" },
      docId ? h(Reader, { id: docId, team: home, doc: current, spaceName: home ? label(home) : "" }) : start));
}
