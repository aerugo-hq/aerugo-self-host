"use client";
// Knowledge health (E2, K10), a team's admins' page: each curated page that needs someone, the problem said in a
// sentence, and the three things an owner can do about it, right on its row. Filters narrow it by kind of problem.
import { createElement as h, useEffect, useState } from "react";
import { ago, kl } from "../lib/client.js";
import { Alert, Confirm, Empty, PageHead, SkeletonRows, Status, Table, Tier, concept } from "./ui.js";
import { inScope } from "./scope.js";

const enc = encodeURIComponent;
// Each kind of problem, its name in the filter, and the status tone it reads in (always as text beside the dot).
const KINDS = [["stale", "Stale", "warn"], ["conflicting", "Conflicting", "bad"], ["orphaned", "Orphaned", "idle"], ["ownerless", "No owner", "warn"]];
const TONE = Object.fromEntries(KINDS.map(([k, , t]) => [k, t]));
const DONE = { confirm: "Confirmed", update: "Update requested", retire: "Retired" };

export default function HealthView({ team, teamName }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [kind, setKind] = useState(""); // "": every page that needs someone
  const [note, setNote] = useState({}); // page id -> [tone, text], after an action
  const [retiring, setRetiring] = useState(null);
  const load = () => kl(`workspaces/${enc(team)}/health`).then((r) => { setData(r); setError(null); }).catch((e) => setError(e.message));
  useEffect(() => { setData(null); load(); }, [team]);

  async function act(page, action, extra = {}) {
    setNote((n) => ({ ...n, [page.id]: ["idle", "Saving…"] }));
    try {
      await kl(`workspaces/${enc(team)}/health/${enc(page.id)}`, { method: "POST", body: { action, ...extra } });
      setNote((n) => ({ ...n, [page.id]: ["ok", DONE[action]] }));
      load();
    } catch (e) {
      setNote((n) => ({ ...n, [page.id]: ["bad", e.message] }));
    }
  }

  const shown = data ? data.needing.filter((p) => !kind || p.issues.some((i) => i.kind === kind)) : [];
  return h("div", { className: "page health-page" },
    h(PageHead, { title: "Knowledge health",
                  lede: data ? (data.needing.length
                    ? `${data.needing.length} of ${teamName || team}'s ${data.pages} curated pages need someone.`
                    : `Every one of ${teamName || team}'s ${data.pages} curated pages is reviewed, owned, linked, and unambiguous.`)
                    : "What about this team's curated pages needs someone, and what you can do about it." }),
    error ? h(Alert, { onRetry: load }, error) : null,
    h("section", { className: "panel" },
      data && data.needing.length ? h("div", { className: "chips health-filters", role: "group", "aria-label": "Show pages that are" },
        h("button", { type: "button", className: "chip", "aria-pressed": !kind, onClick: () => setKind("") }, `All ${data.needing.length}`),
        KINDS.map(([k, label]) => h("button", { key: k, type: "button", className: "chip", "aria-pressed": kind === k, disabled: !data.counts[k],
                                                onClick: () => setKind(kind === k ? "" : k) }, `${label} ${data.counts[k]}`))) : null,
      data === null && !error ? h(SkeletonRows, { rows: 4, widths: [30, 45, 14] })
        : data && !shown.length ? h(Empty, { icon: "check", title: data.needing.length ? "Nothing of this kind" : "All current" },
            data.needing.length ? "No page has this problem. Show all to see the rest." : "When a page goes unreviewed, unowned, or unlinked, it shows up here.")
        : data ? h(Table, null,
            h("thead", null, h("tr", null, h("th", null, "Page"), h("th", null, "What needs doing"), h("th", { className: "hide-sm" }, "Last review"),
              h("th", { className: "actions-cell" }, h("span", { className: "sr-only" }, "Actions")))),
            h("tbody", null, shown.map((p) => h("tr", { key: p.id },
              h("td", null, h("a", { className: "title", href: inScope(concept(p.id), team) }, p.title), h("div", { className: "sub" }, h(Tier, { tier: p.tier }))),
              h("td", null, h("ul", { className: "health-issues" },
                p.retired ? h("li", null, h(Status, { tone: "idle" }, "Retired: left out of search and Ask until it's confirmed.")) : null,
                p.update_requested ? h("li", null, h(Status, { tone: "info" }, `${p.update_requested.by} asked for an update ${ago(p.update_requested.at)}`
                  + (p.update_requested.note ? `: ${p.update_requested.note}` : "."))) : null,
                p.issues.map((i, n) => h("li", { key: n }, h(Status, { tone: TONE[i.kind] }, i.text))))),
              h("td", { className: "hide-sm muted" }, p.last_review ? `${DONE[p.last_review.action]} by ${p.last_review.by}, ${ago(p.last_review.at)}` : "Never"),
              h("td", { className: "actions-cell" },
                note[p.id] ? h(Status, { tone: note[p.id][0] }, note[p.id][1]) : null,
                h("div", { className: "actions" },
                  h("button", { type: "button", className: "btn btn-sm", onClick: () => act(p, "confirm"), title: "It's right as it is: count it as reviewed now" },
                    p.retired ? "Restore" : "Confirm"),
                  h("button", { type: "button", className: "btn btn-sm btn-ghost", onClick: () => act(p, "update"), title: "Ask its owners to change it" }, "Request update"),
                  p.retired ? null : h("button", { type: "button", className: "btn btn-sm btn-ghost", onClick: () => setRetiring(p) }, "Retire")))))))
        : null),
    data && data.reviews.length ? h("section", { className: "panel" },
      h("div", { className: "panel-head" }, h("div", null, h("h2", null, "Recent reviews"), h("p", null, "Who confirmed, asked for updates to, or retired which page."))),
      h("ul", { className: "health-log" }, data.reviews.map((r, n) => h("li", { key: n },
        h("b", null, DONE[r.action]), ` ${r.title} by ${r.by}, ${ago(r.at)}`, r.note ? h("span", { className: "muted" }, `: ${r.note}`) : null)))) : null,
    retiring ? h(Confirm, { title: `Retire “${retiring.title}”?`, action: "Retire page", onCancel: () => setRetiring(null),
                            onConfirm: () => { const p = retiring; setRetiring(null); act(p, "retire"); } },
      "Search and Ask leave it out until someone confirms it again. Its source isn't changed: remove it there to delete it.") : null);
}
