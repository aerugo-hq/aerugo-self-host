"use client";
// The shell (slices N1 and D1, after Datavine): a rail of icons that opens over the page while you're in it, a thin top
// bar (where you are, search, health, you), and one view at a time from the address. On phones the rail is a drawer.
// Every page lives in a scope, as in Harness: the org's (#/<page>) or a team's (#/t/<team>/<page>). The scope picker at
// the top of the sidebar switches it, and every scoped page follows: in a team, Overview, Documents (its space), Ask,
// Search, Map, and Proposals show that team only, and its admins get Team settings. Org pages (Admin across teams,
// Alerts, Compliance, Developer, Connected accounts) stay at the org. ⌘K opens the command palette over any of them.
import { createElement as h, useEffect, useState } from "react";
import { kl } from "../lib/client.js";
import AccountsView from "./accounts-view.js";
import AdminView from "./admin-view.js";
import AlertsView from "./alerts-view.js";
import AskView from "./ask-view.js";
import ComplianceView from "./compliance-view.js";
import ConceptView from "./concept-view.js";
import DeveloperView from "./developer-view.js";
import DocumentsView, { SpaceView } from "./documents-view.js";
import HomeView from "./home-view.js";
import { Icon } from "./icons.js";
import MapView from "./map-view.js";
import Palette from "./palette.js";
import ProposalsView from "./proposals-view.js";
import { SCOPED, at, route } from "./scope.js";
import SearchView from "./search-view.js";
import { THEMES, applyTheme, themeFrom } from "./theme.js";
import { Empty, Status } from "./ui.js";

// G then a letter, from anywhere but a text field: the same places the sidebar goes, in the scope you're in.
const GO = { h: "home", m: "map", s: "search", q: "ask", d: "documents", p: "proposals", a: "admin", c: "compliance", v: "developer", l: "accounts" };
const TEAM_PAGES = { home: "Overview", documents: "Documents", ask: "Ask", search: "Search", map: "Map", proposals: "Proposals", settings: "Team settings", concepts: "Document" };
const ORG_PAGES = { home: "Home", documents: "Documents", ask: "Ask", search: "Search", map: "Map", proposals: "Proposals", admin: "Admin", alerts: "Alerts",
                    compliance: "Compliance", developer: "Developer", accounts: "Connected accounts", concepts: "Document" };

const initials = (name) => name.split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("");

// "/" opens search from anywhere, in the scope you're in, and puts the cursor in the box.
export function focusSearch() {
  const { team, view } = route();
  if (view !== "search") window.location.hash = at(team, "search");
  setTimeout(() => window.dispatchEvent(new Event("kl:focus-search")), 0);
}

// The scope picker, after Datavine's project selector: a tile, the scope's name, and the org it's in. Switching keeps
// you on the same page where that page has a scope.
export function ScopePicker({ me, names, team, view }) {
  if (!me) return null;
  const label = team ? names[team] || team : "All teams";
  const target = (t) => at(t, SCOPED.has(view) ? view : "home");
  return h("details", { className: "scope" },
    h("summary", { "aria-label": `Scope: ${label}. Switch to another team, or the whole org` },
      h("span", { className: "scope-tile", "aria-hidden": true }, team ? initials(label) : "All"),
      h("span", { className: "scope-who sb-fade" }, h("span", { className: "scope-name" }, label),
        h("span", { className: "scope-org" }, team ? "acme · team" : "acme · every team")),
      h("span", { className: "scope-chev sb-fade", "aria-hidden": true }, h(Icon, { name: "chevron", size: 14 }))),
    h("div", { className: "scope-menu" },
      h("a", { href: target(""), "aria-current": team ? undefined : "true" }, h("b", null, "All teams"), h("small", null, "Everything you can see across acme")),
      me.workspaces.length ? h("div", { className: "scope-label" }, "Your teams") : null,
      ...me.workspaces.map((ws) => h("a", { key: ws, href: target(ws), "aria-current": team === ws ? "true" : undefined },
        h("b", null, names[ws] || ws), h("small", { className: "mono" }, ws)))));
}

// Where you are, in the top bar: the org, the team, the page. Every crumb but the last goes back up.
export function Crumbs({ team, names, view }) {
  if (!team && view === "home") return null;
  const label = (team ? TEAM_PAGES : ORG_PAGES)[view] || view;
  return h("nav", { className: "crumbs-bar", "aria-label": "Breadcrumb" },
    h("a", { href: at("", "home") }, "acme"),
    team ? [h("span", { key: "s", "aria-hidden": true }, "›"), h("a", { key: "t", href: at(team, "home") }, names[team] || team)] : null,
    h("span", { "aria-hidden": true }, "›"), h("span", { "aria-current": "page" }, label));
}

// You, at the top bar's end: who you're signed in as, the theme, the shortcuts, and signing out.
export function Account({ me, onHelp }) {
  const [theme, setTheme] = useState(null);
  const current = theme || (typeof document === "undefined" ? "light" : themeFrom(document.cookie));
  return h("details", { className: "tb-account" },
    h("summary", { "aria-label": `Account: ${me.username}` }, h("span", { className: "avatar", "aria-hidden": true }, initials(me.username))),
    h("div", { className: "tb-menu" },
      h("div", { className: "tb-who" }, h("b", null, me.username),
        h("small", { title: me.workspaces.join(", ") }, me.workspaces.length ? me.workspaces.join(", ") : "No teams yet")),
      h("div", { className: "tb-theme" }, h("span", null, "Theme"),
        h("div", { className: "seg", role: "group", "aria-label": "Theme" }, THEMES.map(([value, label]) => h("button", {
          key: value, type: "button", "aria-pressed": current === value, onClick: () => { applyTheme(value); setTheme(value); },
        }, label)))),
      h("a", { href: "#", onClick: (e) => { e.preventDefault(); onHelp(); } }, h(Icon, { name: "keyboard", size: 14 }), "Keyboard shortcuts"),
      h("a", { href: "/auth/logout" }, h(Icon, { name: "logout", size: 14 }), "Sign out")));
}

export default function App() {
  const [where, setWhere] = useState({ team: "", view: "", arg: "" }); // nothing until the address is read, so the server's HTML matches
  const [me, setMe] = useState(null);
  const [names, setNames] = useState({}); // team slug -> its name, for the picker and the breadcrumb
  const [open, setOpen] = useState(false); // the drawer on phones
  const [mac, setMac] = useState(false);
  const [alerts, setAlerts] = useState(0); // firing alerts this admin can act on: the sidebar's badge
  const [palette, setPalette] = useState(null); // the command palette: "commands", "help" (the shortcuts), or closed
  const [status, setStatus] = useState(null); // the health indicator: the API's version and which parts are up
  useEffect(() => {
    if (!me) return undefined;
    const check = () => kl("status").then(setStatus).catch(() => setStatus({ ok: false, checks: {}, version: null }));
    check();
    const timer = setInterval(check, 60000);
    kl("workspaces").then((r) => setNames(Object.fromEntries(r.workspaces.map((w) => [w.slug, w.name])))).catch(() => {});
    return () => clearInterval(timer);
  }, [me]);
  const down = status && !status.ok ? Object.entries(status.checks).filter(([, up]) => !up).map(([name]) => name.replace("_", " ")) : [];

  useEffect(() => {
    const update = () => { setWhere(route()); setOpen(false); };
    let g = 0; // when G was pressed: G then a letter goes somewhere
    const typing = (el) => el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
    const keys = (e) => {
      if (e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setPalette((p) => (p ? null : "commands"));
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target) || document.querySelector(".overlay")) return;
      if (e.key === "Escape") { setOpen(false); return; }  // closes the drawer on phones
      if (e.key === "/") { e.preventDefault(); focusSearch(); return; }
      if (e.key === "?") { e.preventDefault(); setPalette("help"); return; }
      const key = e.key.toLowerCase();
      if (key === "g") { g = Date.now(); return; }
      if (Date.now() - g < 1200 && GO[key]) {
        e.preventDefault();
        window.location.hash = at(SCOPED.has(GO[key]) ? route().team : "", GO[key]);
      }
      g = 0;
    };
    update();
    setMac(/Mac|iPhone|iPad/.test(navigator.platform));
    window.addEventListener("hashchange", update);
    window.addEventListener("keydown", keys);
    kl("whoami").then(setMe).catch(() => {});
    return () => {
      window.removeEventListener("hashchange", update);
      window.removeEventListener("keydown", keys);
    };
  }, []);

  // Admins see how many alerts are firing, refreshed each minute (a badge poll, not a render loop).
  useEffect(() => {
    if (!me || !(me.admin_of.length || me.org_admin)) return undefined;
    const count = () => kl("alerts").then((d) => setAlerts(d.firing)).catch(() => {});
    count();
    const timer = setInterval(count, 60000);
    return () => clearInterval(timer);
  }, [me]);

  const team = where.team;
  const item = (view, icon, label, scope = team) => h("a", {
    href: at(scope, view), className: "sb-item",
    "aria-current": scope === team && (where.view === view || (view === "map" && where.view === "concepts")) ? "page" : undefined,
    title: label, // the rail shows only icons until it opens
  }, h(Icon, { name: icon }), h("span", { className: "sb-text" }, label));
  const group = (label) => h("div", { className: "sb-label", "aria-hidden": true }, label);
  const admin = me && (me.admin_of.length || me.org_admin);
  const teamAdmin = Boolean(me && team && (me.admin_of.includes(team) || me.org_admin));
  const outside = Boolean(me && team && !me.workspaces.includes(team));
  const teamName = names[team] || team;

  const teamPage = () => {
    if (outside) {
      return h("div", { className: "page" }, h("div", { className: "panel" },
        h(Empty, { action: h("a", { className: "btn btn-sm", href: at("", "home") }, "Go to all teams") },
          "You aren't in this team, so there's nothing of it to show you here.")));
    }
    switch (where.view) {
      case "documents": return h(SpaceView, { me, team, teamName, doc: where.arg }); // a document, opened in its space
      case "ask": return h(AskView, { me, id: where.arg, scope: team });
      case "search": return h(SearchView, { me, initial: where.arg, team });
      case "map": return h(MapView, { team });
      case "proposals": return h(ProposalsView, { me, id: where.arg, team }); // this team's proposals only
      case "settings": return h(AdminView, { me, section: where.arg, team });
      case "concepts": return h(ConceptView, { id: where.arg });
      default: return h(HomeView, { me, alerts, team, teamName });
    }
  };
  const orgPage = () => {
    switch (where.view) {
      case "": return null;
      case "home": return h(HomeView, { me, alerts });
      case "search": return h(SearchView, { me, initial: where.arg });
      case "ask": return h(AskView, { me, id: where.arg });
      case "documents": return h(DocumentsView, { me, names, doc: where.arg }); // every space; a document opened in place
      case "developer": return h(DeveloperView, { me });
      case "concepts": return h(ConceptView, { id: where.arg });
      case "proposals": return h(ProposalsView, { me, id: where.arg });
      case "admin": return h(AdminView, { me, section: where.arg });
      case "alerts": return h(AlertsView, { me, onCount: setAlerts });
      case "compliance": return h(ComplianceView, { me, section: where.arg });
      case "accounts": return h(AccountsView, { outcome: where.arg });
      default: return h(MapView, null);
    }
  };

  return h("div", { className: "shell" },
    h("aside", { className: `sidebar ${open ? "open" : ""}`, "aria-label": "Navigation" },
      h("div", { className: "sb-head" },
        h("span", { className: "mark", "aria-hidden": true }, "Ae"),
        h("span", { className: "sb-name sb-fade" }, "Aerugo")),
      h(ScopePicker, { key: `${team}:${where.view}`, me, names, team, view: where.view }), // a new page closes it
      team ? h("nav", { className: "sb-nav", "aria-label": `Pages in ${teamName}` },
          item("home", "home", "Overview"),
          group("Knowledge"),
          item("documents", "docs", "Documents"),
          item("map", "map", "Map"),
          item("search", "search", "Search"),
          group("Assist"),
          item("ask", "ask", "Ask"),
          item("proposals", "draft", "Proposals"),
          teamAdmin ? group("Team") : null,
          teamAdmin ? item("settings", "sliders", "Team settings") : null)
        : h("nav", { className: "sb-nav", "aria-label": "Pages across acme" },
          item("home", "home", "Home"),
          group("Knowledge"),
          item("documents", "docs", "Documents"),
          item("map", "map", "Map"),
          item("search", "search", "Search"),
          group("Assist"),
          item("ask", "ask", "Ask"),
          item("proposals", "draft", "Proposals"),
          admin ? group("Admin") : null,
          admin ? h("a", { href: "#/alerts", className: "sb-item", title: "Alerts", "aria-current": where.view === "alerts" ? "page" : undefined },
            h(Icon, { name: "bell" }), h("span", { className: "sb-text" }, "Alerts"),
            alerts ? h("span", { className: "sb-badge", "aria-label": `${alerts} firing` }, alerts) : null) : null,
          admin ? item("admin", "sliders", "Admin") : null,
          me && me.org_admin ? item("compliance", "shield", "Compliance") : null),
      h("div", { className: "sb-foot" },
        item("developer", "code", "Developer", ""),
        item("accounts", "link", "Connected accounts", ""),
        h("div", { className: "sb-hint sb-fade", "aria-hidden": true }, h("kbd", null, mac ? "⌘K" : "Ctrl K"), h("span", null, "Command palette")))),
    open ? h("div", { className: "drawer-scrim", onClick: () => setOpen(false), "aria-hidden": true }) : null,

    h("main", { className: "main" },
      h("header", { className: "topbar" },
        h("button", { type: "button", className: "btn btn-ghost btn-icon topbar-menu", onClick: () => setOpen(!open),
                      "aria-label": open ? "Close menu" : "Open menu", "aria-expanded": open }, h(Icon, { name: open ? "x" : "menu" })),
        where.view ? h(Crumbs, { team, names, view: where.view }) : null,
        h("div", { className: "tb-spacer" }),
        h("button", { type: "button", className: "tb-search", onClick: () => setPalette("commands"), "aria-label": "Search or jump to a page" },
          h(Icon, { name: "search", size: 14 }), h("span", null, "Search or jump to"), h("kbd", null, mac ? "⌘K" : "Ctrl K")),
        me ? h("div", { className: "tb-health" },
          h(Status, { tone: !status ? "idle" : status.ok ? "ok" : "bad" },
            !status ? "Checking…" : status.ok ? "All systems working" : down.length ? `Not answering: ${down.join(", ")}` : "The API isn't answering"),
          status && status.version ? h("small", null, `v${status.version}`) : null) : null,
        me ? h(Account, { key: `${team}:${where.view}`, me, onHelp: () => setPalette("help") }) : null),
      team ? teamPage() : orgPage()),
    palette ? h(Palette, { me, mode: palette, onMode: setPalette, onClose: () => setPalette(null) }) : null);
}
