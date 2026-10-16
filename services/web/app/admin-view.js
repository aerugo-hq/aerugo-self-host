"use client";
// Admin: everything a team's admins manage. Personal-data flags, members, models (with budgets), connections (with
// each repo's team), imports of Confluence and Notion exports, and the audit trail. Org-wide settings live on the
// Compliance page.
import { createElement as h, useEffect, useState } from "react";
import { ago, kl, klUpload } from "../lib/client.js";
import { Icon } from "./icons.js";
import { SourceIcon, sourceName } from "./brand.js";
import { Alert, Confirm, Empty, PageHead, SectionNav, SkeletonRows, Spinner, Table, Status, TeamPicker, stamp } from "./ui.js";
import { ManagedNote, TerraformDetails, budgetItem, memberItem, modelItem, teamItem, toolName } from "./terraform.js";

const enc = encodeURIComponent;
// What the gateway speaks. Vertex, Bedrock, and Azure work through an OpenAI-compatible gateway such as LiteLLM.
const PROVIDERS = ["openai_compatible", "anthropic"];
const CLASSES = ["public", "internal", "confidential", "restricted"];
const ROLES = ["member", "curator", "admin", "auditor"];

// One job at a time: each section is its own address (#/admin/people). Sources comes first because bringing knowledge
// in is what a team's admins do most.
const SECTIONS = [
  ["sources", "Sources", "Connections and imports that bring knowledge in"],
  ["pipeline", "Pipeline", "Every sync, import, and push, and what failed"],
  ["people", "People", "Members and their roles"],
  ["models", "Models", "Models your team can ask, and their budgets"],
  ["costs", "Costs", "What your models used, against their budgets"],
  ["review", "Review", "Personal data waiting for a decision"],
  ["activity", "Activity", "The audit trail"],
];

// Pipeline (slice R2): a team's runs, newest first. A failed sync or removal can run again; a failed import or push
// can't (its upload is cleared once applied), so it says what to do instead.
const RUN_KINDS = { "connection.discover": "Discover", "connection.sync": "Sync", "connection.retire": "Remove connection", "import.apply": "Import",
                    "import.retire": "Remove import", "pipeline.push": "Push", "pipeline.retire": "Remove pipeline" };
const RUN_TONES = { done: "ok", failed: "bad", running: "pending", queued: "idle" };  // Datavine's six: running is in progress, not a warning
const RUN_STATES = { done: "Done", failed: "Failed", running: "Running", queued: "Waiting" };
const RUN_FIX = { "import.apply": "Upload the export again to retry it.", "pipeline.push": "Push again from CI to retry it." };

export function Runs({ ws }) {
  const [runs, setRuns] = useState(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const load = () => kl(`workspaces/${enc(ws)}/runs${status ? `?status=${status}` : ""}`)
    .then((r) => { setRuns(r.runs); setError(null); }).catch((e) => setError(e.message));
  useEffect(() => { setRuns(null); load(); }, [ws, status]);
  async function retry(run) {
    setBusy(run.id);
    setError(null);
    try {
      await kl(`workspaces/${enc(ws)}/runs/${run.id}/retry`, { method: "POST" });
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }
  return h("section", { className: "panel", "aria-labelledby": "runs-h" },
    h("div", { className: "panel-head" }, h("h2", { id: "runs-h" }, "Pipeline"),
      h("select", { className: "select", value: status, onChange: (e) => setStatus(e.target.value), "aria-label": "Show runs" },
        h("option", { value: "" }, "All runs"), ...["failed", "running", "queued", "done"].map((s) => h("option", { key: s, value: s }, RUN_STATES[s])))),
    error ? h(Alert, null, error) : null,
    runs === null ? h(SkeletonRows, { rows: 4, widths: [50, 20, 15] })
      : !runs.length ? h(Empty, null, status ? "No runs like that." : "No runs yet. Syncs, imports, and pushes show up here as they happen.")
        : h("ul", { className: "runs" }, runs.map((r) => h("li", { key: r.id },
            h("div", { className: "run-main" }, h("b", null, RUN_KINDS[r.kind] || r.kind), r.target ? h("span", { className: "muted" }, r.target) : null),
            h(Status, { tone: RUN_TONES[r.status] || "idle" }, RUN_STATES[r.status] || r.status,
              r.status === "failed" ? ` after ${r.attempts} of ${r.max_attempts} tries` : ""),
            h("span", { className: "muted small" }, ago(r.updated_at)),
            r.error ? h("p", { className: "run-error small" }, r.error) : null,
            r.retryable ? h("button", { type: "button", className: "btn btn-sm", disabled: busy === r.id, onClick: () => retry(r) },
              busy === r.id ? "Retrying…" : "Retry")
              : r.status === "failed" && RUN_FIX[r.kind] ? h("p", { className: "muted small" }, RUN_FIX[r.kind]) : null))));
}

// Costs (slice R3): what a team's models used, in tokens, against each monthly budget (the same UTC calendar month the
// budget gate counts), and day by day. Org admins also see every team's month.
const fmt = (n) => Number(n).toLocaleString("en-US");

// The last `days` UTC days ending today, every model's tokens summed, a day with no use as zero.
export function dailyTotals(daily, days, today = new Date()) {
  const sums = {};
  for (const d of daily) sums[d.day] = (sums[d.day] || 0) + d.tokens;
  const out = [];
  for (let i = days - 1; i >= 0; i--) {
    const day = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i)).toISOString().slice(0, 10);
    out.push({ day, tokens: sums[day] || 0 });
  }
  return out;
}

function Bars({ series }) {
  const max = Math.max(1, ...series.map((d) => d.tokens));
  const width = 600, height = 120, step = width / series.length;
  return h("svg", { viewBox: `0 0 ${width} ${height + 18}`, className: "bars", role: "img",
                    "aria-label": `Tokens per day for the last ${series.length} days. The most in one day was ${fmt(max === 1 && !series.some((d) => d.tokens) ? 0 : max)}.` },
    series.map((d, i) => {
      const bar = d.tokens ? Math.max(2, Math.round((d.tokens / max) * height)) : 2;
      return h("rect", { key: d.day, x: i * step + 1, y: height - bar, width: Math.max(1, step - 2), height: bar, className: d.tokens ? "bar" : "bar empty" },
        h("title", null, `${d.day}: ${fmt(d.tokens)} tokens`));
    }),
    h("text", { x: 0, y: height + 14, className: "axis" }, series[0].day),
    h("text", { x: width, y: height + 14, textAnchor: "end", className: "axis" }, series[series.length - 1].day));
}

export function Costs({ ws, orgAdmin }) {
  const [usage, setUsage] = useState(null);
  const [org, setOrg] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    setUsage(null);
    setError(null);
    kl(`workspaces/${enc(ws)}/usage?days=30`).then(setUsage).catch((e) => setError(e.message));
    if (orgAdmin) kl("usage").then(setOrg).catch(() => setOrg(null));
  }, [ws, orgAdmin]);
  const loading = h(SkeletonRows, { rows: 3, widths: [50, 70, 30] });
  return [
    h("section", { className: "panel", key: "month", "aria-labelledby": "month-h" },
      h("div", { className: "panel-head" }, h("h2", { id: "month-h" }, "This month")),
      error ? h(Alert, null, error) : null,
      usage === null ? (error ? null : loading)
        : !usage.models.length ? h(Empty, null, "No models yet. Add one under Models.")
          : h("ul", { className: "meters" }, usage.models.map((m) => {
              const pct = m.budget ? Math.min(100, Math.round((m.month_tokens / m.budget) * 100)) : null;
              return h("li", { key: m.model },
                h("div", { className: "meter-head" }, h("b", null, m.model),
                  h("span", { className: "muted small" }, m.budget ? `${fmt(m.month_tokens)} of ${fmt(m.budget)} tokens` : `${fmt(m.month_tokens)} tokens, no budget`)),
                m.budget ? h("div", { className: `meter${pct >= 100 ? " over" : pct >= 80 ? " near" : ""}`, role: "meter", "aria-valuenow": pct,
                                      "aria-valuemin": 0, "aria-valuemax": 100, "aria-label": `${m.model}: ${pct}% of its monthly budget` },
                  h("span", { style: { width: `${pct}%` } })) : null,
                h("span", { className: "muted small" }, `${fmt(m.month_calls)} call${m.month_calls === 1 ? "" : "s"}`));
            }))),
    h("section", { className: "panel", key: "days", "aria-labelledby": "days-h" },
      h("div", { className: "panel-head" }, h("h2", { id: "days-h" }, "Last 30 days")),
      usage === null ? (error ? null : loading) : h(Bars, { series: dailyTotals(usage.daily, usage.days) })),
    orgAdmin && org ? h("section", { className: "panel", key: "org", "aria-labelledby": "org-h" },
      h("div", { className: "panel-head" }, h("h2", { id: "org-h" }, "Every team this month")),
      h("ul", { className: "org-usage" }, org.teams.map((t) => h("li", { key: t.team },
        h("span", null, t.team), h("span", { className: "muted" }, `${fmt(t.tokens)} tokens, ${fmt(t.calls)} call${t.calls === 1 ? "" : "s"}`))))) : null,
  ];
}

// In a team's scope (slice N1) this is its Team settings: the team is the address's, and there's no picker.
export default function AdminView({ me, section, team: fixed = "" }) {
  const teams = me ? me.admin_of : [];
  const [chosen, setWs] = useState("");
  const ws = fixed || chosen;
  useEffect(() => {
    if (!fixed && !chosen && teams.length) setWs(teams[0]);
  }, [teams.length, fixed]);
  if (!me) return h("div", { className: "page" }, h("div", { className: "panel" }, h(SkeletonRows, { rows: 5 })));
  if (!teams.length && !me.org_admin) {
    return h("div", { className: "page" }, h(PageHead, { title: "Admin" }),
      h("div", { className: "panel" }, h(Empty, null, "You aren't an admin of any team. Ask one of your team's admins for access.")));
  }
  if (fixed && !teams.includes(fixed) && !me.org_admin) {
    return h("div", { className: "page" }, h(PageHead, { title: "Team settings" }),
      h("div", { className: "panel" }, h(Empty, null, "Only this team's admins can change its settings.")));
  }
  const current = SECTIONS.some(([id]) => id === section) ? section : "sources";
  const team = (el) => (ws ? el : h("div", { className: "panel", key: "no-team" },
    h(Empty, null, "You don't administer a team yet. Create one under People.")));
  return h("div", { className: "page" },
    h(PageHead, fixed ? { title: "Team settings", lede: "What this team knows, who's in it, and what it can ask." }
                      : { title: "Admin", lede: "What your teams know, who's in them, and what they can ask." },
      !fixed && teams.length > 1 && current !== "review" ? h(TeamPicker, { teams, value: ws, onChange: setWs }) : null),
    h(SectionNav, { base: fixed ? `#/t/${enc(fixed)}/settings` : "#/admin", sections: SECTIONS, current, label: fixed ? "Team settings sections" : "Admin sections" }),
    current === "sources" ? [team(h(Imports, { ws, key: `i-${ws}` })), ws ? h(Connections, { ws, teams, orgAdmin: me.org_admin, key: `c-${ws}` }) : null] : null,
    current === "pipeline" ? team(h(Runs, { ws, key: `r-${ws}` })) : null,
    current === "people" ? [team(h(Team, { ws, key: `t-${ws}` })), me.org_admin && !fixed ? h(NewTeam, { key: "new-team" }) : null] : null,
    current === "models" ? team(h(Models, { ws, orgAdmin: me.org_admin, key: `m-${ws}` })) : null,
    current === "costs" ? team(h(Costs, { ws, orgAdmin: me.org_admin, key: `k-${ws}` })) : null,
    current === "review" ? h(PersonalData, { key: "review" }) : null,
    current === "activity" ? team(h(Audit, { ws, key: `a-${ws}` })) : null);
}

function NewTeam() {
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [secret, setSecret] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function create(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await kl("workspaces", { method: "POST", body: { slug, name, discoverability: secret ? "private" : "org" } });
      window.location.reload(); // the team picker comes from whoami, so reload to include the new team
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }
  return h("section", { className: "panel" },
    h("div", { className: "panel-head" }, h("div", null, h("h2", null, "New team"), h("p", null, "Org admins administer every team they create."))),
    h("form", { className: "panel-body inline-form", onSubmit: create },
      h("input", { className: "input", value: slug, onChange: (e) => setSlug(e.target.value.toLowerCase()), placeholder: "Slug, like platform",
                   "aria-label": "Slug", pattern: "[a-z0-9][a-z0-9-]{0,39}", required: true }),
      h("input", { className: "input", value: name, onChange: (e) => setName(e.target.value), placeholder: "Name, like Platform", "aria-label": "Name", required: true }),
      h("label", { className: "check" }, h("input", { type: "checkbox", checked: secret, onChange: () => setSecret(!secret) }), "Private"),
      h("button", { className: "btn btn-primary", disabled: busy || !slug || !name }, busy ? h(Spinner) : null, "Create team")),
    error ? h("div", { style: { padding: "0 16px 16px" } }, h(Alert, null, error)) : null);
}

function Team({ ws }) {
  const [members, setMembers] = useState(null);
  const [info, setInfo] = useState(null);
  const [principal, setPrincipal] = useState("");
  const [role, setRole] = useState("member");
  const [error, setError] = useState(null);
  const load = () => {
    kl(`workspaces/${enc(ws)}/members`).then((r) => setMembers(r.members)).catch((e) => setError(e.message));
    kl(`workspaces/${enc(ws)}`).then(setInfo).catch(() => setInfo(null)); // with what manages it, if a tool does
  };
  useEffect(() => { load(); }, [ws]);
  async function act(work) {
    setError(null);
    try {
      await work();
      load();
    } catch (e) {
      setError(e.message);
    }
  }
  const grant = (event) => {
    event.preventDefault();
    act(async () => {
      await kl(`workspaces/${enc(ws)}/members`, { method: "PUT", body: { principal: principal.trim(), role } });
      setPrincipal("");
    });
  };
  const revoke = (g) => act(() => kl(`workspaces/${enc(ws)}/members?principal=${enc(g.principal)}&role=${g.role}`, { method: "DELETE" }));
  const settings = (patch) => act(() => kl(`workspaces/${enc(ws)}`, { method: "PATCH", body: patch }));
  const isPrivate = info && info.discoverability === "private";

  return h("section", { className: "panel", style: { marginTop: 24 } },
    h("div", { className: "panel-head" },
      h("div", null, h("h2", null, info ? `Team: ${info.name}` : "Team"), h("p", null, isPrivate ? "Private: only members can find it." : "Org-wide: anyone in the org can search it, within their permissions.")),
      info ? h("div", { className: "seg", role: "group", "aria-label": "Visibility" },
        h("button", { type: "button", "aria-pressed": !isPrivate, onClick: () => isPrivate && settings({ discoverability: "org" }) }, "Org-wide"),
        h("button", { type: "button", "aria-pressed": Boolean(isPrivate), onClick: () => !isPrivate && settings({ discoverability: "private" }) }, "Private")) : null),
    info ? h(ManagedNote, { managed: info.managed_by }) : null,
    info ? h("form", { className: "panel-body inline-form", key: info.name, style: { borderBottom: "1px solid var(--border)" },
                      onSubmit: (e) => { e.preventDefault(); settings({ name: e.target.elements.name.value }); } },
      h("input", { className: "input", name: "name", defaultValue: info.name, "aria-label": "Team name" }),
      h("button", { className: "btn" }, "Rename")) : null,
    h("form", { className: "panel-body inline-form", onSubmit: grant, style: { borderBottom: "1px solid var(--border)" } },
      h("input", { className: "input mono", value: principal, onChange: (e) => setPrincipal(e.target.value), placeholder: "user:bob or group:finance", "aria-label": "User or group" }),
      h("select", { className: "select", value: role, onChange: (e) => setRole(e.target.value), "aria-label": "Role" },
        ROLES.map((r) => h("option", { key: r, value: r }, r))),
      h("button", { className: "btn btn-primary", disabled: !principal.trim() }, "Add")),
    error ? h("div", { style: { padding: 16 } }, h(Alert, null, error)) : null,
    members === null ? h(SkeletonRows, { rows: 3, widths: [40, 16] })
      : members.length ? h(Table, null,
          h("thead", null, h("tr", null, h("th", null, "Member"), h("th", null, "Role"), h("th", { className: "actions-cell" }, h("span", { className: "sr-only" }, "Actions")))),
          h("tbody", null, members.map((g) => h("tr", { key: `${g.principal}-${g.role}` },
            h("td", { className: "mono" }, g.principal),
            h("td", null, g.role, g.managed_by ? h("span", { className: "tag tf-tag" }, toolName(g.managed_by.by)) : null),
            h("td", { className: "actions-cell" }, h("button", { type: "button", className: "btn btn-ghost btn-sm", onClick: () => revoke(g),
                                                               "aria-label": `Remove ${g.principal} as ${g.role}` }, "Remove"))))))
        : h(Empty, null, "No direct members yet. Org admins can always manage this team."),
    info && members ? h(TerraformDetails, { items: [teamItem(info), ...members.map((g) => memberItem(ws, g))] }) : null);
}

function Models({ ws, orgAdmin }) {
  const empty = { name: "", provider: "openai_compatible", model: "", base_url: "", api_key: "", allowed: ["public", "internal"] };
  const [models, setModels] = useState(null);
  const [form, setForm] = useState(null);
  const [probe, setProbe] = useState({});
  const [budget, setBudget] = useState({}); // model name -> the cap being edited, for org admins
  const [error, setError] = useState(null);
  const load = () => kl(`workspaces/${enc(ws)}/models`).then((r) => setModels(r.models)).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [ws]);

  async function register(event) {
    event.preventDefault();
    setError(null);
    try {
      await kl(`workspaces/${enc(ws)}/models`, { method: "POST", body: {
        name: form.name, provider: form.provider, model: form.model, base_url: form.base_url || null,
        api_key: form.api_key, allowed_classifications: form.allowed } });
      setForm(null);
      load();
    } catch (e) {
      setError(e.message);
    }
  }
  async function test(name) {
    setProbe((p) => ({ ...p, [name]: ["idle", "Testing…"] }));
    try {
      const r = await kl(`workspaces/${enc(ws)}/models/${enc(name)}/probe`, { method: "POST" });
      setProbe((p) => ({ ...p, [name]: r.ok === false ? ["bad", r.error || "No answer"] : ["ok", "Answered"] }));
    } catch (e) {
      setProbe((p) => ({ ...p, [name]: ["bad", e.message] }));
    }
  }
  async function saveBudget(name) {
    const value = String(budget[name] || "").trim();
    setError(null);
    try {
      await kl(`workspaces/${enc(ws)}/models/${enc(name)}/budget`, { method: "PUT", body: { monthly_tokens: value ? Number(value) : null } });
      setBudget((b) => ({ ...b, [name]: undefined }));
      load();
    } catch (e) {
      setError(e.message);
    }
  }
  const editBudget = (m) => setBudget((b) => ({ ...b, [m.name]: b[m.name] !== undefined ? undefined : String(m.monthly_token_budget || "") }));
  const usage = (m) => `${(m.used_this_month || 0).toLocaleString()}${m.monthly_token_budget ? ` of ${m.monthly_token_budget.toLocaleString()}` : ""} tokens`;
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });
  const toggle = (c) => setForm({ ...form, allowed: form.allowed.includes(c) ? form.allowed.filter((x) => x !== c) : [...form.allowed, c] });

  return h("section", { className: "panel", style: { marginTop: 24 } },
    h("div", { className: "panel-head" }, h("div", null, h("h2", null, "Models"), h("p", null, "Keys live in OpenBao; this page never shows them.")),
      h("button", { type: "button", className: "btn btn-sm", onClick: () => setForm(form ? null : empty) }, form ? "Cancel" : "Register model")),
    form ? h("form", { className: "panel-body", onSubmit: register, style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, borderBottom: "1px solid var(--border)" } },
      h("label", { className: "field" }, h("span", null, "Name"), h("input", { className: "input", value: form.name, onChange: set("name"), placeholder: "claude", required: true })),
      h("label", { className: "field" }, h("span", null, "Provider"), h("select", { className: "select", value: form.provider, onChange: set("provider") },
        PROVIDERS.map((p) => h("option", { key: p, value: p }, p)))),
      h("label", { className: "field" }, h("span", null, "Model"), h("input", { className: "input mono", value: form.model, onChange: set("model"), placeholder: "claude-sonnet-4-6", required: true })),
      h("label", { className: "field" }, h("span", null, "Base URL"), h("input", { className: "input mono", value: form.base_url, onChange: set("base_url"), placeholder: "Optional" })),
      h("label", { className: "field", style: { gridColumn: "1 / -1" } }, h("span", null, "API key"),
        h("input", { className: "input mono", type: "password", value: form.api_key, onChange: set("api_key"), required: true, autoComplete: "off" }),
        h("span", { className: "hint" }, "Stored in OpenBao and never shown again.")),
      h("fieldset", { className: "field", style: { gridColumn: "1 / -1", border: 0, padding: 0, margin: 0 } }, h("span", null, "May receive"),
        h("div", { className: "actions", style: { gap: 16 } }, CLASSES.map((c) => h("label", { key: c, className: "check" },
          h("input", { type: "checkbox", checked: form.allowed.includes(c), onChange: () => toggle(c) }), c)))),
      h("div", { style: { gridColumn: "1 / -1" } }, h("button", { className: "btn btn-primary" }, "Register"))) : null,
    error ? h("div", { style: { padding: 16 } }, h(Alert, null, error)) : null,
    models === null ? h(SkeletonRows, { rows: 2, widths: [30, 30] })
      : models.length ? h(Table, null,
          h("thead", null, h("tr", null, h("th", null, "Model"), h("th", { className: "hide-sm" }, "May receive"), h("th", { className: "num hide-sm" }, "This month"), h("th", null, "Test"), h("th", { className: "actions-cell" }, h("span", { className: "sr-only" }, "Actions")))),
          h("tbody", null, models.map((m) => h("tr", { key: m.name },
            h("td", { className: "ellipsis" }, h("div", { className: "title" }, m.name, m.managed_by ? h("span", { className: "tag tf-tag" }, toolName(m.managed_by.by)) : null), h("div", { className: "sub mono" }, `${m.provider} · ${m.model}`)),
            h("td", { className: "hide-sm muted" }, m.allowed_classifications.join(", ")),
            h("td", { className: "num hide-sm" }, budget[m.name] !== undefined
              ? h("span", { className: "actions" }, h("input", { className: "input", type: "number", min: 1, style: { width: 120 }, placeholder: "No cap",
                  value: budget[m.name], onChange: (e) => setBudget((b) => ({ ...b, [m.name]: e.target.value })), "aria-label": "Monthly token budget" }),
                h("button", { type: "button", className: "btn btn-sm btn-primary", onClick: () => saveBudget(m.name) }, "Save"))
              : usage(m)),
            h("td", null, probe[m.name] ? h(Status, { tone: probe[m.name][0] }, probe[m.name][1]) : h(Status, { tone: "ring" }, "Not tested")),
            h("td", { className: "actions-cell" }, h("div", { className: "actions" },
              orgAdmin ? h("button", { type: "button", className: "btn btn-sm", onClick: () => editBudget(m) }, budget[m.name] !== undefined ? "Cancel" : "Budget") : null,
              h("button", { type: "button", className: "btn btn-sm", onClick: () => test(m.name) }, "Test")))))))
        : h(Empty, { action: h("button", { type: "button", className: "btn btn-sm", onClick: () => setForm(empty) }, "Register model") },
            "No models yet. Ask and drafting need one."),
    models && models.length ? h(TerraformDetails, { items: models.flatMap((m) => [modelItem(ws, m), budgetItem(ws, m)]) }) : null);
}

// Every source a team can connect (ADR 0011: a service account reads; people see only their own access). Ready ones
// open their form or their steps; the rest are next in line.
export const CONNECTORS = [
  { id: "jira", name: "Jira Cloud", what: "Issues and their comments, with Jira's own permissions, security levels included.", ready: true, action: "Connect" },
  { id: "github", name: "GitHub", what: "Repositories, through a GitHub App installed on the ones to index.", ready: true, action: "Set up" },
  { id: "imports", name: "Notion or Confluence export", what: "Upload an export; its pages arrive with the readers you name.", ready: true, action: "How" },
  { id: "pipelines", name: "CI pipeline", what: "Documents pushed from GitHub Actions or another CI, signed in with its own token.", ready: true, action: "Set up" },
  { id: "lineage", name: "Data lineage", what: "OpenLineage events from Airflow, dbt, Dataform, or Spark: what feeds what.", ready: true, action: "Set up" },
  { id: "google", name: "Google Drive", what: "Shared drives a service account is a member of, and folders shared with it.", ready: true, action: "Connect" },
  { id: "confluence", name: "Confluence Cloud", what: "Spaces, their pages and blog posts, with Confluence's own permissions: each space's readers, narrowed by every restriction down the tree.", ready: true, action: "Connect" },
  { id: "slack", name: "Slack", what: "Channels a bot is in, readable by each channel's members.", ready: false },
  { id: "microsoft", name: "Microsoft 365", what: "SharePoint sites an admin grants, with each file's sharing.", ready: false },
  { id: "notion", name: "Notion", what: "Pages shared with an internal integration.", ready: false },
  { id: "directory", name: "Directory", what: "Who's at the company, and who has left (Okta, Google Workspace, or Microsoft Entra): a leaver's matched access goes at once. Google's and Entra's groups also reach the files shared with them.", ready: true, action: "Connect" },
];
const EVERY = [[15, "Every 15 minutes"], [60, "Every hour"], [360, "Every 6 hours"], [1440, "Every day"]];
const blankJira = { name: "jira", api_base: "", email: "", api_token: "", projects: "", company_site: false, every_minutes: 60, credential_ref: "" };
const blankConfluence = { name: "confluence", api_base: "", email: "", api_token: "", spaces: "", labels: "", page_trees: "",
                          company_site: false, every_minutes: 60, credential_ref: "" };
const list = (text, upper = false) => (text.trim() ? text.split(/[\s,]+/).filter(Boolean).map((x) => (upper ? x.toUpperCase() : x)) : null);
const blankDrive = { name: "drive", key: "", domain: "", drives: "", every_minutes: 60, credential_ref: "" };
const blankDirectory = { name: "directory", provider: "okta", api_base: "", api_token: "", key: "", admin_email: "", tenant_id: "", client_id: "",
                         certificate: "", every_minutes: 60, credential_ref: "" };
// The scope list each kind has, to edit in place: its field, its label, and an example.
const SCOPES = { jira: ["projects", "Projects", "EDP, PAY"], confluence: ["spaces", "Spaces", "PAY, ENG"], google: ["drives", "Shared drives", "Drive ids"],
                 github: ["repositories", "Repositories", "acme/payments-api"] };
const managedTitle = (m) => `Managed by ${toolName(m.by)}${m.workspace ? `, ${m.workspace}` : ""}: change it there. A change made here would be undone on its next apply.`;
const every = (m) => (m ? (m % 1440 === 0 ? `Every ${m / 1440 === 1 ? "day" : `${m / 1440} days`}` : m % 60 === 0 ? `Every ${m / 60 === 1 ? "hour" : `${m / 60} hours`}` : `Every ${m} min`) : "Default");

function Connections({ ws, teams, orgAdmin }) {
  const [items, setItems] = useState(null);
  const [note, setNote] = useState({});
  const [repos, setRepos] = useState({}); // connection name -> its repos, once shown
  const [error, setError] = useState(null);
  const [gallery, setGallery] = useState(false);
  const [chosen, setChosen] = useState(null); // the connector whose form or steps are open
  const [form, setForm] = useState(blankJira);
  const [busy, setBusy] = useState(false);
  const [installs, setInstalls] = useState(null); // GitHub App installations, once looked up
  const [confirming, setConfirming] = useState(null); // the connection about to be removed
  const [drive, setDrive] = useState(blankDrive);
  const [dir, setDir] = useState(blankDirectory); // the directory form: Okta or Google Workspace (S2)
  const [editing, setEditing] = useState(null); // the connection being edited, in its own row
  const [edit, setEdit] = useState(null); // its form, from what it was declared with: cadence and scope
  const [cf, setCf] = useState(blankConfluence); // the Confluence form (index 14)
  const load = () => kl(`workspaces/${enc(ws)}/connections`).then((r) => { setItems(r.connections); setError(null); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [ws]);
  const say = (name, tone, text) => setNote((n) => ({ ...n, [name]: [tone, text] }));
  async function resync(name) {
    say(name, "idle", "Queuing…");
    try {
      await kl(`connections/${enc(name)}/refresh?ws=${enc(ws)}`, { method: "POST" });
      say(name, "ok", "Re-sync queued");
    } catch (e) {
      say(name, "bad", e.message);
    }
  }
  async function setEnabled(name, enabled) {
    say(name, "idle", enabled ? "Resuming…" : "Pausing…");
    try {
      await kl(`workspaces/${enc(ws)}/connections/${enc(name)}`, { method: "PATCH", body: { enabled } });
      say(name, "ok", enabled ? "Resumed" : "Paused");
      load();
    } catch (e) {
      say(name, "bad", e.message);
    }
  }
  async function remove(name) {
    setConfirming(null);
    say(name, "idle", "Removing…");
    try {
      await kl(`workspaces/${enc(ws)}/connections/${enc(name)}`, { method: "DELETE" });
      load();
    } catch (e) {
      say(name, "bad", e.message);
    }
  }
  // Each repo can go to its own team (slice F1); a team is offered only if you administer it, as the API requires.
  async function showRepos(name) {
    if (repos[name]) {
      setRepos((r) => ({ ...r, [name]: undefined }));
      return;
    }
    setRepos((r) => ({ ...r, [name]: "loading" }));
    try {
      const c = await kl(`workspaces/${enc(ws)}/connections/${enc(name)}`);
      setRepos((r) => ({ ...r, [name]: c.containers }));
    } catch (e) {
      setRepos((r) => ({ ...r, [name]: undefined }));
      setError(e.message);
    }
  }
  async function route(name, repository, team) {
    say(name, "idle", "Moving…");
    try {
      await kl(`workspaces/${enc(ws)}/connections/${enc(name)}/routes`, { method: "PUT", body: { repository, workspace: team === ws ? null : team } });
      setRepos((r) => ({ ...r, [name]: r[name].map((c) => (c.external_id === repository ? { ...c, team } : c)) }));
      say(name, "ok", `${repository} moves to ${team} on its next sync`);
    } catch (e) {
      say(name, "bad", e.message);
    }
  }
  async function connectJira(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const byRef = form.credential_ref.trim();
    try {
      await kl(`workspaces/${enc(ws)}/connections/jira`, { method: "POST", body: {
        name: form.name.trim(), api_base: form.api_base.trim().replace(/\/+$/, ""),
        email: byRef ? null : form.email.trim(), api_token: byRef ? null : form.api_token, credential_ref: byRef || null,
        projects: form.projects.trim() ? form.projects.split(/[\s,]+/).filter(Boolean).map((p) => p.toUpperCase()) : null,
        company_site: form.company_site, every_minutes: Number(form.every_minutes) } });
      setChosen(null);
      setGallery(false);
      setForm(blankJira);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function connectConfluence(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const byRef = cf.credential_ref.trim();
    try {
      await kl(`workspaces/${enc(ws)}/connections/confluence`, { method: "POST", body: {
        name: cf.name.trim(), api_base: cf.api_base.trim().replace(/\/+$/, "").replace(/\/wiki$/, ""),
        email: byRef ? null : cf.email.trim(), api_token: byRef ? null : cf.api_token, credential_ref: byRef || null,
        spaces: list(cf.spaces, true), labels: list(cf.labels.toLowerCase()), page_trees: list(cf.page_trees),
        company_site: cf.company_site, every_minutes: Number(cf.every_minutes) } });
      setChosen(null);
      setGallery(false);
      setCf(blankConfluence);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function connectDrive(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const byRef = drive.credential_ref.trim();
    try {
      await kl(`workspaces/${enc(ws)}/connections/google`, { method: "POST", body: {
        name: drive.name.trim(), service_account_key: byRef ? null : drive.key.trim(), credential_ref: byRef || null,
        domain: drive.domain.trim().toLowerCase() || null,
        drives: drive.drives.trim() ? drive.drives.split(/[\s,]+/).filter(Boolean) : null, every_minutes: Number(drive.every_minutes) } });
      setChosen(null);
      setGallery(false);
      setDrive(blankDrive);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function connectDirectory(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const byRef = dir.credential_ref.trim();
    const [okta, google, entra] = ["okta", "google", "entra"].map((p) => dir.provider === p);
    try {
      await kl(`workspaces/${enc(ws)}/connections/directory`, { method: "POST", body: {
        name: dir.name.trim(), provider: dir.provider, api_base: okta ? dir.api_base.trim().replace(/\/+$/, "") : null,
        api_token: byRef || !okta ? null : dir.api_token, service_account_key: byRef || !google ? null : dir.key.trim(),
        admin_email: google ? dir.admin_email.trim().toLowerCase() : null,
        tenant_id: entra ? dir.tenant_id.trim() : null, client_id: entra ? dir.client_id.trim() : null,
        certificate: byRef || !entra ? null : dir.certificate.trim(), credential_ref: byRef || null,
        every_minutes: Number(dir.every_minutes) } });
      setChosen(null);
      setGallery(false);
      setDir(blankDirectory);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  // Editing in place: its cadence, and its scope list where its kind has one. A list can be changed, not removed.
  async function startEdit(c) {
    if (editing === c.name) {
      setEditing(null);
      return;
    }
    setEditing(c.name);
    setEdit(null);
    try {
      const d = await kl(`workspaces/${enc(ws)}/connections/${enc(c.name)}`);
      const scope = SCOPES[c.kind];
      const held = (d.settings || {});
      setEdit({ every_minutes: held.every_minutes ? String(held.every_minutes) : "", scope: scope ? (held[scope[0]] || []).join(", ") : "" });
    } catch (e) {
      setEditing(null);
      setError(e.message);
    }
  }
  async function saveEdit(event, c) {
    event.preventDefault();
    const scope = SCOPES[c.kind];
    const body = {};
    if (edit.every_minutes) body.every_minutes = Number(edit.every_minutes);
    if (scope && edit.scope.trim()) body[scope[0]] = edit.scope.split(/[\s,]+/).filter(Boolean).map((x) => (c.kind === "jira" || c.kind === "confluence" ? x.toUpperCase() : x));
    say(c.name, "idle", "Saving…");
    try {
      await kl(`workspaces/${enc(ws)}/connections/${enc(c.name)}`, { method: "PATCH", body });
      say(c.name, "ok", "Saved");
      setEditing(null);
      load();
    } catch (e) {
      say(c.name, "bad", e.message);
    }
  }
  async function findInstallations() {
    setInstalls("loading");
    try {
      setInstalls((await kl(`workspaces/${enc(ws)}/connectors/github/installations`)).installations);
    } catch (e) {
      setInstalls(null);
      setError(e.message);
    }
  }
  async function connectGitHub(inst) {
    setError(null);
    try {
      const name = String(inst.account).toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "github";
      await kl(`workspaces/${enc(ws)}/connections`, { method: "POST", body: { name, installation_id: inst.installation_id } });
      setChosen(null);
      setGallery(false);
      load();
    } catch (e) {
      setError(e.message);
    }
  }
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const setD = (key) => (e) => setDrive({ ...drive, [key]: e.target.value });
  const setO = (key) => (e) => setDir({ ...dir, [key]: e.target.value });
  const setC = (key) => (e) => setCf({ ...cf, [key]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const field = (label, input, hint) => h("label", { className: "field" }, h("span", null, label), input, hint ? h("span", { className: "hint" }, hint) : null);
  const api = `${typeof window === "undefined" ? "" : window.location.origin.replace(":3000", ":8000")}/v1/workspaces/${ws}`;
  const steps = {
    jira: () => h("form", { className: "panel-body connector-form", onSubmit: connectJira },
      h("p", { className: "connector-lede" }, "Create a service account in Atlassian (or a dedicated user), give it browse access to the projects to index, and make it an API token. It only sets what's indexed: each issue keeps Jira's own readers."),
      field("Name", h("input", { className: "input", value: form.name, onChange: set("name"), required: true, pattern: "[a-z0-9][a-z0-9-]{0,39}" })),
      field("Jira site", h("input", { className: "input mono", value: form.api_base, onChange: set("api_base"), placeholder: "https://your-site.atlassian.net", required: true })),
      form.credential_ref ? null : field("Account email", h("input", { className: "input", type: "email", value: form.email, onChange: set("email"), required: true, autoComplete: "off" })),
      form.credential_ref ? null : field("API token", h("input", { className: "input mono", type: "password", value: form.api_token, onChange: set("api_token"), required: true, autoComplete: "off" }), "Stored in OpenBao and never shown again."),
      field("Projects", h("input", { className: "input mono", value: form.projects, onChange: set("projects"), placeholder: "EDP, PAY" }), "Leave empty for every project the account can browse."),
      field("Sync", h("select", { className: "select", value: form.every_minutes, onChange: set("every_minutes") }, EVERY.map(([m, label]) => h("option", { key: m, value: m }, label)))),
      h("label", { className: "check connector-wide" }, h("input", { type: "checkbox", checked: form.company_site, onChange: set("company_site") }),
        h("span", null, h("b", null, "This Jira site is our company's own. "), "Then a project anyone in Jira may browse is readable by everyone in the org. Leave it off for a partner's or a vendor's site: its projects stay with the people Jira names.")),
      h("details", { className: "connector-wide" }, h("summary", null, "Use a secret the team keeps in OpenBao instead"),
        field("Credential reference", h("input", { className: "input mono", value: form.credential_ref, onChange: set("credential_ref"), placeholder: `openbao://secret/kl/acme/${ws}/connections/jira` }),
          "A secret with email and api_token, under this team's own connections path. Aerugo reads it; it never stores the token itself.")),
      h("div", { className: "actions connector-wide" },
        h("button", { type: "submit", className: "btn btn-primary", disabled: busy }, busy ? "Connecting…" : "Connect Jira"),
        h("button", { type: "button", className: "btn btn-ghost", onClick: () => setChosen(null) }, "Cancel"))),
    confluence: () => h("form", { className: "panel-body connector-form", onSubmit: connectConfluence, "aria-label": "Connect Confluence" },
      h("p", { className: "connector-lede" }, "Create a service account in Atlassian (or a dedicated user), give it view access to the spaces to index, and make it an API token. It only sets what's indexed: each page keeps Confluence's own readers, its space's narrowed by every restriction down the tree."),
      field("Name", h("input", { className: "input", value: cf.name, onChange: setC("name"), required: true, pattern: "[a-z0-9][a-z0-9-]{0,39}" })),
      field("Confluence site", h("input", { className: "input mono", value: cf.api_base, onChange: setC("api_base"), placeholder: "https://your-site.atlassian.net", required: true })),
      cf.credential_ref ? null : field("Account email", h("input", { className: "input", type: "email", value: cf.email, onChange: setC("email"), required: true, autoComplete: "off" })),
      cf.credential_ref ? null : field("API token", h("input", { className: "input mono", type: "password", value: cf.api_token, onChange: setC("api_token"), required: true, autoComplete: "off" }), "Stored in OpenBao and never shown again."),
      field("Spaces", h("input", { className: "input mono", value: cf.spaces, onChange: setC("spaces"), placeholder: "PAY, ENG" }), "Leave empty for every space the account can view."),
      field("Labels", h("input", { className: "input mono", value: cf.labels, onChange: setC("labels"), placeholder: "runbook, decision" }), "Only pages with one of these labels. Leave empty for every page."),
      field("Page trees", h("input", { className: "input mono", value: cf.page_trees, onChange: setC("page_trees"), placeholder: "123456789" }), "Only these pages and everything under them, by the number in a page's address."),
      field("Sync", h("select", { className: "select", value: cf.every_minutes, onChange: setC("every_minutes") }, EVERY.map(([m, label]) => h("option", { key: m, value: m }, label)))),
      h("label", { className: "check connector-wide" }, h("input", { type: "checkbox", checked: cf.company_site, onChange: setC("company_site") }),
        h("span", null, h("b", null, "This Confluence site is our company's own. "), "Then a space anyone in Confluence may view is readable by everyone in the org, and restrictions still narrow it. Leave it off for a partner's or a vendor's site.")),
      h("details", { className: "connector-wide" }, h("summary", null, "Use a secret the team keeps instead"),
        field("Credential reference", h("input", { className: "input mono", value: cf.credential_ref, onChange: setC("credential_ref"), placeholder: `openbao://secret/kl/acme/${ws}/connections/confluence` }),
          "A secret with email and api_token, under this team's own connections path, or a GCP Secret Manager secret named for this team. Aerugo reads it; it never stores the token itself.")),
      h("div", { className: "actions connector-wide" },
        h("button", { type: "submit", className: "btn btn-primary", disabled: busy }, busy ? "Connecting…" : "Connect Confluence"),
        h("button", { type: "button", className: "btn btn-ghost", onClick: () => setChosen(null) }, "Cancel"))),
    google: () => h("form", { className: "panel-body connector-form", onSubmit: connectDrive },
      h("p", { className: "connector-lede" }, "In Google Cloud, create a service account and a JSON key for it. Share folders with its email, or add it to shared drives: that's what gets indexed, even from a personal Google account. Each file keeps its own sharing."),
      field("Name", h("input", { className: "input", value: drive.name, onChange: setD("name"), required: true, pattern: "[a-z0-9][a-z0-9-]{0,39}" })),
      field("Company domain", h("input", { className: "input mono", value: drive.domain, onChange: setD("domain"), placeholder: "acme.com" }),
        "A file shared with everyone at this domain becomes readable by everyone in the org. Leave empty if it isn't yours."),
      drive.credential_ref ? null : h("label", { className: "field connector-wide" }, h("span", null, "Service account key"),
        h("textarea", { className: "input mono", rows: 4, value: drive.key, onChange: setD("key"), required: true, spellCheck: false, autoComplete: "off",
                        placeholder: '{"type": "service_account", "client_email": "...", "private_key": "..."}' }),
        h("span", { className: "hint" }, "Paste the JSON key. Only its email and private key are kept, in OpenBao; nothing else in it is used.")),
      field("Shared drives", h("input", { className: "input mono", value: drive.drives, onChange: setD("drives"), placeholder: "Optional drive ids" }),
        "Leave empty for every shared drive it's a member of. Folders shared with it are always included."),
      field("Sync", h("select", { className: "select", value: drive.every_minutes, onChange: setD("every_minutes") }, EVERY.map(([m, label]) => h("option", { key: m, value: m }, label)))),
      h("details", { className: "connector-wide" }, h("summary", null, "Use a key the team keeps in OpenBao instead"),
        field("Credential reference", h("input", { className: "input mono", value: drive.credential_ref, onChange: setD("credential_ref"), placeholder: `openbao://secret/kl/acme/${ws}/connections/drive` }),
          "A secret with service_account (its client_email and private_key), under this team's own connections path.")),
      h("div", { className: "actions connector-wide" },
        h("button", { type: "submit", className: "btn btn-primary", disabled: busy }, busy ? "Connecting…" : "Connect Drive"),
        h("button", { type: "button", className: "btn btn-ghost", onClick: () => setChosen(null) }, "Cancel"))),
    directory: () => !orgAdmin
      ? h("div", { className: "panel-body connector-form" },
          h("p", { className: "connector-lede" }, "Only an org admin connects a directory: it decides who has left, and who a group reaches, across the whole company. Ask an org admin to connect Okta or Google Workspace."))
      : h("form", { className: "panel-body connector-form", onSubmit: connectDirectory, "aria-label": "Connect a directory" },
      h("p", { className: "connector-lede" }, {
        google: "A directory adds no documents. Google Workspace tells Aerugo who's at the company, who has left, and who is in each group, so a Drive file shared with a group reaches its members. In Google Cloud, create a service account and a JSON key; then a super admin authorizes its client ID for domain-wide delegation with exactly the three read-only directory scopes.",
        entra: "A directory adds no documents. Microsoft Entra tells Aerugo who's at the company, who has left, and who is in each group, for the Microsoft 365 files shared with them. Register an app with the application permissions User.Read.All and GroupMember.Read.All (an admin consents), and upload its certificate: it signs in with that, never a client secret.",
      }[dir.provider] || "A directory adds no documents. It tells Aerugo who's at the company and who has left: when someone leaves, the access their email matched is revoked at once, without waiting for a sign-in that won't come. In Okta, create a read-only admin's API token."),
      field("Directory", h("select", { className: "select", value: dir.provider, onChange: setO("provider") },
        h("option", { value: "okta" }, "Okta"), h("option", { value: "google" }, "Google Workspace"), h("option", { value: "entra" }, "Microsoft Entra"))),
      field("Name", h("input", { className: "input", value: dir.name, onChange: setO("name"), required: true, pattern: "[a-z0-9][a-z0-9-]{0,39}" })),
      dir.provider === "entra" ? field("Tenant ID", h("input", { className: "input mono", value: dir.tenant_id, onChange: setO("tenant_id"), placeholder: "00000000-0000-0000-0000-000000000000", required: true, autoComplete: "off" })) : null,
      dir.provider === "entra" ? field("Application (client) ID", h("input", { className: "input mono", value: dir.client_id, onChange: setO("client_id"), placeholder: "00000000-0000-0000-0000-000000000000", required: true, autoComplete: "off" })) : null,
      dir.provider === "entra" ? null : dir.provider === "okta"
        ? field("Okta org", h("input", { className: "input mono", value: dir.api_base, onChange: setO("api_base"), placeholder: "https://your-org.okta.com", required: true }))
        : field("Admin to act as", h("input", { className: "input", type: "email", value: dir.admin_email, onChange: setO("admin_email"), placeholder: "directory-reader@acme.com", required: true, autoComplete: "off" }),
            "A dedicated admin with a read-only role for users and groups, in one of the company's domains."),
      dir.credential_ref ? null : dir.provider === "entra"
        ? h("label", { className: "field connector-wide" }, h("span", null, "Certificate"),
            h("textarea", { className: "input mono", rows: 4, value: dir.certificate, onChange: setO("certificate"), required: true, spellCheck: false, autoComplete: "off",
                            placeholder: "-----BEGIN PRIVATE KEY-----\n...\n-----BEGIN CERTIFICATE-----\n..." }),
            h("span", { className: "hint" }, "Paste the app's private key and its certificate (PEM). Kept in OpenBao and never shown again; only the certificate's public half is uploaded to Entra."))
        : dir.provider === "okta"
        ? field("API token", h("input", { className: "input mono", type: "password", value: dir.api_token, onChange: setO("api_token"), required: true, autoComplete: "off" }),
            "A read-only admin's token. Stored in OpenBao and never shown again.")
        : h("label", { className: "field connector-wide" }, h("span", null, "Service account key"),
            h("textarea", { className: "input mono", rows: 4, value: dir.key, onChange: setO("key"), required: true, spellCheck: false, autoComplete: "off",
                            placeholder: '{"type": "service_account", "client_email": "...", "private_key": "..."}' }),
            h("span", { className: "hint" }, "Paste the JSON key. Only its email and private key are kept, in OpenBao; nothing else in it is used.")),
      field("Sync", h("select", { className: "select", value: dir.every_minutes, onChange: setO("every_minutes") }, EVERY.map(([m, label]) => h("option", { key: m, value: m }, label))),
        "How quickly a leaver's access goes. Every 15 minutes is a good start."),
      h("details", { className: "connector-wide" }, h("summary", null, "Use a credential the team keeps in OpenBao instead"),
        field("Credential reference", h("input", { className: "input mono", value: dir.credential_ref, onChange: setO("credential_ref"), placeholder: `openbao://secret/kl/acme/${ws}/connections/${dir.provider}` }),
          { okta: "A secret with api_token, under this team's own connections path.",
            google: "A secret with service_account (its client_email and private_key), under this team's own connections path.",
            entra: "A secret with certificate (its private_key, certificate, and thumbprint), under this team's own connections path." }[dir.provider])),
      h("div", { className: "actions connector-wide" },
        h("button", { type: "submit", className: "btn btn-primary", disabled: busy }, busy ? "Connecting…" : { okta: "Connect Okta", google: "Connect Google Workspace", entra: "Connect Microsoft Entra" }[dir.provider]),
        h("button", { type: "button", className: "btn btn-ghost", onClick: () => setChosen(null) }, "Cancel"))),
    github: () => h("div", { className: "connector-steps" },
      h("ol", null,
        h("li", null, "Create the GitHub App once: ", h("code", null, "make github-app ORG=<your-org>"), ". It's a non-human identity; nothing runs on a person's token."),
        h("li", null, "Install it on the repositories to index. Each file keeps its repository's own readers."),
        h("li", null, "Find the installation here and connect it. Then route each repository to its team.")),
      h("div", { className: "actions" }, h("button", { type: "button", className: "btn btn-sm", onClick: findInstallations }, "Find installations")),
      installs === "loading" ? h(SkeletonRows, { rows: 1, widths: [40] })
        : installs ? (installs.length ? h("ul", { className: "connector-installs" }, installs.map((inst) => h("li", { key: inst.installation_id },
            h("span", null, h("b", null, inst.account), ` · ${inst.repositories.length} repositor${inst.repositories.length === 1 ? "y" : "ies"}`),
            h("button", { type: "button", className: "btn btn-sm btn-primary", onClick: () => connectGitHub(inst) }, "Connect"))))
          : h("p", { className: "muted" }, "No installations yet. Install the App on a repository, then look again.")) : null),
    imports: () => h("div", { className: "connector-steps" },
      h("p", null, "Export from Notion (Markdown & CSV) or Confluence (HTML), then upload the .zip in the Imports panel above. You name who may read the pages; nothing becomes readable by default.")),
    pipelines: () => h("div", { className: "connector-steps" },
      h("p", null, "A CI job pushes documents with its own short-lived sign-in token (GitHub Actions' OIDC), so no secret is stored anywhere. Register the pipeline with Terraform or the API, then push from CI:"),
      h("pre", null, `POST ${api}/documents\nAuthorization: Bearer <the job's OIDC token>\n{"upserts": [{"key": "runbook-1", "title": "...", "text": "...", "readers": ["group:${ws}"]}]}`)),
    lineage: () => h("div", { className: "connector-steps" },
      h("p", null, "Send OpenLineage run events (Airflow, dbt, Dataform, and Spark emit them): each names a job, what it read, and what it wrote. Metadata only, never data; only this team sees it."),
      h("pre", null, `POST ${api}/lineage\n{"events": [{"job": {"namespace": "airflow", "name": "orders_daily"},\n  "inputs": [{"namespace": "edw", "name": "ORDER_HIST"}],\n  "outputs": [{"namespace": "bigquery", "name": "edp.orders"}]}]}`)),
  };
  const open = (id) => { setChosen(chosen === id ? null : id); setError(null); };
  const repoList = (name) => (repos[name] === "loading" ? h(SkeletonRows, { rows: 1, widths: [30] })
    : repos[name].length ? h("div", { style: { display: "grid", gap: 8 } }, repos[name].map((r) => h("div", { key: r.external_id, className: "actions", style: { justifyContent: "space-between" } },
        h("span", { className: "mono" }, r.external_id, r.selected ? null : h("span", { className: "sub" }, " (not synced)")),
        h("label", { className: "check" }, "Team ",
          h("select", { className: "select", value: r.team, onChange: (e) => route(name, r.external_id, e.target.value) },
            [...new Set([r.team, ...teams])].map((t) => h("option", { key: t, value: t }, t)))))))
      : h("span", { className: "muted" }, "No repos discovered yet."));
  const tone = (s) => (s === "active" || s === "ready" ? "ok" : s === "error" ? "bad" : "idle");
  const editForm = (c) => {
    if (!edit) return h(SkeletonRows, { rows: 1, widths: [40] });
    const scope = SCOPES[c.kind];
    const current = Number(edit.every_minutes);
    const choices = current && !EVERY.some(([m]) => m === current) ? [...EVERY, [current, every(current)]] : EVERY; // keep a cadence set elsewhere
    return h("form", { className: "connector-form connection-edit", onSubmit: (e) => saveEdit(e, c), "aria-label": `Edit ${c.name}` },
      field("Sync", h("select", { className: "select", value: edit.every_minutes, onChange: (e) => setEdit({ ...edit, every_minutes: e.target.value }) },
        h("option", { value: "" }, "The deployment's default"), choices.map(([m, label]) => h("option", { key: m, value: String(m) }, label)))),
      scope ? field(scope[1], h("input", { className: "input mono", value: edit.scope, placeholder: scope[2], onChange: (e) => setEdit({ ...edit, scope: e.target.value }) }),
        "Comma-separated. It can change, not be removed: to read everything again, connect it again.") : null,
      h("div", { className: "actions connector-wide" },
        h("button", { type: "submit", className: "btn btn-primary btn-sm" }, "Save"),
        h("button", { type: "button", className: "btn btn-ghost btn-sm", onClick: () => setEditing(null) }, "Cancel")));
  };
  return h("section", { className: "panel", style: { marginTop: 24 } },
    h("div", { className: "panel-head" }, h("div", null, h("h2", null, "Connections"), h("p", null, "Sources synced into this team by a service account, each item with its source's own readers.")),
      h("button", { type: "button", className: "btn btn-sm", "aria-expanded": gallery, onClick: () => { setGallery(!gallery); setChosen(null); } },
        gallery ? "Close" : h("span", null, h(Icon, { name: "plus", size: 14 }), "Connect a source"))),
    gallery ? h("div", { className: "connector-grid", role: "list", "aria-label": "Sources you can connect" }, CONNECTORS.map((c) => h("div", {
      key: c.id, role: "listitem", className: `connector-card${c.ready ? "" : " later"}${chosen === c.id ? " on" : ""}` },
      h("h3", null, h(SourceIcon, { kind: c.id }), c.name), h("p", null, c.what),
      h("div", { className: "actions" }, c.ready
        ? h("button", { type: "button", className: `btn btn-sm${chosen === c.id ? " btn-primary" : ""}`, "aria-pressed": chosen === c.id, onClick: () => open(c.id) }, c.action)
        : h("span", { className: "tag" }, c.next ? "Coming next" : "Planned"))))) : null,
    gallery && chosen ? steps[chosen]() : null,
    error ? h("div", { style: { padding: 16 } }, h(Alert, { onRetry: load }, error)) : null,
    items === null && !error ? h(SkeletonRows, { rows: 2, widths: [30, 12, 12] })
      : items && items.length ? h(Table, null,
          h("thead", null, h("tr", null, h("th", null, "Connection"), h("th", null, "Status"), h("th", { className: "hide-sm" }, "Sync"),
            h("th", { className: "num hide-sm" }, "Permissions as of"), h("th", { className: "actions-cell" }, h("span", { className: "sr-only" }, "Actions")))),
          h("tbody", null, items.flatMap((c) => [h("tr", { key: c.name },
            h("td", null, h("div", { className: "title" }, c.name,
                c.managed_by ? h("span", { className: "tag tf-tag", title: managedTitle(c.managed_by) }, toolName(c.managed_by.by)) : null),
              h("div", { className: "sub" }, h(SourceIcon, { kind: c.kind }), " ", c.kind)),
            h("td", null, note[c.name] ? h(Status, { tone: note[c.name][0] }, note[c.name][1])
              : h(Status, { tone: tone(c.status) }, c.status.charAt(0).toUpperCase() + c.status.slice(1))),
            h("td", { className: "hide-sm" }, every(c.every_minutes)),
            h("td", { className: "num hide-sm", title: c.last_synced_at ? `Every sync re-reads each item's readers. Last: ${new Date(c.last_synced_at).toLocaleString()}` : "Not synced yet" },
              c.last_synced_at ? ago(c.last_synced_at) : "Not yet"),
            h("td", { className: "actions-cell" }, h("div", { className: "actions" },
              c.kind === "github" ? h("button", { type: "button", className: "btn btn-sm", onClick: () => showRepos(c.name) }, repos[c.name] ? "Hide repos" : "Repos") : null,
              h("button", { type: "button", className: "btn btn-sm", onClick: () => resync(c.name), disabled: c.status === "disabled" },
                h(Icon, { name: "refresh", size: 14 }), "Re-sync"),
              c.managed_by ? null : [ // what Terraform manages changes there, not here
                h("button", { key: "edit", type: "button", className: "btn btn-sm btn-ghost", "aria-expanded": editing === c.name, onClick: () => startEdit(c) }, "Edit"),
                h("button", { key: "pause", type: "button", className: "btn btn-sm btn-ghost", onClick: () => setEnabled(c.name, c.status === "disabled") }, c.status === "disabled" ? "Resume" : "Pause"),
                h("button", { key: "remove", type: "button", className: "btn btn-sm btn-ghost", onClick: () => setConfirming(c.name), "aria-label": `Remove ${c.name}` }, "Remove")]))),
            repos[c.name] ? h("tr", { key: `${c.name}-repos` }, h("td", { colSpan: 5 }, repoList(c.name))) : null,
            editing === c.name ? h("tr", { key: `${c.name}-edit` }, h("td", { colSpan: 5 }, editForm(c))) : null])))
        : items ? h(Empty, null, "No sources connected to this team yet. Connect one above.") : null,
    confirming ? h(Confirm, { title: `Remove the connection “${confirming}”?`, action: "Remove connection", onCancel: () => setConfirming(null),
                             onConfirm: () => remove(confirming) },
      "Its documents leave this team, and anything only it brought in leaves Aerugo, grants and all. Shared documents other connections hold stay.") : null);
}

function Audit({ ws }) {
  const [events, setEvents] = useState(null);
  const [next, setNext] = useState(undefined);
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const page = (before) => {
    setBusy(true);
    return kl(`audit?workspace=${enc(ws)}&limit=50${before ? `&before=${before}` : ""}`)
      .then((r) => { setEvents((e) => (before ? [...(e || []), ...r.events] : r.events)); setNext(r.next_before); setError(null); })
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false));
  };
  useEffect(() => { page(null); }, [ws]);
  const shown = events && filter ? events.filter((e) => `${e.action} ${e.actor}`.toLowerCase().includes(filter.toLowerCase())) : events;
  const summary = (d) => Object.entries(d || {}).slice(0, 4).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join("  ");

  return h("section", { className: "panel", style: { marginTop: 24 } },
    h("div", { className: "panel-head" }, h("div", null, h("h2", null, "Audit trail"), h("p", null, "Every read, change, and refusal in this team, newest first.")),
      h("div", { className: "searchbox sm", style: { width: 260 } }, h(Icon, { name: "search", size: 14 }),
        h("input", { value: filter, onChange: (e) => setFilter(e.target.value), placeholder: "Filter by action or person", "aria-label": "Filter the audit trail" }))),
    error ? h("div", { style: { padding: 16 } }, h(Alert, { onRetry: () => page(null) }, error)) : null,
    shown === null ? h(SkeletonRows, { rows: 6, widths: [14, 12, 18, 40] })
      : shown.length ? h("div", { className: "tbl-wrap" }, h("table", { className: "tbl fixed" },
          h("thead", null, h("tr", null, h("th", { className: "w-stamp" }, "Time"), h("th", { className: "w-person" }, "Actor"),
            h("th", { className: "w-action" }, "Action"), h("th", { className: "hide-sm" }, "Details"))),
          h("tbody", null, shown.map((e) => h("tr", { key: e.id },
            h("td", { className: "mono muted", title: new Date(e.at).toLocaleString(), style: { whiteSpace: "nowrap" } }, stamp(e.at)),
            h("td", null, e.actor),
            h("td", { className: "mono" }, e.action),
            h("td", { className: "hide-sm ellipsis mono muted" }, summary(e.details)))))))
        : h(Empty, null, filter ? `No events match “${filter}”.` : "No events yet."),
    next ? h("div", { className: "panel-foot" }, h("button", { type: "button", className: "btn btn-sm", disabled: busy, onClick: () => page(next) },
      busy ? h(Spinner) : null, "Load older")) : null);
}


// Documents that look like they hold personal data (slice I5), for the admins who can act on them: make one stricter
// (at once, and it stays so through later syncs) or dismiss the flag. Flags say which kinds were found, never the values.
const KINDS = { resume_layout: "résumé layout", government_id: "ID number", payment_card: "card number",
                date_of_birth: "date of birth", hr_terms: "HR terms", email: "email", phone: "phone" };

function PersonalData() {
  const [flags, setFlags] = useState(null);
  const [all, setAll] = useState(false);
  const [note, setNote] = useState(null);
  const [error, setError] = useState(null);
  const load = () => kl(`personal-data?state=${all ? "all" : "open"}`).then((r) => { setFlags(r.flags); setError(null); })
    .catch((e) => setError(e.message));
  useEffect(() => { load(); }, [all]);
  async function resolve(f, action) {
    setError(null);
    try {
      const r = await kl(`personal-data/${enc(f.id)}/resolve`, { method: "POST", body: { action } });
      setNote(action === "reclassify" ? `${f.title} is now ${r.classification}.` : `Dismissed the flag on ${f.title}.`);
      load();
    } catch (e) {
      setError(e.message);
    }
  }
  const found = (kinds) => Object.keys(KINDS).filter((k) => kinds[k]).map((k) => KINDS[k]).join(", ");
  return h("section", { className: "panel", style: { marginTop: 24 } },
    h("div", { className: "panel-head" }, h("div", null, h("h2", null, "Personal data"),
      h("p", null, "Documents that look like they hold personal data. Flags say which kinds were found, never the values.")),
      h("label", { className: "check" }, h("input", { type: "checkbox", checked: all, onChange: () => setAll(!all) }), "Show resolved")),
    note ? h("div", { style: { padding: "12px 16px" } }, h(Status, { tone: "ok" }, note)) : null,
    error ? h("div", { style: { padding: 16 } }, h(Alert, { onRetry: load }, error)) : null,
    flags === null && !error ? h(SkeletonRows, { rows: 2, widths: [30, 24, 16] })
      : flags && flags.length ? h(Table, null,
          h("thead", null, h("tr", null, h("th", null, "Document"), h("th", { className: "hide-sm" }, "Found"), h("th", null, "Classification"),
            h("th", { className: "actions-cell" }, h("span", { className: "sr-only" }, "Actions")))),
          h("tbody", null, flags.map((f) => h("tr", { key: f.id },
            h("td", { className: "ellipsis" }, h("div", { className: "title" }, f.title), h("div", { className: "sub mono" }, `${f.id} · ${f.workspaces.join(", ")}`)),
            h("td", { className: "hide-sm muted" }, found(f.kinds)),
            h("td", null, f.status === "open" ? `${f.classification}, ${f.suggested} suggested`
              : h(Status, { tone: f.status === "reclassified" ? "ok" : "idle" }, f.status === "reclassified" ? `Now ${f.classification}` : "Dismissed")),
            h("td", { className: "actions-cell" }, f.status === "open" ? h("div", { className: "actions" },
              h("button", { type: "button", className: "btn btn-sm", onClick: () => resolve(f, "reclassify") }, `Make ${f.suggested}`),
              h("button", { type: "button", className: "btn btn-sm", onClick: () => resolve(f, "dismiss") }, "Dismiss")) : null)))))
        : flags ? h(Empty, null, all ? "No personal-data flags yet." : "Nothing to review: no open personal-data flags.") : null);
}


// Imports (slice J2): a Confluence or Notion export becomes documents in this team, readable only by the readers picked
// here, since an export carries no permissions to trust. A newer export under the same name updates it.
function Imports({ ws }) {
  const [items, setItems] = useState(null);
  const [holders, setHolders] = useState([]); // who may read: the team's role holders, as the API requires
  const [form, setForm] = useState({ name: "", readers: [], classification: "internal", base_url: "" });
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  const [error, setError] = useState(null);
  const [confirming, setConfirming] = useState(null); // the import whose removal waits for a yes
  const load = () => kl(`workspaces/${enc(ws)}/imports`).then((r) => { setItems(r.imports); setError(null); }).catch((e) => setError(e.message));
  useEffect(() => {
    load();
    kl(`workspaces/${enc(ws)}/members`).then((r) => setHolders([...new Set(r.members.map((m) => m.principal))].sort())).catch(() => setHolders([]));
  }, [ws]);
  const waiting = items ? items.some((i) => i.last_run && i.last_run.status === "queued") : false;
  useEffect(() => { // while an import is being applied, keep checking on it
    if (!waiting) return undefined;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [waiting, ws]);
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: key === "name" ? e.target.value.toLowerCase() : e.target.value }));
  const toggle = (p) => setForm((f) => ({ ...f, readers: f.readers.includes(p) ? f.readers.filter((r) => r !== p) : [...f.readers, p] }));
  async function upload(e) {
    e.preventDefault();
    const formEl = e.target;
    setBusy(true);
    setNote(null);
    const data = new FormData();
    data.append("file", file);
    data.append("name", form.name.trim());
    data.append("readers", form.readers.join(","));
    data.append("classification", form.classification);
    if (form.base_url.trim()) data.append("base_url", form.base_url.trim());
    try {
      const r = await klUpload(`workspaces/${enc(ws)}/imports`, data);
      setNote(["ok", `${r.kind === "notion" ? "Notion" : "Confluence"} export queued as ${r.import}`]);
      setForm((f) => ({ ...f, name: "" }));
      setFile(null);
      formEl.reset(); // clears the file input
      load();
    } catch (err) {
      setNote(["bad", err.message]);
    } finally {
      setBusy(false);
    }
  }
  async function remove(name) {
    try {
      await kl(`workspaces/${enc(ws)}/imports/${enc(name)}`, { method: "DELETE" });
      setConfirming(null);
      load();
    } catch (err) {
      setConfirming(null);
      setError(err.message);
    }
  }
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const outcome = (run) => (!run ? h(Status, { tone: "idle" }, "No runs")
    : run.status === "queued" ? h(Status, { tone: "idle" }, "Importing…")
    : run.status === "failed" ? h(Status, { tone: "bad" }, `Failed: ${run.error || "unknown error"}`)
    : h(Status, { tone: "ok" }, `${plural(run.result.pages, "page")}, ${run.result.changed} changed, ${run.result.removed} removed`));
  const ready = file && /^[a-z0-9][a-z0-9-]{0,39}$/.test(form.name.trim()) && form.readers.length > 0 && !busy;
  return h("section", { className: "panel", style: { marginTop: 24 } },
    h("div", { className: "panel-head" }, h("div", null, h("h2", null, "Imports"),
      h("p", null, "Upload a Confluence HTML space export or a Notion Markdown & CSV export (a zip, up to 50 MB). An export carries no permissions, so pick who may read it. A newer export under the same name updates its pages and removes the ones it no longer has."))),
    h("form", { onSubmit: upload, style: { padding: 16, display: "grid", gap: 12 } },
      h("div", { className: "actions", style: { flexWrap: "wrap" } },
        h("input", { type: "file", accept: ".zip,application/zip", "aria-label": "Export (zip)", onChange: (e) => setFile(e.target.files[0] || null) }),
        h("input", { className: "input", placeholder: "Name, like eng-wiki", "aria-label": "Import name", value: form.name, onChange: set("name") }),
        h("select", { className: "select", "aria-label": "Classification", value: form.classification, onChange: set("classification") },
          CLASSES.map((c) => h("option", { key: c, value: c }, c))),
        h("input", { className: "input", placeholder: "Confluence site (optional)", "aria-label": "Confluence site address", value: form.base_url, onChange: set("base_url") })),
      h("div", { className: "actions", style: { flexWrap: "wrap" } }, h("span", { className: "muted" }, "Readers:"),
        holders.length ? holders.map((p) => h("label", { key: p, className: "check" },
          h("input", { type: "checkbox", checked: form.readers.includes(p), onChange: () => toggle(p) }), ` ${p}`))
          : h("span", { className: "muted" }, "This team has no one to name yet.")),
      h("div", { className: "actions" },
        h("button", { type: "submit", className: "btn btn-primary", disabled: !ready }, busy ? h(Spinner) : h(Icon, { name: "plus", size: 14 }), "Import"),
        note ? h(Status, { tone: note[0] }, note[1]) : null)),
    error ? h("div", { style: { padding: 16 } }, h(Alert, { onRetry: load }, error)) : null,
    items === null && !error ? h(SkeletonRows, { rows: 2, widths: [30, 20, 14] })
      : items && items.length ? h(Table, null,
          h("thead", null, h("tr", null, h("th", null, "Import"), h("th", { className: "hide-sm" }, "Readers"), h("th", null, "Last run"),
            h("th", { className: "actions-cell" }, h("span", { className: "sr-only" }, "Actions")))),
          h("tbody", null, items.map((i) => h("tr", { key: i.name },
            h("td", null, h("div", { className: "title" }, i.name), h("div", { className: "sub" }, h(SourceIcon, { kind: i.kind }), " ", `${sourceName(i.kind)} export, ${i.classification}`)),
            h("td", { className: "hide-sm mono" }, i.readers.join(", ")),
            h("td", null, outcome(i.last_run)),
            h("td", { className: "actions-cell" }, h("button", { type: "button", className: "btn btn-ghost btn-sm", onClick: () => setConfirming(i.name), "aria-label": `Remove ${i.name}` }, "Remove"))))))
        : items ? h(Empty, null, "Nothing imported into this team yet.") : null,
    confirming ? h(Confirm, { title: `Remove the import “${confirming}”?`, action: "Remove import", onCancel: () => setConfirming(null),
                              onConfirm: () => remove(confirming) },
      "Its pages leave search, the map, and every team, with their history. Your export file isn't affected: upload it again to bring them back.") : null);
}
