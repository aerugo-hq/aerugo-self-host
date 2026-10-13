"use client";
// Developer (slice R4): how to reach your teams' knowledge from code and from AI assistants, and every endpoint the API
// has, read from the API's own description. Reads run here with your session, so they answer with what you can see.
import { createElement as h, useEffect, useState } from "react";
import { kl } from "../lib/client.js";
import { Alert, Confirm, Empty, SkeletonRows, Status, Table } from "./ui.js";
import { TerraformDetails, serviceAccountItem } from "./terraform.js";

const enc = encodeURIComponent;

// This development setup's addresses; a deployment publishes its own.
export const DEV_API = "http://localhost:8000"; // KL_API_PORT in docker-compose.yml
export const DEV_MCP = "http://localhost:8090/mcp";
export const MCP_CONFIG = JSON.stringify({ mcpServers: { "aerugo": { url: DEV_MCP } } }, null, 2);
export const CURL = `curl -H "Authorization: Bearer $TOKEN" \\\n  "${DEV_API}/v1/search?q=runbook"`;

const METHODS = ["get", "post", "put", "patch", "delete"];
export const firstSentence = (s) => {
  const text = (s || "").replace(/\s+/g, " ").trim();
  const m = text.match(/^.*?[.!?](?=\s|$)/);
  return m ? m[0] : text;
};

// Every operation in an OpenAPI description, grouped by the part of the API it belongs to. Only reads under /v1 with
// no path parameters run from here: nothing on this page can change anything.
export function operations(spec) {
  const out = [];
  for (const [path, item] of Object.entries((spec && spec.paths) || {})) {
    for (const [method, op] of Object.entries(item)) {
      if (!METHODS.includes(method)) continue;
      out.push({ method: method.toUpperCase(), path, group: path.split("/")[2] || "other", summary: firstSentence(op.description || op.summary),
                 runnable: method === "get" && path.startsWith("/v1/") && !path.includes("{") });
    }
  }
  const order = (o) => METHODS.indexOf(o.method.toLowerCase());
  return out.sort((a, b) => a.group.localeCompare(b.group) || a.path.localeCompare(b.path) || order(a) - order(b));
}

function Copy({ text, label }) {
  const [done, setDone] = useState(false);
  const copy = () => navigator.clipboard.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); }).catch(() => {});
  return h("button", { type: "button", className: "btn btn-ghost btn-sm", onClick: copy, "aria-label": `Copy ${label}` }, done ? "Copied" : "Copy");
}

function Code({ text, label }) {
  return h("div", { className: "code-block" }, h("pre", null, text), label ? h(Copy, { text, label }) : null);
}

function Operation({ op }) {
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  async function run() {
    setBusy(true);
    try {
      setResult({ ok: true, text: JSON.stringify(await kl(op.path.replace(/^\/v1\//, "")), null, 2) });
    } catch (e) {
      setResult({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  }
  const shown = result && result.text.length > 4000 ? `${result.text.slice(0, 4000)}\n…` : result && result.text;
  return h("li", null, h("div", { className: "op-line" },
    h("span", { className: `method ${op.method.toLowerCase()}` }, op.method),
    h("span", { className: "op-path" }, op.path),
    op.runnable ? h("button", { type: "button", className: "btn btn-sm", disabled: busy, onClick: run, "aria-label": `Run GET ${op.path}` }, busy ? "Running…" : "Run")
      : h("span", null),
    op.summary ? h("p", { className: "op-summary" }, op.summary) : null,
    result ? h("div", { className: "op-result" }, result.ok ? null : h(Alert, null, "It answered with an error."), h(Code, { text: shown })) : null));
}

// Service accounts (slices T1 and U1), for org admins: machine identities, such as Terraform or a CI pipeline, that may
// call the API. Each is an OAuth client from the org's identity provider, registered here and given only the roles it
// needs; it reads no documents unless granted.
export function ServiceAccounts() {
  const blank = { client_id: "", name: "", org_admin: false };
  const [accounts, setAccounts] = useState(null);
  const [form, setForm] = useState(blank);
  const [error, setError] = useState(null);
  const [confirming, setConfirming] = useState(null);
  const load = () => kl("service-accounts").then((r) => setAccounts(r.service_accounts)).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);
  async function act(work) {
    setError(null);
    try {
      await work();
      await load();
    } catch (e) {
      setError(e.message);
    }
  }
  const register = (e) => {
    e.preventDefault();
    act(async () => {
      await kl("service-accounts", { method: "POST", body: { client_id: form.client_id.trim(), name: form.name.trim(), org_admin: form.org_admin } });
      setForm(blank);
    });
  };
  const toggle = (a) => act(() => kl(`service-accounts/${enc(a.client_id)}`, { method: "PATCH", body: { enabled: !a.enabled } }));
  const remove = (a) => act(async () => {
    setConfirming(null);
    await kl(`service-accounts/${enc(a.client_id)}`, { method: "DELETE" });
  });
  const ready = form.client_id.trim() && form.name.trim();
  return h("section", { className: "panel", "aria-labelledby": "sa-h" },
    h("div", { className: "panel-head" }, h("div", null, h("h2", { id: "sa-h" }, "Service accounts"),
      h("p", null, "Machine identities, such as Terraform or a CI pipeline. Create an OAuth client in your identity provider, register it here, then give it team roles under Admin, People."))),
    h("form", { className: "panel-body inline-form", onSubmit: register, style: { borderBottom: "1px solid var(--border)" } },
      h("input", { className: "input mono", value: form.client_id, onChange: (e) => setForm({ ...form, client_id: e.target.value }),
                   placeholder: "Client ID, such as terraform", "aria-label": "Client ID" }),
      h("input", { className: "input", value: form.name, onChange: (e) => setForm({ ...form, name: e.target.value }),
                   placeholder: "What it's for", "aria-label": "What it's for" }),
      h("label", { className: "check" }, h("input", { type: "checkbox", checked: form.org_admin, onChange: (e) => setForm({ ...form, org_admin: e.target.checked }) }),
        "Org admin"),
      h("button", { className: `btn ${ready ? "btn-primary" : ""}`, disabled: !ready }, "Register")),
    error ? h("div", { style: { padding: 16 } }, h(Alert, null, error)) : null,
    accounts === null ? (error ? null : h(SkeletonRows, { rows: 2, widths: [40, 30] }))
      : !accounts.length ? h(Empty, null, "No service accounts yet. Terraform needs one to sign in.")
        : h(Table, null,
            h("thead", null, h("tr", null, h("th", null, "Account"), h("th", { className: "hide-sm" }, "Give roles to"), h("th", null, "Status"),
              h("th", { className: "actions-cell" }, h("span", { className: "sr-only" }, "Actions")))),
            h("tbody", null, accounts.map((a) => h("tr", { key: a.client_id },
              h("td", { className: "ellipsis" }, h("div", { className: "title" }, a.name), h("div", { className: "sub mono" }, a.client_id)),
              h("td", { className: "mono hide-sm" }, a.principal),
              h("td", null, h(Status, { tone: a.enabled ? "ok" : "idle" }, a.enabled ? (a.org_admin ? "Enabled, org admin" : "Enabled") : "Disabled")),
              h("td", { className: "actions-cell" }, h("div", { className: "actions" },
                h("button", { type: "button", className: "btn btn-sm", onClick: () => toggle(a) }, a.enabled ? "Disable" : "Enable"),
                h("button", { type: "button", className: "btn btn-ghost btn-sm", onClick: () => setConfirming(a), "aria-label": `Remove ${a.client_id}` }, "Remove"))))))),
    accounts && accounts.length ? h(TerraformDetails, { items: accounts.map(serviceAccountItem) }) : null,
    confirming ? h(Confirm, { title: `Remove ${confirming.client_id}?`, action: "Remove service account", onCancel: () => setConfirming(null),
                              onConfirm: () => remove(confirming) },
      "Its tokens stop working on their next request, and every role it holds is removed.") : null);
}

export default function DeveloperView({ me }) {
  const [spec, setSpec] = useState(null);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("");
  useEffect(() => { kl("openapi.json").then(setSpec).catch((e) => setError(e.message)); }, []);
  const needle = filter.trim().toLowerCase();
  const ops = spec ? operations(spec).filter((o) => !needle || `${o.method} ${o.path} ${o.summary}`.toLowerCase().includes(needle)) : [];
  const groups = [...new Set(ops.map((o) => o.group))];
  return h("div", { className: "page narrow" },
    h("div", { className: "page-head" }, h("div", null, h("h1", null, "Developer"),
      h("p", { className: "lede" }, "Reach your teams' knowledge from code and from AI assistants. Every call answers with the caller's own permissions."))),
    h("section", { className: "panel", "aria-labelledby": "mcp-h" },
      h("div", { className: "panel-head" }, h("h2", { id: "mcp-h" }, "Connect an AI assistant")),
      h("p", null, "The MCP server gives assistants such as Claude the same search and ask you have here, limited to what you can open. The assistant signs you in through your identity provider."),
      h(Code, { text: MCP_CONFIG, label: "the MCP configuration" }),
      h("p", { className: "muted small" }, "That's this development setup's address. A deployment publishes its own.")),
    h("section", { className: "panel", "aria-labelledby": "api-h" },
      h("div", { className: "panel-head" }, h("h2", { id: "api-h" }, "Call the API")),
      h("p", null, "Send a bearer token from your identity provider, issued for the kl-api audience. Every endpoint below is reached the same way."),
      h(Code, { text: CURL, label: "the example request" })),
    me && me.org_admin ? h(ServiceAccounts) : null,
    h("section", { "aria-labelledby": "ops-h" },
      h("div", { className: "page-head" }, h("h2", { id: "ops-h", className: "section-title" }, "Endpoints"),
        h("input", { className: "input", type: "search", value: filter, onChange: (e) => setFilter(e.target.value), placeholder: "Filter endpoints", "aria-label": "Filter endpoints" })),
      error ? h(Alert, null, error) : null,
      spec === null ? (error ? null : h(SkeletonRows, { rows: 6, widths: [20, 60, 40] }))
        : !ops.length ? h(Empty, null, "No endpoint matches that.")
          : groups.map((g) => h("div", { key: g, className: "ops-group" }, h("h3", null, g),
              h("ul", { className: "ops" }, ops.filter((o) => o.group === g).map((o) => h(Operation, { key: `${o.method} ${o.path}`, op: o })))))));
}
