"use client";
// Home (slices R3 and N1): where you start. At the org: your teams, with what you can open in each and when it last
// brought knowledge in, each a way into its own scope; your recent conversations; and, if you run a team, what needs
// you. In a team's scope, the same for that team alone: its Overview. Every count is what you can open, nothing more.
// Teams are a dense table, as in Datavine (slice D3): a card only when the card itself is the interaction.
import { createElement as h, useEffect, useState } from "react";
import { ago, kl } from "../lib/client.js";
import { Icon } from "./icons.js";
import { at, inScope } from "./scope.js";
import { Alert, Empty, SkeletonRows, Status, Table } from "./ui.js";

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

export default function HomeView({ me, alerts = 0, team = "", teamName = "" }) {
  const [home, setHome] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { kl("home").then(setHome).catch((e) => setError(e.message)); }, []);
  const teams = home ? home.teams.filter((t) => !team || t.slug === team) : null;
  const failing = teams ? teams.filter((t) => t.failed_runs) : [];
  const failed = failing.reduce((n, t) => n + t.failed_runs, 0);
  const admin = Boolean(me && (me.admin_of.length || me.org_admin));
  const here = (path) => inScope(path, team);

  return h("div", { className: "page" },
    h("div", { className: "page-head" }, h("div", null, h("h1", null, team ? teamName || team : "Home"),
      h("p", { className: "lede" }, team ? "This team's knowledge, and what needs you." : "What your teams know, and what needs you."))),
    h("div", { className: "actions home-actions" },
      h("a", { className: "btn btn-primary", href: here("#/ask") }, h(Icon, { name: "ask", size: 14 }), team ? "Ask this team" : "Ask a question"),
      h("a", { className: "btn", href: here("#/documents") }, h(Icon, { name: "docs", size: 14 }), team ? "Open its space" : "Browse documents"),
      h("a", { className: "btn btn-ghost", href: here("#/map") }, h(Icon, { name: "map", size: 14 }), "Open the map")),
    error ? h(Alert, null, error) : null,
    admin && (alerts || failed) ? h("section", { className: "panel attention", "aria-labelledby": "attention-h" },
      h("h2", { id: "attention-h" }, "Needs you"),
      h("ul", { className: "attention-list" },
        alerts ? h("li", null, h(Status, { tone: "bad" }, `${plural(alerts, "alert")} firing`), h("a", { className: "a", href: "#/alerts" }, "See alerts")) : null,
        failed ? h("li", null, h(Status, { tone: "warn" }, plural(failed, "failed run")),
          h("a", { className: "a", href: at(failing[0].slug, "settings", "pipeline") }, "See the pipeline")) : null)) : null,
    h("section", { "aria-labelledby": "teams-h" },
      h("h2", { id: "teams-h", className: "section-title" }, team ? "This team" : "Your teams"),
      teams === null ? h("div", { className: "panel" }, h(SkeletonRows, { rows: 3, widths: [30, 12, 20, 12] }))
        : !teams.length ? h("div", { className: "panel" }, h(Empty, { icon: "home", title: team ? "Nothing here for you yet" : "No teams yet" },
            team ? "Nothing of this team for you to open yet." : "You aren't in a team yet. Ask a team's admin to add you."))
          : h("div", { className: "panel" }, h(Table, null,
              h("thead", null, h("tr", null, h("th", null, "Team"), h("th", { className: "num" }, "Documents you can open"),
                h("th", { className: "hide-sm" }, "Last brought in"), h("th", null, "Role"), admin ? h("th", null, "Pipeline") : null)),
              h("tbody", null, teams.map((t) => h("tr", { key: t.slug },
                h("td", { className: "ellipsis" }, h("a", { className: "title-link", href: at(t.slug, "home") }, t.slug)),
                h("td", { className: "num" }, h("a", { className: "a", href: at(t.slug, "documents") }, t.documents.toLocaleString())),
                h("td", { className: "hide-sm muted" }, t.last_sync ? ago(t.last_sync) : "Nothing brought in yet"),
                h("td", null, t.role === "admin" ? "Admin" : "Member"),
                admin ? h("td", null, t.role !== "admin" ? h("span", { className: "faint" }, "Not yours to run")
                  : t.failed_runs ? h("a", { href: at(t.slug, "settings", "pipeline") }, h(Status, { tone: "bad" }, plural(t.failed_runs, "failed run")))
                    : h(Status, { tone: "ok" }, "Healthy")) : null)))))),
    h("section", { "aria-labelledby": "recent-h" },
      h("h2", { id: "recent-h", className: "section-title" }, "Recent conversations"),
      home === null ? h(SkeletonRows, { rows: 3, widths: [60, 50, 70] })
        : !home.recent_conversations.length ? h(Empty, null, "No conversations yet. Ask a question to start one.")
          : h("ul", { className: "recent-list" }, home.recent_conversations.map((c) => h("li", { key: c.id },
              h("a", { href: here(`#/ask/${c.id}`) }, h("span", null, c.title), h("small", { className: "muted" }, ago(c.updated_at))))))));
}
