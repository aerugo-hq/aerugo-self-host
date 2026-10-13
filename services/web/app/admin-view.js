"use client";
// Admin: everything a team's admins manage. Personal-data flags, members, models (with budgets), connections (with
// each repo's team), imports of Confluence and Notion exports, and the audit trail. Org-wide settings live on the
// Compliance page.
import { createElement as h, useEffect, useState } from "react";
import { ago, kl, klUpload } from "../lib/client.js";
import { Icon } from "./icons.js";
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
    current === "sources" ? [team(h(Imports, { ws, key: `i-${ws}` })), ws ? h(Connections, { ws, teams, key: `c-${ws}` }) : null] : null,
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

function Connections({ ws, teams }) {
  const [items, setItems] = useState(null);
  const [note, setNote] = useState({});
  const [repos, setRepos] = useState({}); // connection name -> its repos, once shown
  const [error, setError] = useState(null);
  const load = () => kl(`connections?ws=${enc(ws)}`).then((r) => { setItems(r.connections); setError(null); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [ws]);
  async function resync(name) {
    setNote((n) => ({ ...n, [name]: ["idle", "Queuing…"] }));
    try {
      await kl(`connections/${enc(name)}/refresh?ws=${enc(ws)}`, { method: "POST" });
      setNote((n) => ({ ...n, [name]: ["ok", "Re-sync queued"] }));
    } catch (e) {
      setNote((n) => ({ ...n, [name]: ["bad", e.message] }));
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
    setNote((n) => ({ ...n, [name]: ["idle", "Moving…"] }));
    try {
      await kl(`workspaces/${enc(ws)}/connections/${enc(name)}/routes`, { method: "PUT", body: { repository, workspace: team === ws ? null : team } });
      setRepos((r) => ({ ...r, [name]: r[name].map((c) => (c.external_id === repository ? { ...c, team } : c)) }));
      setNote((n) => ({ ...n, [name]: ["ok", `${repository} moves to ${team} on its next sync`] }));
    } catch (e) {
      setNote((n) => ({ ...n, [name]: ["bad", e.message] }));
    }
  }
  const repoList = (name) => (repos[name] === "loading" ? h(SkeletonRows, { rows: 1, widths: [30] })
    : repos[name].length ? h("div", { style: { display: "grid", gap: 8 } }, repos[name].map((r) => h("div", { key: r.external_id, className: "actions", style: { justifyContent: "space-between" } },
        h("span", { className: "mono" }, r.external_id, r.selected ? null : h("span", { className: "sub" }, " (not synced)")),
        h("label", { className: "check" }, "Team ",
          h("select", { className: "select", value: r.team, onChange: (e) => route(name, r.external_id, e.target.value) },
            [...new Set([r.team, ...teams])].map((t) => h("option", { key: t, value: t }, t)))))))
      : h("span", { className: "muted" }, "No repos discovered yet."));
  return h("section", { className: "panel", style: { marginTop: 24 } },
    h("div", { className: "panel-head" }, h("div", null, h("h2", null, "Connections"), h("p", null, "Sources synced into this team, with their own permissions. Each repo can belong to its own team."))),
    error ? h("div", { style: { padding: 16 } }, h(Alert, { onRetry: load }, error)) : null,
    items === null && !error ? h(SkeletonRows, { rows: 2, widths: [30, 12, 12] })
      : items && items.length ? h(Table, null,
          h("thead", null, h("tr", null, h("th", null, "Connection"), h("th", null, "Status"), h("th", { className: "num hide-sm" }, "Updated"), h("th", { className: "actions-cell" }, h("span", { className: "sr-only" }, "Actions")))),
          h("tbody", null, items.flatMap((c) => [h("tr", { key: c.name },
            h("td", null, h("div", { className: "title" }, c.name), h("div", { className: "sub" }, c.kind)),
            h("td", null, note[c.name] ? h(Status, { tone: note[c.name][0] }, note[c.name][1])
              : h(Status, { tone: c.status === "active" ? "ok" : c.status === "error" ? "bad" : "idle" }, c.status.charAt(0).toUpperCase() + c.status.slice(1))),
            h("td", { className: "num hide-sm", title: new Date(c.updated_at).toLocaleString() }, ago(c.updated_at)),
            h("td", { className: "actions-cell" }, h("div", { className: "actions" },
              h("button", { type: "button", className: "btn btn-sm", onClick: () => showRepos(c.name) }, repos[c.name] ? "Hide repos" : "Repos"),
              h("button", { type: "button", className: "btn btn-sm", onClick: () => resync(c.name) },
                h(Icon, { name: "refresh", size: 14 }), "Re-sync")))),
            repos[c.name] ? h("tr", { key: `${c.name}-repos` }, h("td", { colSpan: 4 }, repoList(c.name))) : null])))
        : items ? h(Empty, null, "No sources connected to this team yet.") : null);
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
            h("td", null, h("div", { className: "title" }, i.name), h("div", { className: "sub" }, `${i.kind === "notion" ? "Notion" : "Confluence"} export, ${i.classification}`)),
            h("td", { className: "hide-sm mono" }, i.readers.join(", ")),
            h("td", null, outcome(i.last_run)),
            h("td", { className: "actions-cell" }, h("button", { type: "button", className: "btn btn-ghost btn-sm", onClick: () => setConfirming(i.name), "aria-label": `Remove ${i.name}` }, "Remove"))))))
        : items ? h(Empty, null, "Nothing imported into this team yet.") : null,
    confirming ? h(Confirm, { title: `Remove the import “${confirming}”?`, action: "Remove import", onCancel: () => setConfirming(null),
                              onConfirm: () => remove(confirming) },
      "Its pages leave search, the map, and every team, with their history. Your export file isn't affected: upload it again to bring them back.") : null);
}
