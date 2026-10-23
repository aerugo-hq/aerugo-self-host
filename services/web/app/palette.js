"use client";
// The command palette (⌘K or Ctrl K): every screen, section, and common action, a few letters away. It lists only what
// you can use (admin pages to admins, compliance to org admins), and anything you type can also become a search.
import { createElement as h, useEffect, useMemo, useRef, useState } from "react";

export const SHORTCUTS = [
  ["⌘K or Ctrl K", "Open the command palette"],
  ["/", "Search"],
  ["G then H", "Go home"],
  ["G then M", "Go to the map"],
  ["G then S", "Go to search"],
  ["G then Q", "Go to ask"],
  ["G then D", "Go to documents"],
  ["G then P", "Go to proposals"],
  ["G then A", "Go to admin"],
  ["G then C", "Go to compliance"],
  ["G then V", "Go to the developer page"],
  ["G then L", "Go to connected accounts"],
  ["?", "Show these shortcuts"],
  ["Esc", "Close the palette or a dialog"],
];

// Every command: the pages, then a way into each of your teams' scopes (slice N1).
export function commands(me) {
  const teams = (me ? me.workspaces : []).map((ws) => ({ id: `team:${ws}`, label: `Team: ${ws}`, hint: "Work in this team's scope", hash: `#/t/${encodeURIComponent(ws)}/home` }));
  return [...pages(me), ...teams];
}

function pages(me) {
  const admin = Boolean(me && (me.admin_of.length || me.org_admin));
  const go = (label, hash, hint, keys) => ({ id: hash, label, hint, keys, hash });
  return [
    go("Home", "#/home", "Your teams, and what needs you", "G H"),
    go("Map", "#/map", "How your teams' knowledge connects", "G M"),
    go("Ask", "#/ask", "Ask a question, and follow up on the answer", "G Q"),
    go("Search", "#/search", "Find a document by keyword or by meaning", "/"),
    go("Documents", "#/documents", "Browse everything you can open", "G D"),
    go("Proposals", "#/proposals", "Draft and review curated knowledge", "G P"),
    go("Handovers", "#/handovers", "Hand over what you know, or review what was handed to you"),
    go("Developer", "#/developer", "The API, and connecting AI assistants", "G V"),
    ...(admin ? [
      go("Alerts", "#/alerts", "What needs attention now"),
      go("Import a Confluence or Notion export", "#/admin/sources", "Upload a zip into one of your teams"),
      go("Admin: Sources", "#/admin/sources", "Connections and imports", "G A"),
      go("Admin: Pipeline", "#/admin/pipeline", "Every sync, import, and push, and what failed"),
      go("Admin: People", "#/admin/people", "Members and their roles"),
      go("Admin: Models", "#/admin/models", "Models your team can ask, and budgets"),
      go("Admin: Costs", "#/admin/costs", "What your models used, against their budgets"),
      go("Admin: Review", "#/admin/review", "Personal data waiting for a decision"),
      go("Admin: Activity", "#/admin/activity", "The audit trail"),
    ] : []),
    ...(me && me.org_admin ? [
      go("Compliance: Retention", "#/compliance/retention", "How long records are kept", "G C"),
      go("Compliance: Legal holds", "#/compliance/holds", "Keep records past their retention"),
      go("Compliance: People's data", "#/compliance/people", "Export or erase what the platform holds about a person"),
      go("Compliance: Access review", "#/compliance/access", "Who can see what, for auditors"),
      go("Compliance: Platform", "#/compliance/platform", "Rate limits, the reranker, and Google sign-in"),
    ] : []),
    go("Connected accounts", "#/accounts", "Link GitHub or Google", "G L"),
    { id: "shortcuts", label: "Keyboard shortcuts", hint: "Every key the app answers to", keys: "?", help: true },
    { id: "signout", label: "Sign out", hint: "End this session", href: "/auth/logout" },
  ];
}

// Letters in order (so "adpe" finds "Admin: People"), scored by how early and how contiguous the match is.
export function score(label, query) {
  const text = label.toLowerCase();
  const q = query.toLowerCase().trim();
  if (!q) return 1;
  if (text.startsWith(q)) return 1000 - text.length;
  const word = text.split(/[^a-z']+/).findIndex((w) => w.startsWith(q));
  if (word >= 0) return 800 - word;
  if (text.includes(q)) return 600 - text.indexOf(q);
  let at = 0, gaps = 0;
  for (const ch of q.replace(/\s+/g, "")) {
    const found = text.indexOf(ch, at);
    if (found < 0) return 0;
    gaps += found - at;
    at = found + 1;
  }
  return Math.max(1, 400 - gaps);
}

export function matches(list, query) {
  const scored = list.map((c, i) => ({ c, i, s: score(c.label, query) })).filter((x) => x.s > 0);
  scored.sort((a, b) => b.s - a.s || a.i - b.i);
  const found = scored.map((x) => x.c);
  const q = query.trim();
  return q.length >= 2 ? [...found, { id: "search", label: `Search for “${q}”`, hint: "Search everything you can access", hash: `#/search/${encodeURIComponent(q)}` }] : found;
}

export default function Palette({ me, mode = "commands", onMode, onClose }) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef(null);
  const list = useMemo(() => matches(commands(me), query), [me, query]);
  useEffect(() => {
    const before = document.activeElement;
    if (input.current) input.current.focus();
    return () => { if (before && before.focus) before.focus(); };
  }, []);
  useEffect(() => setActive(0), [query]);
  function choose(c) {
    if (!c) return;
    if (c.help) return onMode("help");
    onClose();
    if (c.href) window.location.assign(c.href);
    else window.location.hash = c.hash;
  }
  function keys(e) {
    if (e.key === "Escape") { e.preventDefault(); onClose(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => (list.length ? (a + 1) % list.length : 0)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => (list.length ? (a - 1 + list.length) % list.length : 0)); }
    else if (e.key === "Enter") { e.preventDefault(); choose(list[active]); }
    else if (e.key === "Tab") e.preventDefault(); // focus stays in the palette
  }
  const dismiss = (e) => { if (e.target === e.currentTarget) onClose(); };
  if (mode === "help") {
    return h("div", { className: "overlay", onMouseDown: dismiss },
      h("div", { className: "palette", role: "dialog", "aria-modal": "true", "aria-label": "Keyboard shortcuts", tabIndex: -1,
                 onKeyDown: (e) => { if (e.key === "Escape") { e.preventDefault(); onClose(); } }, ref: (el) => el && el.focus() },
        h("div", { className: "palette-head" }, h("h2", null, "Keyboard shortcuts")),
        h("dl", { className: "shortcuts" }, SHORTCUTS.flatMap(([k, what]) => [h("dt", { key: `k${k}` }, h("kbd", null, k)), h("dd", { key: `d${k}` }, what)])),
        h("div", { className: "palette-foot" }, h("span", null, h("kbd", null, "Esc"), " to close"))));
  }
  const option = (c, i) => h("li", { key: c.id, id: `cmd-${i}`, role: "option", "aria-selected": i === active,
                                      onMouseMove: () => setActive(i), onMouseDown: (e) => { e.preventDefault(); choose(c); } },
    h("span", { className: "label" }, c.label), h("span", { className: "hint" }, c.hint), c.keys ? h("kbd", null, c.keys) : null);
  return h("div", { className: "overlay", onMouseDown: dismiss },
    h("div", { className: "palette", role: "dialog", "aria-modal": "true", "aria-label": "Command palette" },
      h("div", { className: "palette-input" },
        h("input", { ref: input, value: query, onChange: (e) => setQuery(e.target.value), onKeyDown: keys,
                     placeholder: "Go to a page, run an action, or search", "aria-label": "Command", role: "combobox",
                     "aria-expanded": true, "aria-controls": "palette-list", "aria-autocomplete": "list",
                     "aria-activedescendant": list.length ? `cmd-${active}` : undefined, autoComplete: "off", spellCheck: false })),
      list.length ? h("ul", { className: "palette-list", id: "palette-list", role: "listbox", "aria-label": "Commands" }, list.map(option))
        : h("p", { className: "palette-empty" }, "No command matches. Type two letters or more to search instead."),
      h("div", { className: "palette-foot" },
        h("span", null, h("kbd", null, "↑"), h("kbd", null, "↓"), " to move"), h("span", null, h("kbd", null, "Enter"), " to open"),
        h("span", null, h("kbd", null, "?"), " for shortcuts"))));
}
