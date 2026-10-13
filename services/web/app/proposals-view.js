"use client";
// Proposals: drafts the team's own model wrote from evidence, reviewed here, published as pull requests.
import { createElement as h, useEffect, useRef, useState } from "react";
import { ago, kl } from "../lib/client.js";
import { Icon } from "./icons.js";
import { Alert, Crumbs, Empty, FRONT, PageHead, SkeletonRows, Spinner, Status, TeamPicker, concept, stamp, tierOf } from "./ui.js";
import { inScope } from "./scope.js";

export const STATUS = { drafted: ["Draft", "idle"], pr_opened: ["PR open", "warn"], merged: ["Merged", "ok"],
                        closed: ["Closed", "idle"], refused: ["Refused", "bad"] };
const enc = encodeURIComponent;
const ProposalStatus = ({ status }) => h(Status, { tone: (STATUS[status] || [status, "idle"])[1] }, (STATUS[status] || [status])[0]);

const body = (okf) => (okf || "").replace(FRONT, "").trim();

// Errors from the API can carry a message and a list of problems (a draft that failed knowledge CI).
function explain(message) {
  try {
    const detail = JSON.parse(message);
    return { title: detail.message || "Request refused.", problems: detail.problems || [] };
  } catch {
    return { title: message, problems: [] };
  }
}

// In a team's scope (slice N1), the team is the address's: its proposals only, with no picker, and every link stays there.
export default function ProposalsView({ me, id, team = "" }) {
  return id ? h(Proposal, { me, id, team }) : h(ProposalList, { me, team });
}

function ProposalList({ me, team: fixed = "" }) {
  const teams = me ? me.workspaces : [];
  const topicInput = useRef(null);
  const [chosen, setWs] = useState("");
  const ws = fixed || chosen;
  const [items, setItems] = useState(null);
  const [models, setModels] = useState([]);
  const [topic, setTopic] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const isAdmin = Boolean(me && ws && me.admin_of.includes(ws));

  useEffect(() => {
    // Open on a team the person can act in, if there is one.
    if (!fixed && !chosen && teams.length) setWs(teams.find((t) => me.admin_of.includes(t)) || teams[0]);
  }, [teams.length, fixed]);
  const load = () => {
    setItems(null);
    setError(null);
    kl(`workspaces/${enc(ws)}/proposals`).then((r) => setItems(r.proposals)).catch((e) => setError({ title: e.message, problems: [], load: true }));
  };
  useEffect(() => {
    if (!ws) return;
    load();
    kl(`workspaces/${enc(ws)}/models`)
      .then((r) => {
        const refs = r.models.map((m) => `${ws}/${m.name}`);
        setModels(refs);
        setModel(refs[0] || "");
      })
      .catch(() => setModels([]));
  }, [ws]);

  async function draft(event) {
    event.preventDefault();
    if (topic.trim().length < 3 || !model) return;
    setBusy(true);
    setError(null);
    try {
      const out = await kl(`workspaces/${enc(ws)}/compile`, { method: "POST", body: { topic, model } });
      window.location.hash = inScope(`#/proposals/${out.proposal}`, fixed);
    } catch (e) {
      setError(explain(e.message));
    } finally {
      setBusy(false);
    }
  }

  if (!me) return h("div", { className: "page" }, h("div", { className: "panel" }, h(SkeletonRows, { rows: 5 })));
  const open = (p) => { window.location.hash = inScope(`#/proposals/${p.id}`, fixed); };

  return h("div", { className: "page" },
    h(PageHead, { title: "Proposals", lede: "Drafts your team's own model wrote from evidence. Review one beside its sources, then open a pull request to publish it." },
      teams.length && !fixed ? h(TeamPicker, { teams, value: ws, onChange: setWs }) : null),
    error ? h(Alert, { title: error.title, onRetry: error.load ? load : undefined },
      error.problems.length ? h("pre", null, error.problems.join("\n")) : null) : null,
    h("section", { className: "panel", style: { marginTop: error ? 16 : 0 } },
      isAdmin ? h("form", { className: "panel-head inline-form wide", onSubmit: draft },
        h("input", { ref: topicInput, className: "input", value: topic, onChange: (e) => setTopic(e.target.value), "aria-label": "Topic",
                     placeholder: "What should the draft cover? For example: paging during a payments outage" }),
        models.length ? h("select", { className: "select", value: model, onChange: (e) => setModel(e.target.value), "aria-label": "Model" },
          models.map((m) => h("option", { key: m, value: m }, m))) : null,
        h("button", { className: "btn btn-primary", disabled: busy || !model || topic.trim().length < 3 }, busy ? h(Spinner) : null, "Draft")) : null,
      isAdmin && !models.length ? h(Empty, { action: h("a", { className: "btn btn-sm", href: "#/admin" }, "Register a model") },
        "Drafting needs a model registered in this team.") : null,
      items === null ? h(SkeletonRows, { rows: 4, widths: [12, 40, 14, 10] })
        : items.length ? h("div", { className: "tbl-wrap" }, h("table", { className: "tbl fixed" },
            h("thead", null, h("tr", null, h("th", { className: "w-status" }, "Status"), h("th", null, "Title"),
              h("th", { className: "hide-sm w-audience" }, "Audience"), h("th", { className: "hide-sm w-person" }, "Author"),
              h("th", { className: "num w-time" }, "Created"))),
            h("tbody", null, items.map((p) => h("tr", {
              key: p.id, className: "link", tabIndex: 0, onClick: () => open(p),
              onKeyDown: (e) => { if (e.key === "Enter") open(p); },
            }, h("td", null, h(ProposalStatus, { status: p.status })),
              h("td", { className: "ellipsis" }, h("span", { className: "title" }, p.title), " ", h("span", { className: "sub mono" }, p.concept_key)),
              h("td", { className: "hide-sm mono" }, p.audience),
              h("td", { className: "hide-sm" }, p.created_by),
              h("td", { className: "num", title: new Date(p.created_at).toLocaleString() }, ago(p.created_at)))))))
          : h(Empty, { action: isAdmin ? h("button", { type: "button", className: "btn btn-sm", onClick: () => topicInput.current && topicInput.current.focus() }, "Draft one") : null },
              isAdmin ? "No proposals in this team yet." : "No proposals you can see in this team yet.")));
}

function Proposal({ me, id, team = "" }) {
  const [p, setP] = useState(null);
  const [sources, setSources] = useState({});
  const [error, setError] = useState(null);
  const [waitFrom, setWaitFrom] = useState(null); // event count when a PR was requested

  const load = () => kl(`proposals/${enc(id)}`).then((x) => { setP(x); setError(null); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [id]);
  useEffect(() => {
    if (!p) return;
    for (const key of p.source_keys) {
      kl(`concepts/${enc(key)}`)
        .then((c) => setSources((s) => ({ ...s, [key]: c })))
        .catch(() => setSources((s) => ({ ...s, [key]: false })));
    }
  }, [p && p.id]);
  // While the worker opens the PR, re-read the proposal until a new event lands.
  useEffect(() => {
    if (waitFrom === null) return undefined;
    const timer = setInterval(load, 2500);
    return () => clearInterval(timer);
  }, [waitFrom]);
  useEffect(() => {
    if (waitFrom !== null && p && p.events.length > waitFrom) setWaitFrom(null);
  }, [p]);

  async function openPr() {
    setError(null);
    try {
      await kl(`proposals/${enc(id)}/pr`, { method: "POST" });
      setWaitFrom(p.events.length);
    } catch (e) {
      setError(e.message);
    }
  }

  const crumbs = (label) => h(Crumbs, { items: [{ href: inScope("#/proposals", team), label: "Proposals" }, { label, mono: true }] });
  if (!p) {
    return h("div", { className: "page" }, crumbs(id.slice(0, 8)),
      error ? h(Alert, { title: "This proposal isn't available.", onRetry: load }, error)
        : h("div", { className: "panel" }, h(SkeletonRows, { rows: 6, widths: [80] })));
  }
  const ws = p.model.split("/")[0];
  const canOpen = Boolean(me && me.admin_of.includes(ws)) && (p.status === "drafted" || p.status === "refused");
  const refusal = [...p.events].reverse().find((e) => e.kind === "refused");
  const events = [...p.events].reverse();

  return h("div", { className: "page" },
    crumbs(p.concept_key),
    h("header", { style: { marginBottom: 16 } },
      h("h1", null, p.title),
      h("div", { className: "meta", style: { marginTop: 8 } }, h(ProposalStatus, { status: p.status }),
        h("span", { className: "mono" }, p.concept_key), h("span", null, `audience ${p.audience}`),
        h("span", { className: "mono" }, p.model), h("span", null, `by ${p.created_by}, ${ago(p.created_at)}`),
        p.pr_url ? h("a", { className: "a", href: p.pr_url, target: "_blank", rel: "noreferrer" }, `Pull request #${p.pr_number} `, h(Icon, { name: "external", size: 12 })) : null)),
    canOpen || waitFrom !== null ? h("div", { className: "panel publish", style: { marginBottom: 16 } },
      h("div", null, h("b", null, waitFrom !== null ? "Opening a pull request…" : "Ready to publish?"),
        h("p", null, "The curator opens it only if everyone who can read the knowledge repo can already read every source.")),
      h("button", { type: "button", className: "btn btn-primary", disabled: waitFrom !== null, onClick: openPr },
        waitFrom !== null ? h(Spinner) : h(Icon, { name: "external", size: 14 }), waitFrom !== null ? "Working" : "Open pull request")) : null,
    refusal && p.status === "refused" ? h("div", { style: { marginBottom: 16 } }, h(Alert, { title: "Refused:" }, refusal.detail.reason)) : null,
    error ? h("div", { style: { marginBottom: 16 } }, h(Alert, { title: "That didn't work." }, error)) : null,
    h("div", { className: "split" },
      h("section", { className: "panel" }, h("div", { className: "panel-head" }, h("h2", null, "Draft")),
        h("div", { className: "panel-body" }, h("div", { className: "prose" }, body(p.okf)))),
      h("div", null,
        h("section", { className: "panel" }, h("div", { className: "panel-head" }, h("h2", null, "Sources"), h("span", { className: "faint mono" }, p.source_keys.length)),
          p.source_keys.map((key, i) => {
            const s = sources[key];
            return h("div", { key, className: "source" },
              h("div", { className: "source-head" }, h("span", { className: "n" }, `s${i + 1}`),
                s ? h("span", { className: `dot ${tierOf(s.tier)}` }) : null,
                s ? h("a", { className: "a", href: concept(key) }, s.title) : h("span", { className: "mono" }, key)),
              s === undefined ? h("span", { className: "skeleton", style: { width: "70%", marginTop: 8 } })
                : s === false ? h("p", { className: "faint small", style: { marginTop: 4 } }, "You can't read this source.")
                  : h("p", { className: "source-text" }, body(s.okf).slice(0, 700)));
          })),
        h("section", { className: "panel" }, h("div", { className: "panel-head" }, h("h2", null, "Timeline")),
          h("div", { className: "panel-body" }, events.length ? h("ol", { className: "timeline-list" }, events.map((e, i) => h("li", { key: i },
            h("span", { className: `dot ${e.kind === "refused" ? "bad" : e.kind === "merged" ? "ok" : "idle"}` }),
            h("div", null, h("b", null, e.kind.replaceAll("_", " ")),
              h("span", { className: "when" }, `${stamp(e.at)} · ${ago(e.at)}${e.detail && e.detail.by ? ` · ${e.detail.by}` : ""}`)))))
            : h("p", { className: "muted small" }, "Nothing yet: this draft hasn't been sent for review."))))));
}
