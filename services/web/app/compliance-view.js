"use client";
// Compliance (slice D4): what org admins answer auditors with. Retention and legal holds, people's data, the access
// review, rate limits, the reranker, and Google sign-in for linking identities. Team admins review personal-data
// flags on the Admin page.
import { createElement as h, useEffect, useState } from "react";
import { ago, kl } from "../lib/client.js";
import { Icon } from "./icons.js";
import { Alert, Confirm, Empty, PageHead, SectionNav, SkeletonRows, Spinner, Status, Table, stamp } from "./ui.js";

// One question at a time, each with its own address (#/compliance/holds), in the order auditors usually ask.
const SECTIONS = [
  ["retention", "Retention", "How long records are kept"],
  ["holds", "Legal holds", "Keep records past their retention while a matter is open"],
  ["people", "People's data", "Export or erase what the platform holds about a person"],
  ["access", "Access review", "Who can see what, for auditors"],
  ["platform", "Platform", "Rate limits, the reranker, and Google sign-in"],
];

const enc = encodeURIComponent;
const CLASSES = ["public", "internal", "confidential", "restricted"];
const TARGETS = { documents: "Documents", concept_versions: "Concept versions", audit_events: "Audit events", model_usage: "Model usage" };
const COUNTED = { documents: "documents", concept_versions: "concept versions", audit_events: "audit events", model_usage: "model calls" };

// Hands the browser a JSON file to save; nothing is sent anywhere else.
function download(name, data) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const panel = (title, lede, actions, ...body) => h("section", { className: "panel", style: { marginTop: 24 } },
  h("div", { className: "panel-head" }, h("div", null, h("h2", null, title), h("p", null, lede)), actions), ...body);
const errorBox = (error, onRetry) => (error ? h("div", { style: { padding: 16 } }, h(Alert, { onRetry }, error)) : null);
const field = (label, input, hint) => h("label", { className: "field" }, h("span", null, label), input,
  hint ? h("span", { className: "hint" }, hint) : null);

export default function ComplianceView({ me, section }) {
  if (!me) return h("div", { className: "page" }, h("div", { className: "panel" }, h(SkeletonRows, { rows: 5 })));
  if (!me.org_admin) {
    return h("div", { className: "page" }, h(PageHead, { title: "Compliance" }),
      h("div", { className: "panel" }, h(Empty, null, "Compliance settings are for org admins.")));
  }
  const current = SECTIONS.some(([id]) => id === section) ? section : "retention";
  return h("div", { className: "page" },
    h(PageHead, { title: "Compliance", lede: "What auditors ask about: how long records are kept, who can see what, and people's own data." }),
    h(SectionNav, { base: "#/compliance", sections: SECTIONS, current, label: "Compliance sections" }),
    current === "retention" ? h(Retention, { key: "retention" }) : null,
    current === "holds" ? h(Holds, { key: "holds" }) : null,
    current === "people" ? h(DataRequests, { key: "people" }) : null,
    current === "access" ? h(AccessReview, { key: "access" }) : null,
    current === "platform" ? [h(Limits, { key: "limits" }), h(Reranker, { key: "reranker" }), h(GoogleSignIn, { key: "google" })] : null);
}

function Retention() {
  const blank = { workspace: "", target: "documents", classification: "", keep_days: "365" };
  const [data, setData] = useState(null);
  const [form, setForm] = useState(null);
  const [note, setNote] = useState(null);
  const [error, setError] = useState(null);
  const load = () => kl("retention").then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  async function act(request, done) {
    setError(null);
    try {
      await request();
      if (done) done();
      load();
    } catch (e) {
      setError(e.message);
    }
  }
  const scoped = form && (form.target === "documents" || form.target === "model_usage");
  const save = (event) => {
    event.preventDefault();
    act(() => kl("retention/policies", { method: "PUT", body: {
      workspace: scoped && form.workspace ? form.workspace : null, target: form.target,
      classification: form.target === "documents" && form.classification ? form.classification : null,
      keep_days: Number(form.keep_days) } }), () => setForm(null));
  };
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });
  const runs = data ? data.runs.slice(0, 5) : [];

  return panel("Retention", "How long each kind of record is kept. With no policy, records are kept forever.",
    h("div", { className: "actions" },
      h("button", { type: "button", className: "btn btn-sm", onClick: () => act(() => kl("retention/apply", { method: "POST" }),
        () => setNote("Retention is running; its run appears below when it finishes.")) }, h(Icon, { name: "play", size: 14 }), "Run now"),
      h("button", { type: "button", className: "btn btn-sm", onClick: () => setForm(form ? null : blank) }, form ? "Cancel" : "Add policy")),
    form ? h("form", { className: "panel-body", onSubmit: save,
      style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, borderBottom: "1px solid var(--border)" } },
      field("Records", h("select", { className: "select", value: form.target, onChange: set("target") },
        Object.entries(TARGETS).map(([value, label]) => h("option", { key: value, value }, label)))),
      field("Keep for (days)", h("input", { className: "input", type: "number", min: form.target === "audit_events" ? 30 : 1,
        value: form.keep_days, onChange: set("keep_days"), required: true }),
        form.target === "audit_events" ? "The audit trail is evidence: at least 30 days." : null),
      field("Team", h("input", { className: "input mono", value: scoped ? form.workspace : "", onChange: set("workspace"),
        disabled: !scoped, placeholder: scoped ? "Blank for the whole org" : "Kept org-wide" })),
      field("Classification", h("select", { className: "select", value: form.target === "documents" ? form.classification : "",
        onChange: set("classification"), disabled: form.target !== "documents" },
        h("option", { value: "" }, "Any"), CLASSES.map((c) => h("option", { key: c, value: c }, c)))),
      h("div", { style: { gridColumn: "1 / -1" } }, h("button", { className: "btn btn-primary" }, "Save policy"),
        h("span", { className: "hint", style: { marginLeft: 12 } }, "A document goes only when every team holding it has expired it."))) : null,
    note ? h("div", { style: { padding: "12px 16px" } }, h(Status, { tone: "ok" }, note)) : null,
    errorBox(error, load),
    data === null && !error ? h(SkeletonRows, { rows: 2, widths: [24, 16, 12] })
      : data && data.policies.length ? h(Table, null,
          h("thead", null, h("tr", null, h("th", null, "Records"), h("th", null, "Scope"), h("th", { className: "num" }, "Kept"),
            h("th", { className: "hide-sm" }, "Set by"), h("th", { className: "actions-cell" }, h("span", { className: "sr-only" }, "Actions")))),
          h("tbody", null, data.policies.map((p) => h("tr", { key: p.id },
            h("td", null, TARGETS[p.target] || p.target),
            h("td", { className: "muted" }, [p.workspace || "Whole org", p.classification].filter(Boolean).join(" · ")),
            h("td", { className: "num" }, `${p.keep_days} days`),
            h("td", { className: "hide-sm muted" }, `${p.created_by}, ${ago(p.created_at)}`),
            h("td", { className: "actions-cell" }, h("button", { type: "button", className: "btn btn-sm",
              onClick: () => act(() => kl(`retention/policies/${enc(p.id)}`, { method: "DELETE" })) }, "Remove"))))))
        : data ? h(Empty, null, "No retention policies: every record is kept.") : null,
    runs.length ? h("div", { className: "panel-body", style: { borderTop: "1px solid var(--border)" } },
      h("p", { className: "muted", style: { margin: "0 0 8px" } }, "Recent runs"),
      runs.map((r, i) => h("div", { key: i, className: "sub" }, `${stamp(r.at)}: removed `,
        Object.entries(r.counts).map(([k, n]) => `${n} ${COUNTED[k] || k}`).join(", ") || "nothing"))) : null);
}

function Holds() {
  const [data, setData] = useState(null);
  const [form, setForm] = useState(null);
  const [error, setError] = useState(null);
  const load = () => kl("retention").then((d) => { setData(d.holds); setError(null); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);
  async function act(request, done) {
    setError(null);
    try {
      await request();
      if (done) done();
      load();
    } catch (e) {
      setError(e.message);
    }
  }
  const place = (event) => {
    event.preventDefault();
    act(() => kl("legal-holds", { method: "POST", body: { workspace: form.workspace || null, reason: form.reason } }), () => setForm(null));
  };

  return panel("Legal holds", "While a hold is active, retention removes nothing it covers, and a held team can't be purged.",
    h("button", { type: "button", className: "btn btn-sm", onClick: () => setForm(form ? null : { workspace: "", reason: "" }) },
      form ? "Cancel" : "Place hold"),
    form ? h("form", { className: "panel-body", onSubmit: place,
      style: { display: "grid", gridTemplateColumns: "1fr 2fr", gap: 12, borderBottom: "1px solid var(--border)" } },
      field("Team", h("input", { className: "input mono", value: form.workspace, placeholder: "Blank for the whole org",
        onChange: (e) => setForm({ ...form, workspace: e.target.value }) })),
      field("Reason", h("input", { className: "input", value: form.reason, required: true, minLength: 3, placeholder: "Matter 2026-14: preserve all records",
        onChange: (e) => setForm({ ...form, reason: e.target.value }) })),
      h("div", { style: { gridColumn: "1 / -1" } }, h("button", { className: "btn btn-primary" }, "Place hold"))) : null,
    errorBox(error, load),
    data === null && !error ? h(SkeletonRows, { rows: 2, widths: [20, 40, 14] })
      : data && data.length ? h(Table, null,
          h("thead", null, h("tr", null, h("th", null, "Scope"), h("th", null, "Reason"), h("th", null, "Status"),
            h("th", { className: "actions-cell" }, h("span", { className: "sr-only" }, "Actions")))),
          h("tbody", null, data.map((x) => h("tr", { key: x.id },
            h("td", null, x.workspace || "Whole org"),
            h("td", { className: "ellipsis" }, h("div", { className: "title" }, x.reason), h("div", { className: "sub" }, `${x.created_by}, ${ago(x.created_at)}`)),
            h("td", null, x.released_at ? h(Status, { tone: "idle" }, `Released by ${x.released_by}`) : h(Status, { tone: "bad" }, "Active")),
            h("td", { className: "actions-cell" }, x.released_at ? null : h("button", { type: "button", className: "btn btn-sm",
              onClick: () => act(() => kl(`legal-holds/${enc(x.id)}/release`, { method: "POST" })) }, "Release"))))))
        : data ? h(Empty, null, "No legal holds.") : null);
}

function DataRequests() {
  const [name, setName] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  async function exportData() {
    setError(null);
    setResult(null);
    setBusy(true);
    try {
      download(`${name}-data.json`, await kl(`users/${enc(name)}/export`));
      setResult(`Exported everything held about ${name}, with everyone else redacted.`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function erase(event) {
    event.preventDefault();
    setError(null);
    setResult(null);
    setBusy(true);
    try {
      const r = await kl(`users/${enc(name)}/erase`, { method: "POST", body: { confirm } });
      setResult(`Erased. ${name} is now ${r.pseudonym} everywhere. Delete their sign-in account in the identity provider too.`);
      setConfirm("");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const valid = /^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$/.test(name);

  return panel("People's data", "Export what's held about a person, or erase them. Check legal holds before erasing.", null,
    h("div", { className: "panel-body", style: { display: "grid", gap: 12 } },
      field("Username", h("input", { className: "input mono", value: name, autoComplete: "off", placeholder: "alice",
        onChange: (e) => { setName(e.target.value); setConfirm(""); setResult(null); } })),
      h("div", { className: "actions" },
        h("button", { type: "button", className: "btn btn-sm", disabled: !valid || busy, onClick: exportData },
          busy ? h(Spinner, null) : null, "Export their data")),
      h("form", { onSubmit: erase, style: { display: "grid", gap: 8, borderTop: "1px solid var(--border)", paddingTop: 12 } },
        field("Type the username again to erase them", h("input", { className: "input mono", value: confirm, autoComplete: "off",
          onChange: (e) => setConfirm(e.target.value) }),
          "Removes their access and linked accounts and replaces their name everywhere with a pseudonym. It can't be undone."),
        h("div", null, h("button", { className: "btn btn-sm btn-danger", disabled: !valid || confirm !== name || busy }, "Erase permanently"))),
      result ? h(Status, { tone: "ok" }, result) : null),
    errorBox(error));
}

function AccessReview() {
  const [review, setReview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function run() {
    setError(null);
    setBusy(true);
    try {
      setReview(await kl("access-review"));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const docs = (counts) => Object.entries(counts).map(([c, n]) => `${n} ${c}`).join(", ") || "none";

  return panel("Access review", "Who can see what: each team's admins and members, groups expanded, and its documents by classification.",
    h("div", { className: "actions" },
      review ? h("button", { type: "button", className: "btn btn-sm", onClick: () => download(`access-review-${review.generated_at.slice(0, 10)}.json`, review) }, "Download") : null,
      h("button", { type: "button", className: "btn btn-sm", disabled: busy, onClick: run }, busy ? h(Spinner, null) : null, review ? "Run again" : "Run review")),
    errorBox(error, run),
    review ? h("div", null,
      h("div", { className: "panel-body sub" }, `Org admins: ${review.org_admins.map((u) => u.replace(/^user:/, "")).join(", ") || "none"}. Generated ${stamp(review.generated_at)}.`),
      h(Table, null,
        h("thead", null, h("tr", null, h("th", null, "Team"), h("th", null, "Admins"), h("th", { className: "hide-sm" }, "Members"), h("th", null, "Documents"))),
        h("tbody", null, review.teams.map((t) => h("tr", { key: t.workspace },
          h("td", null, h("div", { className: "title" }, t.name), h("div", { className: "sub mono" }, `${t.workspace} · ${t.discoverability}`)),
          h("td", { className: "muted" }, t.admins.join(", ") || "none"),
          h("td", { className: "muted hide-sm" }, t.members.join(", ") || "none",
            t.through_groups.length ? h("div", { className: "sub" }, `through ${t.through_groups.join(", ")}`) : null),
          h("td", { className: "muted" }, docs(t.documents)))))))
      : busy ? h(SkeletonRows, { rows: 3, widths: [20, 20, 30] }) : null);
}

function Limits() {
  const [limits, setLimits] = useState(null);
  const [edit, setEdit] = useState({});
  const [error, setError] = useState(null);
  const load = () => kl("limits").then((d) => { setLimits(d.limits); setEdit({}); setError(null); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);
  async function act(request) {
    setError(null);
    try {
      await request();
      load();
    } catch (e) {
      setError(e.message);
    }
  }
  const change = (route, key) => (e) => setEdit({ ...edit, [route]: { ...edit[route], [key]: e.target.value } });

  return panel("Rate limits", "Requests a minute per person and per team on each route; past them, callers get 429 with a time to retry.", null,
    errorBox(error, load),
    limits === null && !error ? h(SkeletonRows, { rows: 3, widths: [16, 16, 16] }) : limits ? h(Table, null,
      h("thead", null, h("tr", null, h("th", null, "Route"), h("th", { className: "num" }, "Per person"), h("th", { className: "num" }, "Per team"),
        h("th", { className: "actions-cell" }, h("span", { className: "sr-only" }, "Actions")))),
      h("tbody", null, limits.map((l) => {
        const draft = edit[l.route];
        return h("tr", { key: l.route },
          h("td", null, h("div", { className: "title mono" }, l.route), h("div", { className: "sub" }, l.custom ? "Custom" : "Default")),
          h("td", { className: "num" }, draft ? h("input", { className: "input", type: "number", min: 1, style: { width: 96 },
            value: draft.per_user_per_minute, onChange: change(l.route, "per_user_per_minute") }) : l.per_user_per_minute),
          h("td", { className: "num" }, draft ? h("input", { className: "input", type: "number", min: 1, style: { width: 96 },
            value: draft.per_team_per_minute, onChange: change(l.route, "per_team_per_minute") }) : l.per_team_per_minute),
          h("td", { className: "actions-cell" }, draft ? h("div", { className: "actions" },
            h("button", { type: "button", className: "btn btn-sm btn-primary", onClick: () => act(() => kl(`limits/${enc(l.route)}`, { method: "PUT", body: {
              per_user_per_minute: Number(draft.per_user_per_minute), per_team_per_minute: Number(draft.per_team_per_minute) } })) }, "Save"),
            h("button", { type: "button", className: "btn btn-sm", onClick: () => setEdit({ ...edit, [l.route]: undefined }) }, "Cancel"))
            : h("div", { className: "actions" },
              h("button", { type: "button", className: "btn btn-sm", onClick: () => setEdit({ ...edit, [l.route]: {
                per_user_per_minute: l.per_user_per_minute, per_team_per_minute: l.per_team_per_minute } }) }, "Edit"),
              l.custom ? h("button", { type: "button", className: "btn btn-sm", onClick: () => act(() => kl(`limits/${enc(l.route)}`, { method: "DELETE" })) }, "Reset") : null)));
      }))) : null);
}

function Reranker() {
  const blank = { model: "", base_url: "", api_key: "", allowed: ["public", "internal"] };
  const [current, setCurrent] = useState(undefined); // undefined: loading, null: none set
  const [form, setForm] = useState(null);
  const [error, setError] = useState(null);
  const load = () => kl("reranker").then((r) => { setCurrent(r); setError(null); })
    .catch((e) => (e.message === "no reranker is set" ? setCurrent(null) : setError(e.message)));
  useEffect(() => { load(); }, []);
  async function act(request, done) {
    setError(null);
    try {
      await request();
      if (done) done();
      load();
    } catch (e) {
      setError(e.message);
    }
  }
  const save = (event) => {
    event.preventDefault();
    act(() => kl("reranker", { method: "PUT", body: { model: form.model, base_url: form.base_url, api_key: form.api_key || null,
      allowed_classifications: form.allowed } }), () => setForm(null));
  };
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });
  const toggle = (c) => setForm({ ...form, allowed: form.allowed.includes(c) ? form.allowed.filter((x) => x !== c) : [...form.allowed, c] });

  return panel("Reranker", "Reorders search results by meaning. It reads only passages the searcher may see and its classifications allow.",
    h("div", { className: "actions" },
      current ? h("button", { type: "button", className: "btn btn-sm", onClick: () => act(() => kl("reranker", { method: "DELETE" })) }, "Remove") : null,
      h("button", { type: "button", className: "btn btn-sm", onClick: () => setForm(form ? null : current ? {
        model: current.model, base_url: current.base_url, api_key: "", allowed: current.allowed_classifications } : blank) },
        form ? "Cancel" : current ? "Change" : "Set reranker")),
    form ? h("form", { className: "panel-body", onSubmit: save,
      style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, borderBottom: "1px solid var(--border)" } },
      field("Model", h("input", { className: "input mono", value: form.model, onChange: set("model"), required: true, placeholder: "bge-reranker-v2-m3" })),
      field("Base URL", h("input", { className: "input mono", value: form.base_url, onChange: set("base_url"), required: true, placeholder: "https://rerank.example.com" })),
      h("label", { className: "field", style: { gridColumn: "1 / -1" } }, h("span", null, "API key"),
        h("input", { className: "input mono", type: "password", value: form.api_key, onChange: set("api_key"), autoComplete: "off" }),
        h("span", { className: "hint" }, current && current.has_key ? "Blank keeps the current key. Stored in OpenBao, never shown." : "Optional. Stored in OpenBao, never shown.")),
      h("fieldset", { className: "field", style: { gridColumn: "1 / -1", border: 0, padding: 0, margin: 0 } }, h("span", null, "May read"),
        h("div", { className: "actions", style: { gap: 16 } }, CLASSES.map((c) => h("label", { key: c, className: "check" },
          h("input", { type: "checkbox", checked: form.allowed.includes(c), onChange: () => toggle(c) }), c)))),
      h("div", { style: { gridColumn: "1 / -1" } }, h("button", { className: "btn btn-primary" }, "Save"))) : null,
    errorBox(error, load),
    current === undefined && !error ? h(SkeletonRows, { rows: 1, widths: [40] })
      : current ? h("div", { className: "panel-body" },
          h("div", { className: "title mono" }, `${current.model} · ${current.base_url}`),
          h("div", { className: "sub" }, `May read ${current.allowed_classifications.join(", ")}. Set by ${current.set_by}, ${ago(current.set_at)}.`))
        : current === null ? h(Empty, null, "No reranker: search uses its own fusion order.") : null);
}


// Google sign-in (slice F2b): the org's OAuth client that people sign in with to link their Google identity, so Drive
// files shared with them by name reach them. The secret goes to the secrets backend and isn't shown again.
function GoogleSignIn() {
  const [state, setState] = useState(null);
  const [form, setForm] = useState({ client_id: "", client_secret: "" });
  const [note, setNote] = useState(null);
  const [error, setError] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const load = () => kl("identities/google/client").then((r) => { setState(r); setError(null); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);
  async function save(e) {
    e.preventDefault();
    setNote(null);
    try {
      setState(await kl("identities/google/client", { method: "PUT", body: { client_id: form.client_id.trim(), client_secret: form.client_secret } }));
      setForm({ client_id: "", client_secret: "" });
      setNote(["ok", "Saved"]);
    } catch (err) {
      setNote(["bad", err.message]);
    }
  }
  async function remove() {
    try {
      setState(await kl("identities/google/client", { method: "DELETE" }));
      setConfirming(false);
      setNote(["ok", "Removed"]);
    } catch (err) {
      setConfirming(false);
      setNote(["bad", err.message]);
    }
  }
  const ready = Boolean(form.client_id.trim() && form.client_secret);
  const callback = `${typeof window === "undefined" ? "" : window.location.origin}/link/google/callback`;
  return h("section", { className: "panel", style: { marginTop: 24 } },
    h("div", { className: "panel-head" }, h("div", null, h("h2", null, "Google sign-in"),
      h("p", null, "The OAuth client people sign in with to link their Google identity, so Drive files shared with them by name reach them. In Google Cloud, create a web application client whose redirect URI is ",
        h("span", { className: "mono" }, callback), "."))),
    error ? h("div", { style: { padding: 16 } }, h(Alert, { onRetry: load }, error)) : null,
    state === null && !error ? h(SkeletonRows, { rows: 1, widths: [40] }) : state ? h("div", { style: { padding: 16, display: "grid", gap: 12 } },
      h("div", { className: "actions" },
        h(Status, { tone: state.configured ? "ok" : "idle" }, state.configured ? "Set up" : "Not set up"),
        state.configured ? h("span", { className: "mono" }, state.client_id) : null,
        state.configured ? h("button", { type: "button", className: "btn btn-ghost btn-sm", onClick: () => setConfirming(true) }, "Remove") : null),
      h("form", { className: "actions", onSubmit: save, style: { flexWrap: "wrap" } },
        h("input", { className: "input", placeholder: "Client ID", "aria-label": "Client ID", value: form.client_id,
                     onChange: (e) => setForm((f) => ({ ...f, client_id: e.target.value })) }),
        h("input", { className: "input", type: "password", placeholder: "Client secret", "aria-label": "Client secret", autoComplete: "off",
                     value: form.client_secret, onChange: (e) => setForm((f) => ({ ...f, client_secret: e.target.value })) }),
        h("button", { type: "submit", className: ready ? "btn btn-primary" : "btn", disabled: !ready },
          state.configured ? "Replace" : "Save"),
        note ? h(Status, { tone: note[0] }, note[1]) : null)) : null,
    confirming ? h(Confirm, { title: "Remove Google sign-in?", action: "Remove sign-in", onCancel: () => setConfirming(false), onConfirm: remove },
      "People can't link a Google identity until it's set up again. Identities already linked stay linked.") : null);
}
