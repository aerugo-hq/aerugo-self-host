"use client";
// Use in Terraform (slice U1): each resource's ID, what manages it, and, for what was made by clicking, the
// configuration that adopts it, with import blocks so nothing is recreated. Secrets never appear: a model's key is a
// variable here, because the app never has it to show.
import { createElement as h, useState } from "react";

// HCL strings are JSON strings, except that ${ and %{ start templates: escaped, a name can never become code.
export const hcl = (v) => JSON.stringify(String(v)).replace(/\$\{/g, () => "$${").replace(/%\{/g, () => "%%{");
// (Replacer functions, not strings: in a replacement string "$$" means one "$", which would undo the escape.)

// A resource's local name in Terraform: lowercase letters, digits, and underscores, never starting with a digit.
export function local(...parts) {
  const name = parts.join("_").toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/_+/g, "_").replace(/^_+|_+$/g, "");
  return /^[a-z_]/.test(name) ? name : `r_${name}`;
}

export const toolName = (by) => ({ terraform: "Terraform", opentofu: "OpenTofu" }[by] || by);

function block(type, name, attrs) {
  const width = Math.max(...attrs.map(([key]) => key.length));
  return `resource "${type}" "${name}" {\n${attrs.map(([key, value]) => `  ${key.padEnd(width)} = ${value}`).join("\n")}\n}`;
}

export const importBlock = (type, name, id) => `import {\n  to = ${type}.${name}\n  id = ${hcl(id)}\n}`;

export function teamItem(team) {
  const name = local(team.slug);
  return { label: `Team ${team.slug}`, type: "aerugo_team", name, id: team.slug, managed: team.managed_by || null,
           hcl: block("aerugo_team", name, [["slug", hcl(team.slug)], ["name", hcl(team.name)], ["discoverability", hcl(team.discoverability)]]) };
}

export function memberItem(ws, grant) {
  const name = local(ws, grant.principal.replace(/^(user|group):/, ""), grant.role);
  return { label: `${grant.principal} as ${grant.role}`, type: "aerugo_team_member", name, id: `${ws}/${grant.principal}/${grant.role}`,
           managed: grant.managed_by || null,
           hcl: block("aerugo_team_member", name, [["team", hcl(ws)], ["principal", hcl(grant.principal)], ["role", hcl(grant.role)]]) };
}

export function modelItem(ws, m) {
  const name = local(ws, m.name);
  const attrs = [["team", hcl(ws)], ["name", hcl(m.name)], ["api", hcl(m.provider)], ["model", hcl(m.model)]];
  if (m.base_url) attrs.push(["base_url", hcl(m.base_url)]);
  attrs.push(["allowed_classifications", `[${(m.allowed_classifications || []).map(hcl).join(", ")}]`], ["api_key", `var.${name}_key`]);
  const variable = `variable "${name}_key" {\n  type      = string\n  sensitive = true\n  ephemeral = true # sent only when api_key_version changes: importing keeps the current key\n}`;
  return { label: `Model ${m.name}`, type: "aerugo_model", name, id: `${ws}/${m.name}`, managed: m.managed_by || null,
           hcl: `${variable}\n\n${block("aerugo_model", name, attrs)}` };
}

export function budgetItem(ws, m) {
  if (!m.monthly_token_budget) return null;
  const name = local(ws, m.name);
  return { label: `Budget for ${m.name}`, type: "aerugo_model_budget", name, id: `${ws}/${m.name}`, managed: m.budget_managed_by || null,
           hcl: block("aerugo_model_budget", name, [["team", hcl(ws)], ["model", hcl(m.name)], ["monthly_tokens", String(m.monthly_token_budget)]]) };
}

export function serviceAccountItem(a) {
  const name = local(a.client_id);
  return { label: `Service account ${a.client_id}`, type: "aerugo_service_account", name, id: a.client_id, managed: a.managed_by || null,
           hcl: block("aerugo_service_account", name, [["client_id", hcl(a.client_id)], ["name", hcl(a.name)],
                                                        ["enabled", String(a.enabled)], ["org_admin", String(a.org_admin)]]) };
}

// The configuration that adopts everything here Terraform doesn't manage yet: each resource, then its import block.
export function adoption(items) {
  return items.filter((i) => i && !i.managed).map((i) => `${i.hcl}\n\n${importBlock(i.type, i.name, i.id)}`).join("\n\n");
}

export function Copy({ text, label }) {
  const [done, setDone] = useState(false);
  const copy = () => navigator.clipboard.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); }).catch(() => {});
  return h("button", { type: "button", className: "btn btn-ghost btn-sm", onClick: copy, "aria-label": `Copy ${label}` }, done ? "Copied" : "Copy");
}

// A note on a resource a tool manages: changes made here will be undone, so they belong in that tool.
export function ManagedNote({ managed }) {
  if (!managed) return null;
  return h("div", { className: "managed-note", role: "note" },
    h("b", null, `Managed by ${toolName(managed.by)}`), managed.workspace ? h("span", { className: "mono" }, managed.workspace) : null,
    h("span", null, `Changes made here will be undone on its next apply. Make them in ${toolName(managed.by)} instead.`));
}

// IDs and Terraform: collapsed, so the page stays simple; opened, every ID with a copy button and what manages it,
// then the configuration that adopts the rest.
export function TerraformDetails({ items }) {
  const list = items.filter(Boolean);
  if (!list.length) return null;
  const left = list.filter((i) => !i.managed);
  const config = adoption(list);
  return h("details", { className: "tf-details" },
    h("summary", null, "IDs and Terraform",
      h("span", { className: "muted small" }, left.length ? ` · ${left.length} of ${list.length} not managed by Terraform yet` : " · all managed by Terraform")),
    h("div", { className: "tf-body" },
      h("table", { className: "tf-ids" },
        h("thead", null, h("tr", null, h("th", { scope: "col" }, "What"), h("th", { scope: "col" }, "ID"), h("th", { scope: "col" }, "Managed by"))),
        h("tbody", null, list.map((i) => h("tr", { key: `${i.type}:${i.id}` },
          h("td", null, i.label),
          h("td", null, h("span", { className: "tf-id" }, h("code", null, i.id), h(Copy, { text: i.id, label: `the ID of ${i.label}` }))),
          h("td", null, i.managed ? `${toolName(i.managed.by)}${i.managed.workspace ? ` (${i.managed.workspace})` : ""}` : "By hand, in the app"))))),
      left.length ? [
        h("p", { key: "how", className: "muted small" }, "To manage these with Terraform, add this to your configuration and run terraform plan. The import blocks adopt them as they are: nothing is recreated."),
        h("div", { key: "code", className: "code-block" }, h("pre", null, config), h(Copy, { text: config, label: "the Terraform configuration" })),
      ] : null));
}
