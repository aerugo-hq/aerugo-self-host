"use client";
import { inScope } from "./scope.js";
// Ask (slice R1): conversations, each answer beside the knowledge behind it. The thread in the middle; your history to
// one side; on the other, the answer's sources and the graph around them, where any node can start the next question.
// Every answer is the server's re-checked view: one whose sources you can no longer open arrives hidden.
import { createElement as h, useEffect, useRef, useState } from "react";
import { ago, kl } from "../lib/client.js";
import { Icon } from "./icons.js";
import { Alert, Confirm, Empty, SkeletonRows, Spinner, TIERS, Tier, concept, tierOf } from "./ui.js";

const enc = encodeURIComponent;
const MAX_NODES = 24; // beyond this a graph stops being readable; the rest is one click away on the map

// Today, Yesterday, This week, Earlier: how a history reads at a glance.
export function groupByDay(conversations, now = Date.now()) {
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  const t0 = start.getTime(), day = 864e5;
  const groups = [["Today", []], ["Yesterday", []], ["This week", []], ["Earlier", []]];
  for (const c of conversations) {
    const at = Date.parse(c.updated_at);
    groups[at >= t0 ? 0 : at >= t0 - day ? 1 : at >= t0 - 6 * day ? 2 : 3][1].push(c);
  }
  return groups.filter(([, list]) => list.length);
}

// The answer's sources, then their best-connected neighbors, up to MAX_NODES.
export function trim(graph, focus, max = MAX_NODES) {
  const degree = {};
  for (const e of graph.edges) for (const id of [e.source, e.target]) degree[id] = (degree[id] || 0) + 1;
  const seeds = graph.nodes.filter((n) => focus.includes(n.id));
  const rest = graph.nodes.filter((n) => !focus.includes(n.id)).sort((a, b) => (degree[b.id] || 0) - (degree[a.id] || 0));
  const nodes = [...seeds, ...rest].slice(0, max);
  const keep = new Set(nodes.map((n) => n.id));
  return { nodes, edges: graph.edges.filter((e) => keep.has(e.source) && keep.has(e.target)) };
}

// Rings: the answer's sources in the middle, everything one step away around them.
export function layout(graph, focus, size = 320) {
  const c = size / 2;
  const ring = (list, r, offset) => list.map((n, i) => {
    const a = offset + (2 * Math.PI * i) / Math.max(list.length, 1);
    return { ...n, x: Math.round(c + r * Math.cos(a)), y: Math.round(c + r * Math.sin(a)) };
  });
  const seeds = graph.nodes.filter((n) => focus.includes(n.id));
  const rest = graph.nodes.filter((n) => !focus.includes(n.id));
  const nodes = [...ring(seeds, seeds.length > 1 ? size * 0.17 : 0, -Math.PI / 2), ...ring(rest, size * 0.37, -Math.PI / 2 + 0.3)];
  const at = Object.fromEntries(nodes.map((n) => [n.id, n]));
  return { nodes, edges: graph.edges.filter((e) => at[e.source] && at[e.target]).map((e) => ({ ...e, a: at[e.source], b: at[e.target] })) };
}

// How Ask may search (slice R1): every channel, words only, meaning only, or curated knowledge only.
export const MODES = [["smart", "Smart"], ["keyword", "Keywords"], ["meaning", "Meaning"], ["curated", "Curated only"]];

// The @ being typed just before the caret, if any: "who owns @ledg" gives { query: "ledg", start: 9 }.
export function mentionAt(text, caret) {
  const m = text.slice(0, caret).match(/(^|\s)@([^\s@]{0,40})$/);
  return m ? { query: m[2], start: caret - m[2].length - 1 } : null;
}

const short = (t) => (t.length > 22 ? `${t.slice(0, 21)}…` : t);
const lastAnswer = (messages) => {
  for (let i = messages.length - 1; i >= 0; i--) if (messages[i].role === "assistant") return i;
  return null;
};

// Citation markers in an answer, set as superscript numbers in the accent.
function withCitations(text) {
  return text.split(/\[(\d{1,2})\]/).map((part, i) => (i % 2 ? h("sup", { key: i, className: "cite-n" }, part) : part));
}

function History({ items, current, query, setQuery, onSearch, link = (path) => path }) {
  const list = items === null ? h(SkeletonRows, { rows: 4, widths: [70, 50, 60] })
    : items.length ? groupByDay(items).map(([label, group]) => h("section", { key: label },
        h("h3", null, label),
        h("ul", null, group.map((c) => h("li", { key: c.id }, h("a", { href: link(`#/ask/${c.id}`), "aria-current": c.id === current ? "page" : undefined },
          h("span", null, c.title), h("small", null, ago(c.updated_at))))))))
      : h(Empty, null, query ? "No conversation matches." : "No conversations yet. Your first question starts one.");
  return h("aside", { className: "ask-history", "aria-label": "Your conversations" },
    h("a", { className: "btn", href: link("#/ask") }, h(Icon, { name: "plus", size: 14 }), "New conversation"),
    h("form", { role: "search", onSubmit: (e) => { e.preventDefault(); onSearch(); } },
      h("input", { className: "input", type: "search", value: query, onChange: (e) => setQuery(e.target.value),
                   placeholder: "Search your questions", "aria-label": "Search your conversations" })),
    list);
}

function AnswerGraph({ ids, selected, onSelect }) {
  const [graph, setGraph] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    setGraph(null);
    setError(null);
    if (!ids.length) return;
    kl(`graph?focus=${enc(ids.join(","))}&min_mentions=1`).then(setGraph).catch((e) => setError(e.message));
  }, [ids.join(",")]);
  if (!ids.length) return h(Empty, null, "This answer has no sources to map.");
  if (error) return h(Alert, null, error);
  if (!graph) return h(SkeletonRows, { rows: 3, widths: [60, 40, 50] });
  const { nodes, edges } = layout(trim(graph, ids), ids);
  const more = graph.nodes.length - nodes.length;
  return h("div", { className: "answer-graph" },
    h("svg", { viewBox: "0 0 320 320", role: "group", "aria-label": `${nodes.length} pieces of knowledge this answer used or touches` },
      edges.map((e, i) => h("line", { key: i, x1: e.a.x, y1: e.a.y, x2: e.b.x, y2: e.b.y,
                                      className: selected && (e.source === selected || e.target === selected) ? "on" : "" })),
      nodes.map((n) => {
        const seed = ids.includes(n.id);
        const pick = () => onSelect(n);
        return h("g", { key: n.id, role: "button", tabIndex: 0, "aria-pressed": selected === n.id,
                        "aria-label": `${n.title}: ${n.tier === "entity" ? n.kind || "entity" : TIERS[tierOf(n.tier)].label}${seed ? ", a source of this answer" : ""}`,
                        className: `node ${tierOf(n.tier)}${seed ? " seed" : ""}${selected === n.id ? " on" : ""}`, onClick: pick,
                        onKeyDown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } } },
          h("circle", { cx: n.x, cy: n.y, r: seed ? 9 : 6 }),
          h("text", { x: n.x, y: n.y + (seed ? 22 : 17), textAnchor: "middle" }, short(n.title)));
      })),
    more > 0 ? h("p", { className: "muted small" }, `${more} more on the map`) : null);
}

function NodePanel({ node, onAsk }) {
  if (!node) return h("p", { className: "muted small node-hint" }, "Select a node to see what it is and ask about it.");
  const entity = node.tier === "entity";
  return h("div", { className: "node-panel" },
    h("div", { className: "node-panel-head" }, h("b", null, node.title),
      entity ? h("span", { className: "tag" }, node.kind || "Entity") : h(Tier, { tier: node.tier })),
    h("p", { className: "muted small" }, entity
      ? `Named in ${node.mentions} document${node.mentions === 1 ? "" : "s"} you can read.`
      : `In ${(node.workspaces || []).join(", ") || "your teams"}.`),
    h("div", { className: "actions" },
      h("button", { type: "button", className: "btn btn-sm", onClick: () => onAsk(node) }, "Ask about this"),
      h("a", { className: "btn btn-ghost btn-sm", href: entity ? "#/map" : concept(node.id) }, entity ? "Open on the map" : "Open",
        h(Icon, { name: "external", size: 12 }))));
}

function Sources({ answer }) {
  if (!answer.citations.length) return h(Empty, null, "This answer cites no sources.");
  const order = [...answer.citations].sort((a, b) => Number(b.cited) - Number(a.cited) || a.n - b.n);
  return h("ol", { className: "ask-sources" }, order.map((c) => h("li", { key: c.id },
    h("span", { className: "cite-n" }, c.n),
    h("div", null, h("a", { className: "a", href: concept(c.id) }, c.title),
      h("small", { className: "muted" }, c.cited ? (c.section || "Cited") : "Read, not cited")))));
}

export default function AskView({ me, id: route, scope: workingIn = "org" }) {
  const here = (path) => inScope(path, workingIn === "org" ? "" : workingIn); // links stay in the scope you're in (slice N1)
  // "focus:<key>" starts a new conversation focused on that document (Documents' "Ask about this").
  const id = route && !route.startsWith("focus:") ? route : "";
  const preset = route && route.startsWith("focus:") ? route.slice(6) : "";
  const [history, setHistory] = useState(null);
  const [query, setQuery] = useState("");
  const [thread, setThread] = useState(null);   // the conversation and its messages; "loading" while it arrives
  const [pick, setPick] = useState(null);       // which answer's knowledge the side pane shows
  const [node, setNode] = useState(null);
  const [pane, setPane] = useState("graph");
  const [models, setModels] = useState(null);
  const [model, setModel] = useState("");
  const [scope, setScope] = useState(workingIn);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [focus, setFocus] = useState([]);          // documents named with @: answers come from these only
  const [mode, setMode] = useState("smart");
  const [mention, setMention] = useState(null);    // the @ being typed, while it's being typed
  const [suggestions, setSuggestions] = useState([]);
  const [active, setActive] = useState(0);
  const box = useRef(null);
  const lookup = useRef(null);
  const loadHistory = (q = query) => kl(`conversations${q.trim().length >= 2 ? `?q=${enc(q.trim())}` : ""}`)
    .then((r) => setHistory(r.conversations)).catch((e) => setError(e.message));
  useEffect(() => { loadHistory(""); }, []);
  useEffect(() => { // the models you can ask with: the ones registered in your own teams
    if (!me) return;
    Promise.all(me.workspaces.map((ws) => kl(`workspaces/${enc(ws)}/models`)
      .then((r) => (Array.isArray(r) ? r : r.models || []).map((m) => `${ws}/${m.name}`)).catch(() => [])))
      .then((lists) => { const all = lists.flat(); setModels(all); setModel((m) => m || all[0] || ""); });
  }, [me]);
  useEffect(() => {
    setNode(null);
    setError(null);
    if (!id) { setThread(null); setPick(null); return; }
    setThread("loading");
    kl(`conversations/${enc(id)}`).then((t) => { setThread(t); setPick(lastAnswer(t.messages)); }).catch((e) => { setThread(null); setError(e.message); });
  }, [id]);
  useEffect(() => { // the title comes through the documents list, so a key you can't open names nothing
    if (!preset) return;
    kl(`documents?key=${enc(preset)}`).then((r) => { const d = r.documents[0]; if (d) setFocus([{ id: d.id, title: d.title }]); }).catch(() => {});
  }, [preset]);
  useEffect(() => { setScope(workingIn); }, [workingIn]); // the team switcher sets where you start asking

  async function send(e) {
    e.preventDefault();
    const text = question.trim();
    if (text.length < 3 || !model || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (!id) {
        const r = await kl("conversations", { method: "POST", body: { question: text, model, scope, mode, ...named() } });
        setQuestion("");
        window.location.hash = here(`#/ask/${r.conversation.id}`); // the address loads it, in this scope
      } else {
        await kl(`conversations/${enc(id)}/messages`, { method: "POST", body: { question: text, model, scope, mode, ...named() } });
        setQuestion("");
        const t = await kl(`conversations/${enc(id)}`);
        setThread(t);
        setPick(lastAnswer(t.messages));
        setNode(null);
      }
      loadHistory();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  const named = () => (focus.length ? { focus: focus.map((f) => f.id) } : {});
  function typed(e) {
    const value = e.target.value;
    setQuestion(value);
    const m = mentionAt(value, e.target.selectionStart ?? value.length);
    setMention(m);
    setActive(0);
    clearTimeout(lookup.current);
    if (!m || m.query.length < 2) { setSuggestions([]); return; }
    lookup.current = setTimeout(() => kl(`search?q=${enc(m.query)}&scope=org&limit=8`)  // only what you can open
      .then((r) => setSuggestions(r.results.filter((x) => !focus.some((f) => f.id === x.id)).slice(0, 6).map((x) => ({ id: x.id, title: x.title }))))
      .catch(() => setSuggestions([])), 200);
  }
  function choose(s) {
    if (!mention) return;
    setFocus((f) => (f.some((x) => x.id === s.id) || f.length >= 5 ? f : [...f, s]));
    setQuestion((q) => q.slice(0, mention.start) + q.slice(mention.start + 1 + mention.query.length));
    setMention(null);
    setSuggestions([]);
    if (box.current) box.current.focus();
  }
  function keys(e) {
    if (mention && suggestions.length) {
      if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => (a + 1) % suggestions.length); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => (a - 1 + suggestions.length) % suggestions.length); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); choose(suggestions[active]); return; }
    }
    if (mention && e.key === "Escape") { e.preventDefault(); setMention(null); setSuggestions([]); return; }
    if (e.key === "Enter" && !e.shiftKey) send(e);
  }
  function askAbout(n) {
    setQuestion(`Tell me more about ${n.title}`);
    if (box.current) box.current.focus();
  }
  async function remove() {
    try {
      await kl(`conversations/${enc(id)}`, { method: "DELETE" });
      setConfirming(false);
      window.location.hash = here("#/ask");
      loadHistory();
    } catch (err) {
      setConfirming(false);
      setError(err.message);
    }
  }

  const loaded = thread && thread !== "loading" ? thread : null;
  const answer = loaded && pick !== null ? loaded.messages[pick] : null;
  const ids = answer && !answer.hidden ? answer.citations.map((c) => c.id) : [];
  const noModels = models !== null && !models.length;
  const ready = question.trim().length >= 3 && model && !busy;

  const thread_ = h("section", { className: "ask-thread", "aria-label": "Conversation" },
    h("div", { className: "ask-head" },
      h("h1", null, loaded ? loaded.conversation.title : id ? "Conversation" : "New conversation"),
      loaded ? h("button", { type: "button", className: "btn btn-ghost btn-sm", onClick: () => setConfirming(true) }, "Delete") : null),
    error ? h(Alert, { onRetry: id ? () => window.dispatchEvent(new HashChangeEvent("hashchange")) : null }, error) : null,
    thread === "loading" ? h(SkeletonRows, { rows: 4, widths: [50, 80, 70] })
      : loaded ? h("div", { className: "ask-messages" }, loaded.messages.map((m, i) => m.role === "user"
          ? h("div", { key: m.id, className: "msg msg-user" }, m.content)
          : h("article", { key: m.id, className: `msg msg-answer${i === pick ? " on" : ""}` },
              m.hidden ? h("p", { className: "muted" }, m.content) : h("div", { className: "answer-text" }, withCitations(m.content)),
              h("div", { className: "msg-meta" },
                h("span", { className: "muted small" }, m.model),
                m.hidden ? null : h("button", { type: "button", className: "btn btn-ghost btn-sm", "aria-pressed": i === pick,
                                                onClick: () => { setPick(i); setNode(null); } }, "Show its knowledge")))))
        : h("p", { className: "ask-intro" }, "Ask about anything your teams know. Each answer cites its sources, and only what you can open."),
    noModels ? h(Alert, { tone: "warn", title: "No model yet." }, "A team admin adds one under Admin, then Models.") : null,
    h("form", { className: "composer", onSubmit: send },
      focus.length ? h("div", { className: "chips", role: "group", "aria-label": "Answering from" }, focus.map((f) => h("span", { key: f.id, className: "chip" },
        h("span", null, `@ ${f.title}`),
        h("button", { type: "button", "aria-label": `Stop focusing on ${f.title}`, onClick: () => setFocus((x) => x.filter((y) => y.id !== f.id)) },
          h(Icon, { name: "x", size: 12 }))))) : null,
      h("textarea", { ref: box, rows: 2, value: question, onChange: typed, onKeyDown: keys, "aria-label": "Question",
                      "aria-autocomplete": "list", "aria-controls": "mention-list", "aria-expanded": Boolean(mention && suggestions.length),
                      "aria-activedescendant": mention && suggestions.length ? `mention-${active}` : undefined,
                      placeholder: loaded ? "Ask a follow-up, or type @ to focus on a document" : "Ask a question, or type @ to focus on a document" }),
      mention ? (suggestions.length
        ? h("ul", { id: "mention-list", className: "mention-list", role: "listbox", "aria-label": "Documents to focus on" },
            suggestions.map((s, i) => h("li", { key: s.id, id: `mention-${i}`, role: "option", "aria-selected": i === active,
                                                  onMouseDown: (e) => { e.preventDefault(); choose(s); } }, s.title)))
        : h("p", { id: "mention-list", className: "mention-hint muted small" },
            mention.query.length < 2 ? "Type a document's name to focus on it." : "No document you can open matches that.")) : null,
      h("div", { className: "composer-bar" },
        h("select", { className: "select", value: scope, onChange: (e) => setScope(e.target.value), "aria-label": "Look in" },
          h("option", { value: "org" }, "All my teams"), ...(me ? me.workspaces : []).map((ws) => h("option", { key: ws, value: ws }, ws))),
        h("select", { className: "select", value: mode, onChange: (e) => setMode(e.target.value), "aria-label": "Answer from" },
          MODES.map(([value, label]) => h("option", { key: value, value }, label))),
        h("select", { className: "select", value: model, onChange: (e) => setModel(e.target.value), "aria-label": "Model", disabled: !models || noModels },
          (models || []).map((m) => h("option", { key: m, value: m }, m))),
        h("span", { className: "spacer" }),
        h("button", { type: "submit", className: "btn btn-primary", disabled: !ready }, busy ? h(Spinner) : h(Icon, { name: "ask", size: 14 }), "Ask"))));

  const knowledge = h("aside", { className: "ask-knowledge", "aria-label": "Knowledge behind the answer" },
    h("div", { className: "tabs", role: "tablist" },
      ["sources", "graph"].map((p) => h("button", { key: p, type: "button", role: "tab", "aria-selected": pane === p, onClick: () => setPane(p) },
        p === "sources" ? "Sources" : "Graph"))),
    !answer ? h(Empty, null, "Ask a question to see the knowledge behind its answer.")
      : answer.hidden ? h(Empty, null, "This answer is hidden, so its sources are too.")
        : pane === "sources" ? h(Sources, { answer })
          : h("div", null, h(AnswerGraph, { ids, selected: node && node.id, onSelect: setNode }), h(NodePanel, { node, onAsk: askAbout })));

  return h("div", { className: "ask" },
    h(History, { items: history, current: id, query, setQuery, onSearch: () => loadHistory(), link: here }),
    thread_,
    knowledge,
    confirming ? h(Confirm, { title: "Delete this conversation?", action: "Delete conversation", onCancel: () => setConfirming(false), onConfirm: remove },
      "Its questions and answers go for good. The documents it drew on aren't affected.") : null);
}
