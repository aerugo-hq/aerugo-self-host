// D0's clickable prototype: every screen of the revamped app for one person at a time (docs/design/IA.md), rendered
// from data.js by a small hash router. Static, no dependencies, never shipped; D1 builds these screens in the app.
"use strict";

const state = { persona: "ana", done: {}, connectStep: 0, frontTab: "ask" };
try { state.persona = PEOPLE[localStorage.getItem("d0-persona")] ? localStorage.getItem("d0-persona") : "ana"; } catch { /* storage blocked: Ana */ }

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const me = () => PEOPLE[state.persona];
const unitOf = (id) => UNITS.find((u) => u.id === id);
const kindName = (k, plural = false) => { const row = KINDS.find((x) => x[0] === k); return row ? (plural ? row[1] : row[2]) : k; };
const teamName = (t) => TEAMS[t] || t;

// The app's own icon paths (services/web/app/icons.js), and four new ones the revamp needs.
const PATHS = {
  home: ["M4 11.5 12 4.5l8 7", "M6 10v10h12V10", "M10 20v-5h4v5"],
  search: ["M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Z", "m20 20-3.6-3.6"],
  ask: ["M20 11.5a7.5 7.5 0 0 1-10.9 6.7L4 19.5l1.3-4.6A7.5 7.5 0 1 1 20 11.5Z", "M9.5 9.5a2.5 2.5 0 1 1 3.4 2.3c-.6.3-.9.8-.9 1.4", "M12 15.5h0"],
  docs: ["M9 6V5a2 2 0 0 1 2-2h5l4 4v9a2 2 0 0 1-2 2h-1", "M16 3v4h4", "M4 10a2 2 0 0 1 2-2h5l4 4v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-9Z", "M11 8v4h4"],
  map: ["M12 8.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z", "M5.5 20.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z", "M18.5 20.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z", "m10.6 8 -3.7 7.6", "m13.4 8 3.7 7.6", "M8 18h8"],
  sliders: ["M4 6h10", "M18 6h2", "M16 4v4", "M4 12h4", "M12 12h8", "M10 10v4", "M4 18h12", "M20 18h0", "M18 16v4"],
  shield: ["M12 3 5 6v6c0 4.5 3 8 7 9 4-1 7-4.5 7-9V6l-7-3Z"],
  user: ["M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z", "M4 21a8 8 0 0 1 16 0"],
  handover: ["M4 8h13", "m14 5 3 3-3 3", "M20 16H7", "m10 13-3 3 3 3"],
  check: ["m5 12.5 4.5 4.5L19 7.5"],
  menu: ["M4 7h16", "M4 12h16", "M4 17h16"],
  x: ["M18 6 6 18", "m6 6 12 12"],
  book: ["M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2V5Z", "M4 19a2 2 0 0 1 2-2h13", "M9 7h6"],
  phone: ["M5 4h3l1.5 4-2 1.5a11 11 0 0 0 7 7l1.5-2 4 1.5v3a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2Z"],
  inbox: ["M4 13l2.5-8h11L20 13", "M4 13v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5", "M4 13h4.5l1.5 2.5h4l1.5-2.5H20"],
  alert: ["M12 4 3 19h18L12 4Z", "M12 10v4", "M12 17h0"],
};
const icon = (name, size = 16) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${(PATHS[name] || []).map((d) => `<path d="${d}"/>`).join("")}</svg>`;
// A dot and a word; a status that's a sentence wraps rather than widening the page.
const status = (label, tone) => `<span class="status${String(label).length > 32 ? " wrap" : ""}"><span class="dot ${tone}"></span>${esc(label)}</span>`;
const STATUS = { confirmed: ["Current", "ok"], stale: ["Due for review", "warn"], proposed: ["Waiting to be confirmed", "pending"], superseded: ["Replaced", "idle"] };
const unitStatus = (u) => status(...(STATUS[u.status] || [u.status, "idle"]));

// Whether a unit applies to the person (F8): a role names their title or its head; a place names their state or store.
function applies(u, p = me()) {
  const s = u.scope || {};
  const role = !s.roles.length || s.roles.some((r) => p.title.startsWith(r));
  const place = !s.places.length || s.places.some((x) => x === p.state || x === p.place);
  return role && place;
}
const scopeText = (u) => {
  const s = u.scope || { roles: [], places: [] };
  const parts = [...s.roles.map((r) => `${r}s`), ...s.places];
  return parts.length ? parts.join(", ") : "Everyone at acme";
};
const holderFor = (u) => (HOLDERS[state.persona] || {})[u.id] || (u.contact && u.contact.holder) || "Nobody holds it for you yet";
const stepsFor = (u, p = me()) => (u.steps || []).filter((s) => !s.places || s.places.some((x) => x === p.state || x === p.place));
const canSettle = (u) => me().curator && Object.keys(me().roles).includes(u.team);

// ------------------------------------------------------------------ the router
const ROUTES = [
  [/^home$/, home], [/^ask$/, ask], [/^knowledge$/, knowledge], [/^knowledge\/([\w-]+)$/, unitPage], [/^run\/([\w-]+)$/, runner],
  [/^people$/, whoToCall], [/^review(?:\/(\w+))?$/, review], [/^documents$/, () => asToday("Documents", "docs", DOCS_NOTE)],
  [/^map$/, () => asToday("Map", "map", MAP_NOTE)], [/^handovers$/, () => asToday("Handovers", "handover", HANDOVER_NOTE)],
  [/^settings(?:\/sources)?$/, sources], [/^settings\/sources\/([\w-]+)$/, source], [/^settings\/organization$/, organization],
  [/^setup$/, setupOrg], [/^setup\/team$/, setupTeam], [/^welcome$/, welcome], [/^connect$/, connect], [/^frontline$/, frontline],
  [/^you$/, you], [/^systems$/, () => asToday("Systems", "map", SYSTEMS_NOTE)],
];
const route = () => location.hash.replace(/^#\/?/, "").split("?")[0] || "home";

function render() {
  const path = route();
  let view = notFound;
  for (const [re, fn] of ROUTES) {
    const m = path.match(re);
    if (m) { view = () => fn(...m.slice(1)); break; }
  }
  document.getElementById("nav").innerHTML = nav(path);
  document.getElementById("crumbs").innerHTML = crumbs(path);
  const target = document.getElementById("view");
  target.innerHTML = view();
  document.getElementById("sidebar").classList.remove("open");
  const heading = target.querySelector("h1");
  if (heading && render.moved) { heading.setAttribute("tabindex", "-1"); heading.focus({ preventScroll: true }); }
  render.moved = true;
  window.scrollTo(0, 0);
}

// ------------------------------------------------------------------ the shell
function nav(path) {
  const p = me();
  const waiting = (REVIEW[state.persona] || []).length;
  const item = (href, iconName, label, extra = "") => {
    const current = path === href || (href !== "home" && path.startsWith(`${href}/`)) || (href === "knowledge" && path.startsWith("run/"));
    return `<a class="sb-item" href="#/${href}" title="${esc(label)}"${current ? ' aria-current="page"' : ""}>${icon(iconName)}<span class="sb-text">${esc(label)}</span>${extra}</a>`;
  };
  const group = (label) => `<div class="sb-label" aria-hidden="true">${esc(label)}</div>`;
  return [
    item("home", "home", "Home"),
    group("Know"), item("ask", "ask", "Ask"), item("knowledge", "book", "Knowledge"), item("people", "phone", "Who to call"),
    item("documents", "docs", "Documents"), item("map", "map", "Map"),
    group("Act"),
    waiting ? item("review", "inbox", "Review", `<span class="sb-badge count" aria-label="${waiting} waiting">${waiting}</span>`) : "",
    item("handovers", "handover", "Handovers"),
    p.admin ? group("Admin") + item("settings", "sliders", "Settings") : "",
    p.orgAdmin ? item("settings/organization", "shield", "Organization") : "",
  ].join("");
}

const PAGE_NAMES = { home: "Home", ask: "Ask", knowledge: "Knowledge", run: "Knowledge", people: "Who to call", review: "Review", documents: "Documents",
                     map: "Map", handovers: "Handovers", settings: "Settings", setup: "Getting started", welcome: "Welcome", connect: "Connect a source",
                     frontline: "Frontline view", you: "You", systems: "Systems" };
function crumbs(path) {
  const head = path.split("/")[0];
  if (head === "home") return "";
  const label = PAGE_NAMES[head] || head;
  return `<nav class="crumbs-bar" aria-label="Breadcrumb"><a href="#/home">acme</a><span aria-hidden="true">›</span><span aria-current="page">${esc(label)}</span></nav>`;
}

// ------------------------------------------------------------------ Home, fitted to role and place
function home() {
  const p = me();
  const changes = CHANGES[state.persona] || [];
  const checklists = CHECKLISTS[state.persona] || [];
  const waiting = REVIEW[state.persona] || [];
  const frontline = ["ana", "cora", "fay"].includes(state.persona);
  const setup = p.orgAdmin ? setupList(SETUP_ORG, "Getting acme started", "#/setup") : p.admin ? setupList(SETUP_TEAM, `Get ${teamName(p.team)} ready`, "#/setup/team") : "";
  return `<div class="page">
    <div class="page-head"><div><h1>Good morning, ${esc(p.name.split(" ")[0])}</h1>
      <p class="lede">${esc(p.title)} at ${esc(p.placeName)}, ${esc(p.state)}. Answers and changes here fit that. <a class="a" href="#/you">Not right?</a></p></div></div>
    <div class="grid-main">
      <div class="stack">
        ${setup}
        <form class="ask-home" data-go="#/ask">
          <label class="sr-only" for="home-ask">Ask a question</label>
          <div class="searchbox ask-box">${icon("ask")}<input id="home-ask" placeholder="Ask about anything at acme"><button class="btn btn-primary btn-sm" type="submit">Ask</button></div>
          <p class="suggest">People in your role asked: <a class="a" href="#/ask">${esc(ASK[state.persona].q)}</a></p>
        </form>
        <section class="panel">
          <div class="panel-head"><h2>This week for you</h2><p>Changes that apply to ${esc(p.title.toLowerCase())}s${p.place === "HQ" ? "" : ` in ${esc(p.state)}`}</p></div>
          ${changes.length ? `<ul class="changes">${changes.map((c) => {
            const u = c.unit && unitOf(c.unit);
            return `<li><div class="change-what">${u ? `<a class="change-title" href="#/knowledge/${u.id}">${esc(c.what)}</a>` : `<a class="change-title" href="#/settings/sources/stores-slack">${esc(c.what)}</a>`}
              <span class="meta">${u ? `<span>${esc(kindName(u.kind))}, ${esc(u.owner)}</span>` : ""}<span>Applies: ${esc(c.why)}</span><span>${esc(c.when)}</span></span></div></li>`;
          }).join("")}</ul>` : `<div class="empty"><span class="empty-text">Nothing changed for your role this week.</span></div>`}
        </section>
      </div>
      <aside class="stack">
        ${waiting.length ? `<section class="panel"><div class="panel-head"><h2>Waiting for you</h2><a class="btn btn-sm" href="#/review">Open Review</a></div>
          <ul class="counts">${REVIEW_TABS.map(([tab, name]) => [name, waiting.filter((w) => w.tab === tab).length, tab]).filter(([, n]) => n)
            .map(([name, n, tab]) => `<li><a href="#/review/${tab}"><span>${esc(name)}</span><span class="num">${n}</span></a></li>`).join("")}</ul></section>` : ""}
        ${checklists.length ? `<section class="panel"><div class="panel-head"><h2>Your checklists</h2></div><ul class="counts">${checklists.map((c) => {
          const u = unitOf(c.unit); const total = stepsFor(u).length; const done = Math.min(c.done, total);
          return `<li><a href="#/run/${u.id}"><span>${esc(u.title)}</span><span class="num">${done} of ${total}</span></a></li>`;
        }).join("")}</ul></section>` : ""}
        ${frontline ? `<section class="panel"><div class="panel-head"><h2>Who to call here</h2><a class="btn btn-sm btn-ghost" href="#/people">All</a></div>
          <ul class="callers">${["manager-on-duty", "district-manager", "risk"].map(unitOf).map((u) =>
            `<li><b>${esc(u.title)}</b><span>${esc(holderFor(u))}</span></li>`).join("")}</ul></section>` : ""}
      </aside>
    </div></div>`;
}

function setupList(steps, title, href) {
  const done = steps.filter((s) => s.done).length;
  return `<section class="panel"><div class="panel-head"><h2>${esc(title)}</h2><p>${done} of ${steps.length} done</p></div>
    <ol class="setup">${steps.map((s) => `<li class="${s.done ? "done" : ""}"><span class="setup-mark" aria-hidden="true">${s.done ? icon("check", 14) : ""}</span>
      <div><b>${esc(s.step)}</b><span>${esc(s.detail)}</span></div>${s.done ? '<span class="sr-only">Done</span>' : ""}</li>`).join("")}</ol>
    <div class="panel-foot"><a class="btn btn-sm" href="${href}">Continue setting up</a></div></section>`;
}

// ------------------------------------------------------------------ Ask: units first, in their kind's shape
function ask() {
  const a = ASK[state.persona];
  const lead = unitOf(a.lead);
  return `<div class="page narrow">
    <div class="page-head"><div><h1>Ask</h1><p class="lede">Answers come from what your teams confirmed first, then from documents, always within what you can read.</p></div></div>
    <div class="msg-q">${esc(a.q)}</div>
    <article class="msg-answer">
      <p class="answer-text">${esc(a.text)}</p>
      <div class="unit-cards">${unitCard(lead, true)}${a.also.map((id) => unitCard(unitOf(id), false)).join("")}</div>
      <div class="msg-meta">${status(`${a.confidence} confidence: ${a.reasons}`, a.confidence === "High" ? "ok" : "warn")}</div>
      <div class="feedback-row"><button class="btn btn-sm" type="button" data-say="Sent to its owners. They see the note, never who sent it.">This is wrong</button>
        <button class="btn btn-sm" type="button" data-say="Asked ${esc(lead.owner)}. Their answer comes back here.">Ask an expert</button><span class="said" role="status"></span></div>
    </article>
    <form class="composer" data-go="#/ask"><label class="sr-only" for="follow">Ask a follow-up</label>
      <textarea id="follow" placeholder="Ask a follow-up"></textarea><div class="composer-bar"><span class="spacer"></span><button class="btn btn-primary" type="submit">Ask</button></div></form>
  </div>`;
}

// A unit as Ask shows it: a procedure's first steps with Start, a policy with its date, a contact with its holder.
function unitCard(u, lead) {
  const head = `<div class="uc-head"><span class="kind">${esc(kindName(u.kind))}</span><a class="uc-title" href="#/knowledge/${u.id}">${esc(u.title)}</a></div>`;
  let body = "";
  if (u.kind === "procedure") {
    const steps = stepsFor(u);
    body = `<ol class="uc-steps">${steps.slice(0, lead ? 4 : 2).map((s) => `<li>${esc(s.text)}</li>`).join("")}</ol>
      ${steps.length > (lead ? 4 : 2) ? `<p class="small muted">and ${steps.length - (lead ? 4 : 2)} more steps</p>` : ""}
      <a class="btn btn-sm${lead ? " btn-primary" : ""}" href="#/run/${u.id}">Start the checklist</a>`;
  } else if (u.kind === "contact") {
    body = `<p><b>${esc(holderFor(u))}</b></p><p class="small muted">${esc(u.contact.reach)}</p>`;
  } else if (u.kind === "policy") {
    body = `<p>${esc(u.text)}</p><p class="small muted">In effect from ${esc(u.effective)}. Applies to: ${esc(scopeText(u))}.</p>`;
  } else if (u.kind === "definition") {
    body = `<p>${esc(u.text)}</p>${(u.perspectives || []).map((v) => `<p class="perspective"><b>${esc(v.holder)}'s view:</b> ${esc(v.text)}</p>`).join("")}`;
  } else {
    body = `<p>${esc(u.text)}</p>`;
  }
  return `<section class="unit-card${lead ? " lead" : ""}">${head}${body}</section>`;
}


// ------------------------------------------------------------------ Knowledge: one list, kinds as filters
function knowledge() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const kind = params.get("kind") || "";
  const mine = params.get("mine") !== "0";
  const pool = UNITS.filter((u) => u.status !== "proposed" && (!mine || applies(u)));
  const rows = pool.filter((u) => !kind || u.kind === kind);
  const link = (k, m = mine) => `#/knowledge?${new URLSearchParams({ ...(k ? { kind: k } : {}), mine: m ? "1" : "0" })}`;
  return `<div class="page">
    <div class="page-head"><div><h1>Knowledge</h1><p class="lede">What your teams know and confirmed, with its evidence. ${mine ? "Showing what applies to you." : "Showing everything you can read."}</p></div>
      <div class="actions"><label class="check"><input type="checkbox" ${mine ? "checked" : ""} data-href="${esc(link(kind, !mine))}"> Applies to me</label></div></div>
    <nav class="chips" aria-label="Kinds">
      <a class="chip" href="${esc(link(""))}"${!kind ? ' aria-current="page"' : ""}>All <span class="num">${pool.length}</span></a>
      ${KINDS.map(([k, plural]) => [k, plural, pool.filter((u) => u.kind === k).length]).filter(([, , n]) => n)
        .map(([k, plural, n]) => `<a class="chip" href="${esc(link(k))}"${kind === k ? ' aria-current="page"' : ""}>${esc(plural)} <span class="num">${n}</span></a>`).join("")}
    </nav>
    <section class="panel"><div class="tbl-wrap"><table class="tbl">
      <thead><tr><th>Title</th><th class="hide-sm">Kind</th><th class="hide-sm">Owner</th><th>Status</th><th class="hide-sm">Applies to</th></tr></thead>
      <tbody>${rows.map((u) => `<tr><td class="k-title"><a class="title" href="#/knowledge/${u.id}">${esc(u.title)}</a><div class="sub">${esc(u.text)}</div></td>
        <td class="hide-sm">${esc(kindName(u.kind))}</td><td class="hide-sm">${esc(u.owner)}</td><td>${unitStatus(u)}</td><td class="hide-sm">${esc(scopeText(u))}</td></tr>`).join("")}</tbody>
    </table></div>${rows.length ? "" : `<div class="empty"><span class="empty-text">Nothing of this kind applies to you. Turn off "Applies to me" to see everything you can read.</span></div>`}</section>
  </div>`;
}

// ------------------------------------------------------------------ a unit's page: the same frame, its kind's shape
function unitPage(id) {
  const u = unitOf(id);
  if (!u) return notFound();
  const settle = canSettle(u);
  return `<div class="page">
    <nav class="crumbs" aria-label="Where it sits"><a href="#/knowledge">Knowledge</a><span class="sep" aria-hidden="true">/</span><a href="#/knowledge?kind=${u.kind}&mine=0">${esc(kindName(u.kind, true))}</a></nav>
    <div class="page-head"><div><h1>${esc(u.title)}</h1><p class="lede">${esc(kindName(u.kind))} owned by ${esc(u.owner)}. ${u.confirmed ? `Confirmed ${esc(u.confirmed)}.` : u.origin ? esc(u.origin) + "." : ""}</p></div>
      <div class="actions">${u.kind === "procedure" ? `<a class="btn btn-primary" href="#/run/${u.id}">Start the checklist</a>` : ""}
        ${settle ? `<button class="btn" type="button" data-say="Confirmed: reviewed today.">${u.status === "proposed" ? "Confirm" : "Still right"}</button><button class="btn btn-ghost" type="button" data-say="A new version opens beside this one.">New version</button>` : ""}
        <button class="btn btn-ghost" type="button" data-say="Sent to ${esc(u.owner)}. They see the note, never who sent it.">This is wrong</button><span class="said" role="status"></span></div></div>
    ${(u.conflicts || []).length ? `<div class="alert warn"><span class="dot warn"></span><div class="alert-body"><b>Two sources disagree.</b> ${esc(u.conflicts[0].note)} <a class="a" href="#/knowledge/${u.conflicts[0].with}">See the other</a></div></div>` : ""}
    <div class="grid-main unit-grid">
      <div class="stack">${shape(u)}</div>
      <aside class="stack">
        <section class="panel"><dl class="panel-body facts">
          <dt>Status</dt><dd>${unitStatus(u)}</dd>
          <dt>Applies to</dt><dd>${esc(scopeText(u))}<span class="small muted block">${applies(u) ? "Including you" : "Not you, but you can read it"}</span></dd>
          <dt>Owner</dt><dd>${esc(u.owner)}</dd>
          <dt>Trust</dt><dd>${esc({ confirmed: "Confirmed by its owner", documented: "Written down, not yet confirmed", observed: "Said by someone, not yet confirmed", inferred: "Inferred" }[u.trust])}</dd>
          ${u.review ? `<dt>Review</dt><dd>${esc(u.review)}</dd>` : ""}
        </dl></section>
        <section class="panel"><div class="panel-head"><h2>Evidence</h2><p>Only what you can open</p></div>
          <ul class="evidence">${u.evidence.map((e) => `<li><a href="#/documents"><b>${esc(e.title)}</b><span>${esc(e.source)}, ${esc(e.where)}</span></a></li>`).join("")}</ul></section>
      </aside>
    </div></div>`;
}

function shape(u) {
  if (u.kind === "policy") {
    return `<section class="panel"><div class="panel-body"><p class="rule">${esc(u.text)}</p><p class="meta"><span>In effect from ${esc(u.effective)}</span></p>
      ${u.changed ? `<p class="changed"><b>What changed:</b> ${esc(u.changed)}</p>` : ""}</div></section>
      ${(u.versions || []).length > 1 ? `<section class="panel"><div class="panel-head"><h2>Versions</h2></div><ul class="timeline-list versions">${u.versions.map((v) =>
        `<li><span class="dot ${v.status === "current" ? "ok" : "idle"}"></span><div><b>${esc(v.text)}</b><span class="when">${esc(v.from)}${v.until ? ` to ${esc(v.until)}` : ", in effect now"}</span></div></li>`).join("")}</ul></section>` : ""}`;
  }
  if (u.kind === "procedure") {
    const steps = stepsFor(u);
    return `<section class="panel"><div class="panel-head"><h2>Steps</h2><p>${steps.length} steps for ${esc(me().placeName)}</p></div>
      <ol class="proc-steps">${steps.map((s) => `<li><b>${esc(s.text)}</b><span class="meta"><span>${esc(s.role)}</span>${s.when ? `<span>${esc(s.when)}</span>` : ""}${s.form ? `<span>Form ${esc(s.form)}</span>` : ""}${s.places ? `<span>${esc(s.places.join(", "))} only</span>` : ""}</span></li>`).join("")}</ol></section>`;
  }
  if (u.kind === "contact") {
    return `<section class="panel"><div class="panel-body contact-big"><span class="muted">${esc(u.contact.role)}, for you</span><b>${esc(holderFor(u))}</b><span>${esc(u.contact.reach)}</span><p class="muted small">${esc(u.text)}</p></div></section>`;
  }
  if (u.kind === "definition") {
    return `<section class="panel"><div class="panel-body"><p class="rule">${esc(u.text)}</p></div></section>
      <section class="panel"><div class="panel-head"><h2>How others use it</h2><p>Each said as its holder's, never merged</p></div><div class="panel-body">${(u.perspectives || []).map((v) =>
        `<p class="perspective"><b>${esc(v.holder)}</b>: ${esc(v.text)} <span class="muted small">(from ${esc(v.where)})</span></p>`).join("")}</div></section>`;
  }
  if (u.kind === "decision") {
    return `<section class="panel"><div class="panel-body"><p class="rule">${esc(u.text)}</p><p class="meta"><span>${esc(u.decided)}</span></p></div></section>`;
  }
  return `<section class="panel"><div class="panel-body"><p class="rule">${esc(u.text)}</p>${u.origin ? `<p class="meta"><span>${esc(u.origin)}</span></p>` : ""}</div></section>`;
}

// ------------------------------------------------------------------ the procedure runner
function runner(id) {
  const u = unitOf(id);
  if (!u || u.kind !== "procedure") return notFound();
  const steps = stepsFor(u);
  const key = `${state.persona}:${id}`;
  if (!(key in state.done)) state.done[key] = Math.min(((CHECKLISTS[state.persona] || []).find((c) => c.unit === id) || { done: 0 }).done, steps.length);
  const at = state.done[key];
  const contacts = [...new Set(steps.map((s) => s.contact).filter(Boolean))].map(unitOf);
  const finished = at >= steps.length;
  return `<div class="page runner">
    <nav class="crumbs" aria-label="Where it sits"><a href="#/knowledge">Knowledge</a><span class="sep" aria-hidden="true">/</span><a href="#/knowledge/${u.id}">The procedure, with its evidence</a></nav>
    <div class="page-head"><div><h1>${esc(u.title)}</h1><p class="lede" role="status">${finished ? "Every step done." : `Step ${at + 1} of ${steps.length}`}</p></div>
      <div class="actions"><button class="btn btn-ghost" type="button" data-restart="${esc(key)}">Start over</button></div></div>
    <div class="progress" aria-hidden="true"><span style="width:${Math.round((at / steps.length) * 100)}%"></span></div>
    <div class="grid-main">
      <ol class="run-steps">${steps.map((s, i) => {
        const cls = i < at ? "done" : i === at ? "current" : "next";
        return `<li class="${cls}"${i === at ? ' aria-current="step"' : ""}>
          <span class="run-mark" aria-hidden="true">${i < at ? icon("check", 14) : i + 1}</span>
          <div class="run-body"><p class="run-text">${esc(s.text)}</p>
            ${i === at ? `<p class="meta"><span>Who: ${esc(s.role)}</span>${s.when ? `<span>When: ${esc(s.when)}</span>` : ""}${s.form ? `<span>Form: ${esc(s.form)}</span>` : ""}${s.places ? `<span>${esc(s.places.join(", "))} stores</span>` : ""}</p>
              <div class="actions"><button class="btn btn-primary" type="button" data-advance="1" data-key="${esc(key)}">Done, next step</button>${i ? `<button class="btn btn-ghost" type="button" data-advance="-1" data-key="${esc(key)}">Back</button>` : ""}</div>` : ""}
          </div></li>`;
      }).join("")}</ol>
      <aside class="run-contacts" aria-label="Who you'll call"><section class="panel"><div class="panel-head"><h2>Who you'll call</h2></div>
        <ul class="callers">${contacts.length ? contacts.map((c) => `<li><b>${esc(c.title)}</b><span>${esc(holderFor(c))}</span><small>${esc(c.contact.reach)}</small></li>`).join("") : "<li><span>This procedure names no one to call.</span></li>"}</ul></section></aside>
    </div>
    ${finished ? `<div class="alert ok"><span class="dot ok"></span><div class="alert-body"><b>Done.</b> Anything that went differently? <a class="a" href="#/ask">Tell its owners</a>; it can become a lesson.</div></div>` : ""}
  </div>`;
}

// ------------------------------------------------------------------ who to call
function whoToCall() {
  const contacts = UNITS.filter((u) => u.kind === "contact");
  return `<div class="page">
    <div class="page-head"><div><h1>Who to call</h1><p class="lede">Roles and who holds them for you at ${esc(me().placeName)}, from the company directory. A role nobody clearly holds says so; it never guesses.</p></div></div>
    <section class="panel"><div class="tbl-wrap"><table class="tbl">
      <thead><tr><th>Role</th><th>For you</th><th class="hide-sm">How to reach</th><th class="hide-sm">Kept by</th></tr></thead>
      <tbody>${contacts.map((u) => `<tr><td><a class="title" href="#/knowledge/${u.id}">${esc(u.title)}</a></td><td>${esc(holderFor(u))}</td><td class="hide-sm">${esc(u.contact.reach)}</td><td class="hide-sm">${esc(u.owner)}</td></tr>`).join("")}</tbody>
    </table></div></section></div>`;
}

// ------------------------------------------------------------------ Review: one inbox
function review(tab) {
  const items = REVIEW[state.persona] || [];
  if (!items.length) {
    return `<div class="page"><div class="page-head"><div><h1>Review</h1></div></div><section class="panel"><div class="empty">${icon("inbox", 28)}<b class="empty-title">Nothing is waiting for you</b>
      <span class="empty-text">When something you own needs confirming, or a handover or meeting you're part of proposes something, it shows here.</span></div></section></div>`;
  }
  const tabs = REVIEW_TABS.filter(([t]) => items.some((i) => i.tab === t));
  const current = tab && tabs.some(([t]) => t === tab) ? tab : tabs[0][0];
  const ACTIONS = { confirm: ["Confirm", "Not right"], conflicts: ["Keep the policy's", "Keep both, with a note"], stale: ["Still right", "New version"],
                    corrections: ["Fix it", "Still right"], gaps: ["Write the answer", "Ask an expert"], drafts: ["Open the draft", "Close it"] };
  return `<div class="page">
    <div class="page-head"><div><h1>Review</h1><p class="lede">Everything waiting on you, whatever proposed it. Each item says why it's yours.</p></div></div>
    <nav class="section-nav" aria-label="Review">${tabs.map(([t, name]) => `<a href="#/review/${t}"${t === current ? ' aria-current="page"' : ""}>${esc(name)} <span class="num">${items.filter((i) => i.tab === t).length}</span></a>`).join("")}</nav>
    <section class="panel"><ul class="inbox">${items.filter((i) => i.tab === current).map((i) => `<li>
      <div class="inbox-text"><b>${i.unit ? `<a href="#/knowledge/${i.unit}">${esc(i.title)}</a>` : esc(i.title)}</b><p>${esc(i.text)}</p>
        <span class="meta"><span>${esc(i.from)}</span><span>${esc(i.why)}</span><span>${esc(i.age)}</span></span></div>
      <div class="actions"><button class="btn btn-sm" type="button" data-settled="${esc(ACTIONS[current][0])}">${esc(ACTIONS[current][0])}</button>
        <button class="btn btn-sm btn-ghost" type="button" data-settled="${esc(ACTIONS[current][1])}">${esc(ACTIONS[current][1])}</button></div><span class="said" role="status"></span></li>`).join("")}</ul></section></div>`;
}

// ------------------------------------------------------------------ settings: sources and their channels
function sources() {
  const p = me();
  if (!p.admin) return forbidden("Settings are for team admins.");
  const mine = SOURCES.filter((s) => p.orgAdmin || Object.keys(p.roles).includes(s.team));
  return `<div class="page">
    <div class="page-head"><div><h1>Sources</h1><p class="lede">What your teams connected, how fresh it is, and for chat, which channels are captured.</p></div>
      <div class="actions"><a class="btn btn-primary" href="#/connect">Connect a source</a></div></div>
    <section class="panel"><div class="tbl-wrap"><table class="tbl">
      <thead><tr><th>Source</th><th class="hide-sm">Team</th><th>Synced</th><th class="hide-sm">Keeps</th><th class="hide-sm">Channels captured</th></tr></thead>
      <tbody>${mine.map((s) => `<tr><td><a class="title" href="#/settings/sources/${s.id}">${esc(s.name)}</a><div class="sub">${esc(s.kind)}</div></td><td class="hide-sm">${esc(teamName(s.team))}</td>
        <td>${status(s.synced, s.health)}</td><td class="hide-sm">${esc(s.storage)}</td><td class="hide-sm num">${s.channels.length ? `${s.channels.filter((c) => c.capture === "on").length} of ${s.channels.length}` : ""}</td></tr>`).join("")}</tbody>
    </table></div>${mine.length ? "" : `<div class="empty"><span class="empty-text">Your teams haven't connected a source yet.</span><a class="btn btn-sm" href="#/connect">Connect a source</a></div>`}</section></div>`;
}

function source(id) {
  const s = SOURCES.find((x) => x.id === id);
  if (!s) return notFound();
  const CAP = { on: ["Captured", "ok"], requested: ["Its owner asked: waiting for you", "warn"], off: ["Not captured", "idle"] };
  return `<div class="page">
    <nav class="crumbs" aria-label="Where it sits"><a href="#/settings/sources">Sources</a></nav>
    <div class="page-head"><div><h1>${esc(s.name)}</h1><p class="lede">${esc(s.kind)}, for ${esc(teamName(s.team))}. Keeps: ${esc(s.storage)}. Last synced ${esc(s.synced)}.</p></div>
      <div class="actions"><button class="btn" type="button" data-say="Syncing now.">Sync now</button><span class="said" role="status"></span></div></div>
    ${s.note ? `<div class="alert warn"><span class="dot warn"></span><div class="alert-body"><b>Needs attention.</b> ${esc(s.note)}</div></div>` : ""}
    ${s.channels.length ? `<section class="panel"><div class="panel-head"><h2>Channels</h2><p>A channel is captured only with two keys: its owner asks, and a team admin confirms</p></div>
      <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Channel</th><th>Capture</th><th class="hide-sm">Who</th><th class="hide-sm">Answers asks</th><th class="actions-cell"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody>${s.channels.map((c) => `<tr><td>${esc(c.name)}</td><td>${status(...CAP[c.capture])}</td><td class="hide-sm">${esc(c.by)}</td><td class="hide-sm">${c.answers ? "Yes, privately" : "No"}</td>
        <td class="actions-cell">${c.capture === "requested" ? `<button class="btn btn-sm" type="button" data-settled="Confirmed">Confirm</button>` : c.capture === "on" ? `<button class="btn btn-sm btn-ghost" type="button" data-settled="Turned off">Turn off</button>` : ""}<span class="said" role="status"></span></td></tr>`).join("")}</tbody></table></div></section>`
      : `<section class="panel"><div class="empty"><span class="empty-text">${esc(s.kind)} has no chat channels to capture; it's read as documents.</span></div></section>`}
  </div>`;
}

function organization() {
  if (!me().orgAdmin) return forbidden("Organization settings are for org admins.");
  return `<div class="page narrow"><div class="page-head"><div><h1>Organization</h1><p class="lede">Who gets in, the directory, and what every team shares.</p></div></div>
    <section class="panel"><div class="panel-head"><h2>Who gets in</h2></div><div class="panel-body stack">
      <div class="seg" role="group" aria-label="Who gets in"><button type="button" aria-pressed="true">A pilot of named teams</button><button type="button">Everyone at acme</button></div>
      <p class="muted">During the pilot, only Stores and Contact center are let in; anyone else sees a page saying their team isn't on Aerugo yet.</p></div></section>
    <section class="panel"><div class="panel-head"><h2>Company directory</h2></div><div class="panel-body">${status("Microsoft Entra, synced 2 hours ago: 88 people, 14 groups", "ok")}</div></section></div>`;
}

// ------------------------------------------------------------------ onboarding flows
function setupOrg() {
  return `<div class="page narrow"><div class="page-head"><div><h1>Getting acme started</h1><p class="lede">The first admin's checklist. Each step can also be done in the onboarding repository.</p></div></div>${setupList(SETUP_ORG, "Steps", "#/settings/sources")}</div>`;
}
function setupTeam() {
  return `<div class="page narrow"><div class="page-head"><div><h1>Get ${esc(teamName(me().team))} ready</h1><p class="lede">A team admin's checklist. Your team's people see its knowledge once a source has synced.</p></div></div>${setupList(SETUP_TEAM, "Steps", "#/connect")}</div>`;
}
function welcome() {
  const p = me();
  return `<div class="page narrow"><div class="page-head"><div><h1>Welcome to Aerugo, ${esc(p.name.split(" ")[0])}</h1><p class="lede">It answers from what acme's teams know, only what you're allowed to read.</p></div></div>
    <section class="panel"><div class="panel-head"><h2>Who Aerugo thinks you are</h2><p>From the company directory</p></div>
      <div class="panel-body stack"><dl class="facts"><dt>Title</dt><dd>${esc(p.title)}</dd><dt>Place</dt><dd>${esc(p.placeName)}, ${esc(p.state)}</dd>
        <dt>Manager</dt><dd>${esc(p.manager || "None listed")}</dd><dt>Teams</dt><dd>${Object.keys(p.roles).map(teamName).map(esc).join(", ")}</dd></dl>
        <p class="small muted">Something wrong? Your directory is where it's fixed; tell your admin.</p></div></section>
    <section class="panel"><div class="panel-head"><h2>See documents shared with your accounts</h2></div><div class="panel-body actions">
      <button class="btn" type="button" data-say="Linked: GitHub.">Link GitHub</button><button class="btn" type="button" data-say="Linked: Google.">Link Google</button><span class="said" role="status"></span></div></section>
    <div class="welcome-go"><a class="btn btn-primary" href="#/ask">Ask your first question</a><a class="btn btn-ghost" href="#/home">Skip for now</a></div></div>`;
}

const CONNECT = [
  ["Choose a source", () => `<div class="gallery">${["Confluence", "Jira", "Slack", "Microsoft Teams", "Google Drive", "SharePoint", "Notion", "Data catalog"].map((n, i) =>
    `<button type="button" class="gallery-item" aria-pressed="${i === 2}">${esc(n)}</button>`).join("")}</div>`],
  ["What it needs", () => `<p>Slack: an app your company creates in its own workspace, with a bot token. It reads only the channels the bot is in, never direct messages.</p>
    <p class="small muted">Scopes: channels:history, channels:read, groups:history, groups:read, users:read, users:read.email. For capture: commands, chat:write, reactions:read.</p>`],
  ["Its credential", () => `<label class="field"><span>Bot token</span><input class="input" type="password" autocomplete="off" placeholder="Stored in your secret store, never shown again"></label>
    <p class="small muted">Or name a secret your team already keeps (Vault, AWS, Azure, GCP).</p>`],
  ["What to read", () => `<fieldset class="stack plain"><legend class="small muted">Channels the bot is in</legend>${["#stores-all", "#store-co201", "#store-or101", "#store-ca301"].map((c, i) =>
    `<label class="check"><input type="checkbox" ${i < 3 ? "checked" : ""}> ${esc(c)}</label>`).join("")}</fieldset><p class="small muted">Reading a channel isn't capturing it: capture needs its owner's key and yours, later.</p>`],
  ["How to keep it", () => `<div class="seg" role="group" aria-label="How to keep it"><button type="button" aria-pressed="true">Indexed</button><button type="button">Pointers only</button></div>
    <p class="small muted">Indexed keeps its text in your own database. Pointers only keeps no text and reads it live when someone asks.</p>`],
  ["First sync", () => `${status("Synced: 412 messages in 3 channels", "ok")}<p class="small muted">Each message is readable only by its channel's members, as in Slack.</p>`],
];
function connect() {
  const i = Math.min(state.connectStep, CONNECT.length - 1);
  return `<div class="page narrow"><div class="page-head"><div><h1>Connect a source</h1><p class="lede">Step ${i + 1} of ${CONNECT.length}: ${esc(CONNECT[i][0])}</p></div></div>
    <ol class="stepper" aria-label="Steps">${CONNECT.map(([name], n) => `<li class="${n < i ? "done" : n === i ? "current" : ""}"${n === i ? ' aria-current="step"' : ""}>${esc(name)}</li>`).join("")}</ol>
    <section class="panel"><div class="panel-head"><h2>${esc(CONNECT[i][0])}</h2></div><div class="panel-body stack">${CONNECT[i][1]()}</div>
      <div class="panel-foot between"><button class="btn btn-ghost" type="button" ${i ? "" : "disabled"} data-connect="-1">Back</button>
        ${i < CONNECT.length - 1 ? `<button class="btn btn-primary" type="button" data-connect="1">Continue</button>` : `<a class="btn btn-primary" href="#/settings/sources/stores-slack">Open the source</a>`}</div></section></div>`;
}

function you() {
  return welcome().replace("Welcome to Aerugo, ", "You, ").replace("Ask your first question", "Ask a question").replace("Skip for now", "Home");
}

// ------------------------------------------------------------------ the frontline view
function frontline() {
  const p = me();
  const tabs = [["ask", "Ask", "ask"], ["incident", "Incident", "alert"], ["policies", "Policies", "book"], ["call", "Call", "phone"]];
  const injury = unitOf("injury-steps");
  const body = {
    ask: `<label class="sr-only" for="fl-ask">Ask</label><textarea id="fl-ask" class="fl-input" placeholder="Ask anything"></textarea>
      <p class="fl-q">${esc(ASK[state.persona].q)}</p><p class="answer-text">${esc(ASK[state.persona].text)}</p><a class="btn btn-primary fl-big" href="#/run/${ASK[state.persona].lead === "injury-steps" ? "injury-steps" : "pos-offline"}">Start the checklist</a>`,
    incident: `<p class="fl-label">What's happening?</p>${["injury-steps", "pos-offline", "escalation"].map(unitOf).filter((u) => applies(u)).map((u) =>
      `<a class="fl-row" href="#/run/${u.id}"><b>${esc(u.title)}</b><span>${stepsFor(u).length} steps</span></a>`).join("")}
      <p class="small muted">Step one of ${esc(injury.title.toLowerCase())}: ${esc(stepsFor(injury)[0].text)}</p>`,
    policies: `<label class="sr-only" for="fl-pol">Look up a policy</label><input id="fl-pol" class="input fl-input" placeholder="Look up a policy">${UNITS.filter((u) => u.kind === "policy" && applies(u) && u.status === "confirmed").map((u) =>
      `<a class="fl-row" href="#/knowledge/${u.id}"><b>${esc(u.title)}</b><span>${esc(u.text)}</span></a>`).join("")}`,
    call: ["manager-on-duty", "district-manager", "risk", "pos-help"].map(unitOf).map((u) => `<a class="fl-row" href="tel:"><b>${esc(u.title)}</b><span>${esc(holderFor(u))}</span></a>`).join(""),
  }[state.frontTab];
  return `<div class="page"><div class="page-head"><div><h1>Frontline view</h1><p class="lede">What ${esc(p.name.split(" ")[0])} sees on a store handheld: three things one tap away, Who to call always at the bottom.</p></div></div>
    <div class="phone" role="region" aria-label="A phone, as a store associate holds it"><div class="phone-screen">
      <header class="phone-bar"><span class="mark">Ae</span><b>${esc(p.placeName)}</b></header>
      <div class="phone-body">${body}</div>
      <nav class="phone-tabs" aria-label="Frontline">${tabs.map(([t, name, ic]) => `<button type="button" data-front="${t}"${state.frontTab === t ? ' aria-current="page"' : ""}>${icon(ic, 20)}<span>${esc(name)}</span></button>`).join("")}</nav>
    </div></div></div>`;
}

// ------------------------------------------------------------------ screens D1 leaves as they are, and the rest
const DOCS_NOTE = "Documents stays as it is: every space you're in, each document in the reader. Each document gains a line saying which knowledge cites it.";
const MAP_NOTE = "The map of how knowledge connects stays. For data teams it gains Systems, the Atlas (designed at O3).";
const HANDOVER_NOTE = "Handovers stays as C4b built it; what a handover proposes for you to confirm now arrives in Review.";
const SYSTEMS_NOTE = "The Atlas: tables, jobs, repos, and how data flows between them. Designed at O3, with the engineering pack.";
function asToday(title, ic, note) {
  return `<div class="page narrow"><div class="page-head"><div><h1>${esc(title)}</h1></div></div><section class="panel"><div class="empty">${icon(ic, 28)}<span class="empty-text">${esc(note)}</span></div></section></div>`;
}
function forbidden(why) {
  return `<div class="page narrow"><div class="page-head"><div><h1>Not available</h1></div></div><section class="panel"><div class="empty">${icon("shield", 28)}<span class="empty-text">${esc(why)} Switch to Olivia or Kai at the top to see it.</span></div></section></div>`;
}
function notFound() {
  return `<div class="page narrow"><div class="page-head"><div><h1>Not found</h1></div></div><section class="panel"><div class="empty"><span class="empty-text">Nothing here, or nothing you can read.</span><a class="btn btn-sm" href="#/home">Go home</a></div></section></div>`;
}

// ------------------------------------------------------------------ start
function choosePersona(value) {
  state.persona = value;
  try { localStorage.setItem("d0-persona", value); } catch { /* storage blocked: kept for this visit */ }
  render();
}
function setTheme(value) {
  document.documentElement.setAttribute("data-theme", value);
  document.querySelectorAll("#theme button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.theme === value)));
}
// The prototype's own flows, reachable from any persona (in the app they're reached from Home's checklists and Settings).
document.getElementById("foot").innerHTML = '<div class="sb-label" aria-hidden="true">Prototype flows</div>' + [["welcome", "user", "First sign-in"],
  ["connect", "docs", "Connect a source"], ["setup", "check", "Getting acme started"], ["frontline", "phone", "Frontline view"]]
  .map(([href, ic, label]) => `<a class="sb-item" href="#/${href}" title="${label}">${icon(ic)}<span class="sb-text">${label}</span></a>`).join("");
document.getElementById("persona").innerHTML = Object.entries(PEOPLE).map(([id, p]) => `<option value="${id}">${esc(p.name)}, ${esc(p.title)}</option>`).join("");
document.getElementById("persona").value = state.persona;
document.getElementById("persona").addEventListener("change", (e) => choosePersona(e.target.value));
document.querySelectorAll("#theme button").forEach((b) => b.addEventListener("click", () => setTheme(b.dataset.theme)));
document.getElementById("menu").addEventListener("click", () => document.getElementById("sidebar").classList.toggle("open"));
// Every action on a page, delegated: no inline handlers, so no text ever becomes code.
document.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-say], button[data-settled], button[data-advance], button[data-restart], button[data-connect], button[data-front]");
  if (!b) return;
  const d = b.dataset;
  if (d.say !== undefined) {
    const out = b.parentElement.querySelector(".said");
    if (out) out.innerHTML = status(d.say, "ok");
    b.disabled = true;
  } else if (d.settled !== undefined) {
    const row = b.closest("li, tr");
    row.querySelector(".said").innerHTML = status(`${d.settled}: done`, "ok");
    row.querySelectorAll("button").forEach((x) => { x.disabled = true; });
  } else if (d.advance !== undefined) {
    state.done[d.key] = Math.max(0, state.done[d.key] + Number(d.advance));
    render.moved = false;
    render();
    const next = document.querySelector(".run-steps .current .btn-primary") || document.querySelector(".runner h1");
    if (next) { if (next.tagName === "H1") next.setAttribute("tabindex", "-1"); next.focus(); }
  } else if (d.restart !== undefined) {
    state.done[d.restart] = 0;
    render();
  } else if (d.connect !== undefined) {
    state.connectStep = Math.max(0, Math.min(CONNECT.length - 1, state.connectStep + Number(d.connect)));
    render();
  } else if (d.front !== undefined) {
    state.frontTab = d.front;
    render.moved = false;
    render();
  }
});
document.addEventListener("change", (e) => { if (e.target.dataset && e.target.dataset.href) location.hash = e.target.dataset.href; });
document.addEventListener("submit", (e) => { const go = e.target.dataset && e.target.dataset.go; if (go !== undefined) { e.preventDefault(); location.hash = go; } });
window.addEventListener("hashchange", render);
render.moved = false;
render();
