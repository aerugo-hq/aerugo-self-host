"use client";
import { inScope } from "./scope.js";
import { createElement as h, useEffect, useRef, useState } from "react";
import { kl } from "../lib/client.js";
import { Icon } from "./icons.js";
import { Alert, Empty, SkeletonRows, Spinner, Tier, concept } from "./ui.js";

const CHANNELS = { lexical: "keyword", vector: "meaning", title: "title", curated: "curated", canonical: "exact", graph: "related", rerank: "reranked" };

export default function SearchView({ me, initial = "", team = "" }) {
  const input = useRef(null);
  const [mode, setMode] = useState("search");
  const [q, setQ] = useState("");
  const [asked, setAsked] = useState("");
  const [results, setResults] = useState(null);
  const [models, setModels] = useState(null);
  const [model, setModel] = useState("");
  const [answer, setAnswer] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [mac, setMac] = useState(false); // read after mount, so the server and browser render the same text
  const [pending, setPending] = useState(""); // a search started elsewhere, run once the mode is search
  const [marks, setMarks] = useState({}); // feedback sent for this search or answer: result id, "_missing", or "_answer"

  useEffect(() => setMac(/Mac|iPhone|iPad/.test(navigator.platform)), []);

  // "/" from anywhere lands here with the cursor in the box.
  useEffect(() => {
    const focus = () => input.current && input.current.focus();
    window.addEventListener("kl:focus-search", focus);
    return () => window.removeEventListener("kl:focus-search", focus);
  }, []);

  // A search started elsewhere (the palette's "Search for …") arrives in the address: #/search/<words>.
  useEffect(() => {
    if (!initial) return;
    setMode("search");
    setQ(initial);
    setPending(initial);
  }, [initial]);
  useEffect(() => {
    if (pending && mode === "search") {
      run(pending);
      setPending("");
    }
  }, [pending, mode]);

  // The models you can ask with: the ones registered in your own teams.
  useEffect(() => {
    if (!me) return;
    Promise.all(me.workspaces.map((ws) => kl(`workspaces/${encodeURIComponent(ws)}/models`)
      .then((r) => (Array.isArray(r) ? r : r.models || []).map((m) => `${ws}/${m.name}`))
      .catch(() => [])))
      .then((lists) => {
        const all = lists.flat();
        setModels(all);
        setModel((current) => current || all[0] || "");
      });
  }, [me]);

  async function run(text = q.trim()) {
    if (text.length < (mode === "search" ? 2 : 3) || (mode === "ask" && !model)) return;
    setBusy(true);
    setError(null);
    setAsked(text);
    setMarks({});
    try {
      if (mode === "search") setResults(await kl(`search?q=${encodeURIComponent(text)}&scope=${team ? encodeURIComponent(team) : "org"}&limit=20`));
      else {
        setAnswer(null);
        setAnswer(await kl("ask", { method: "POST", body: { question: text, model, scope: "org" } }));
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const submit = (event) => { event.preventDefault(); run(); };
  // Feedback grows the golden set (make eval-mine). It names only documents you can see, so it's safe to send.
  async function sendFeedback(slot, body) {
    setMarks((m) => ({ ...m, [slot]: "saving" }));
    try {
      await kl("feedback", { method: "POST", body: { query: asked, ...body } });
      setMarks((m) => ({ ...m, [slot]: "saved" }));
    } catch {
      setMarks((m) => ({ ...m, [slot]: "error" }));
    }
  }
  const markRight = (r) => {
    const state = marks[r.id];
    return h("button", {
      type: "button", className: `result-mark${state === "saved" ? " done" : ""}`, disabled: state === "saving" || state === "saved",
      onClick: () => sendFeedback(r.id, { kind: "search", verdict: "right", result: r.id }),
      "aria-label": state === "saved" ? `${r.title}: marked as the right answer` : `Mark ${r.title} as the right answer`,
    }, h(Icon, { name: "check", size: 14 }), state === "saved" ? "Marked right" : state === "error" ? "Couldn't save" : "Right answer");
  };
  const switchTo = (next) => { // asking lives in Ask now (slice R1), with conversations and the knowledge behind each answer
    if (next === "ask") { window.location.hash = inScope("#/ask", team); return; }
    setMode(next); setError(null); setTimeout(() => input.current && input.current.focus(), 0);
  };

  const tab = (value, label) => h("button", { type: "button", role: "tab", "aria-selected": mode === value, onClick: () => switchTo(value) }, label);
  const withheld = answer ? answer.withheld_by_policy || 0 : 0; // how many sources this model may not receive
  const cited = answer ? (answer.citations || []).filter((c) => c.cited).map((c) => c.id) : [];
  const noModels = mode === "ask" && models !== null && !models.length;
  const isAdmin = me && (me.admin_of.length || me.org_admin);

  return h("div", { className: "page narrow" },
    h("div", { className: "page-head" }, h("div", null, h("h1", null, "Search"),
      h("p", { className: "lede" }, "Keyword and meaning search across every team, limited to what you can access. To ask a question, use Ask."))),
    h("div", { className: "tabs", role: "tablist" }, tab("search", "Search"), tab("ask", "Ask")),
    h("form", { className: "searchbox", onSubmit: submit, role: "search" },
      h(Icon, { name: mode === "search" ? "search" : "ask" }),
      h("input", { ref: input, autoFocus: true, value: q, onChange: (e) => setQ(e.target.value), "aria-label": mode === "search" ? "Search" : "Question",
                   placeholder: mode === "search" ? "Search by keyword or by meaning" : "Ask a question, like “who do I page when payments are down?”" }),
      mode === "ask" && models && models.length ? h("select", { className: "select", value: model, onChange: (e) => setModel(e.target.value), "aria-label": "Model" },
        models.map((m) => h("option", { key: m, value: m }, m))) : null,
      h("button", { className: "btn btn-primary btn-sm", disabled: busy || !q.trim() || (mode === "ask" && !model) },
        busy ? h(Spinner) : null, mode === "search" ? "Search" : "Ask")),
    !results && !answer && !error && !busy ? h("p", { className: "faint small", style: { marginTop: 8 } },
      h("kbd", null, "Enter"), " to run · ", h("kbd", null, mac ? "⌘K" : "Ctrl K"), " from anywhere") : null,

    error ? h("div", { style: { marginTop: 16 } }, h(Alert, { title: mode === "search" ? "Search failed." : "No answer.", onRetry: () => run(asked) }, error)) : null,

    noModels ? h("div", { className: "panel results" }, h(Empty, {
      action: isAdmin ? h("a", { className: "btn btn-primary btn-sm", href: "#/admin" }, "Register a model") : null,
    }, isAdmin ? "Ask needs a model registered in one of your teams." : "Ask needs a model; an admin of your team can register one.")) : null,

    mode === "search" && busy && !results ? h("div", { className: "panel results" }, h(SkeletonRows, { rows: 5, widths: [56, 18] })) : null,
    mode === "search" && results ? h("div", { className: "results" },
      h("div", { className: "results-meta" },
        h("span", null, `${results.results.length} result${results.results.length === 1 ? "" : "s"} for “${asked}”`),
        h("span", null, "Limited to sources you can access")),
      h("div", { className: "panel" }, results.results.length ? [
        ...results.results.map((r) => h("div", { key: r.id, className: "result-row" },
          h("a", { className: "result", href: concept(r.id) },
            h("div", { className: "result-title" }, h("span", null, r.title)),
            r.section ? h("div", { className: "faint small" }, r.section) : null,
            h("p", { className: "result-snippet" }, r.snippet),
            h("div", { className: "meta" }, h(Tier, { tier: r.tier }),
              h("span", { className: "mono" }, r.id),
              (r.matched || []).length ? h("span", null, `matched ${r.matched.map((m) => CHANNELS[m] || m).join(", ")}`) : null,
              r.related_to ? h("span", null, `via ${r.related_to}`) : null)),
          markRight(r))),
        h("div", { key: "_foot", className: "results-foot" }, marks._missing === "saved"
          ? h("span", { className: "faint small" }, "Thanks. Questions nothing answers show up as gaps to write.")
          : h("button", { type: "button", className: "btn btn-ghost btn-sm", disabled: marks._missing === "saving",
                          onClick: () => sendFeedback("_missing", { kind: "search", verdict: "missing" }) },
              marks._missing === "error" ? "Couldn't save. Try again" : "None of these answered it")),
      ]
        : h(Empty, { action: h("button", { type: "button", className: "btn btn-sm", onClick: () => switchTo("ask") }, "Ask instead") },
            `Nothing you can access matches “${asked}”.`))) : null,

    mode === "ask" && busy ? h("div", { className: "panel results" }, h("div", { className: "loading-line" }, h(Spinner), "Reading your sources…")) : null,
    mode === "ask" && answer ? h("div", { className: "panel results answer" },
      h("p", { className: "answer-text" }, answer.answer),
      (answer.citations || []).length ? h("div", { className: "cites" },
        h("span", { className: "faint small" }, "Sources"),
        answer.citations.map((c, i) => h("a", { key: i, className: "cite", href: concept(c.id) },
          h("span", { className: "n" }, `[${i + 1}]`), h("span", null, c.title || c.id)))) : null,
      withheld ? h("div", { style: { marginTop: 12 } }, h(Alert, { tone: "warn" },
        `${withheld} source${withheld === 1 ? " was" : "s were"} withheld: this model may not receive ${withheld === 1 ? "it" : "them"}.`)) : null,
      h("div", { className: "feedback-row" }, marks._answer === "saved"
        ? h("span", { className: "faint small" }, "Thanks for the feedback.")
        : [h("span", { key: "q", className: "faint small" }, marks._answer === "error" ? "Couldn't save. Try again:" : "Was this answer helpful?"),
           h("button", { key: "y", type: "button", className: "btn btn-sm", disabled: marks._answer === "saving",
                         onClick: () => sendFeedback("_answer", { kind: "ask", verdict: "helpful", cited }) }, "Helpful"),
           h("button", { key: "n", type: "button", className: "btn btn-sm", disabled: marks._answer === "saving",
                         onClick: () => sendFeedback("_answer", { kind: "ask", verdict: "not_helpful", cited }) }, "Not helpful")]))
      : null);
}
