"use client";
// Handovers (C4b): someone leaving or changing roles answers prompts built from what they own, and shares the answers
// they choose; their successor and lead see how far it's come and confirm what's shared. Drafts are the person's alone
// (the API's own rule), so this page shows anyone else counts, never a draft.
import { createElement as h, useEffect, useRef, useState } from "react";
import { ago, kl } from "../lib/client.js";
import { Alert, Confirm, Empty, PageHead, SkeletonRows, Spinner, Status, Table } from "./ui.js";

const enc = encodeURIComponent;
const REASONS = [["leaving", "Leaving"], ["changing roles", "Changing roles"]];
const POLL_MS = 2000;
const POLL_TRIES = 30; // a shared answer is kept by the worker, usually within seconds

// "payments, dave, and alice": who reads an answer shared with the handover's team and the people it names.
export function audienceOf(handover, names = {}) {
  const who = [names[handover.team] || handover.team, handover.successor, handover.lead].filter(Boolean);
  return who.length < 3 ? who.join(" and ") : `${who.slice(0, -1).join(", ")}, and ${who[who.length - 1]}`;
}

function HandoverStatus({ handover }) {
  const [label, tone] = {
    requested: handover.mine ? ["Waiting for you", "warn"] : ["Waiting for them", "idle"],
    open: ["Open", "pending"], declined: ["Declined", "idle"], closed: ["Closed", "idle"],
  }[handover.status] || [handover.status, "idle"];
  return h(Status, { tone }, label);
}

export default function HandoversView({ me, id, names }) {
  return id ? h(Interview, { me, id, names }) : h(HandoverList, { me, names });
}

// ------------------------------------------------------------------ the list
function HandoverList({ me, names }) {
  const [items, setItems] = useState(null);
  const [review, setReview] = useState(null);
  const [error, setError] = useState(null);
  const [starting, setStarting] = useState(false);
  const [done, setDone] = useState(null); // what the last action did, said beside it
  const [declining, setDeclining] = useState(null);

  const load = () => {
    setError(null);
    kl("handovers").then((r) => setItems(r.handovers)).catch((e) => setError(e.message));
    kl("handovers/review").then((r) => setReview(r.units)).catch(() => setReview([]));
  };
  useEffect(load, []);

  async function accept(hv) {
    try {
      await kl(`handovers/${enc(hv.id)}/accept`, { method: "POST" });
      window.location.hash = `#/handovers/${hv.id}`;
    } catch (e) { setError(e.message); }
  }
  async function decline() {
    const hv = declining;
    setDeclining(null);
    try {
      await kl(`handovers/${enc(hv.id)}/decline`, { method: "POST" });
      setDone(`Declined ${hv.requested_by}'s request.`);
      load();
    } catch (e) { setError(e.message); }
  }

  if (!me) return h("div", { className: "page" }, h("div", { className: "panel" }, h(SkeletonRows, { rows: 4 })));
  const mine = (items || []).filter((x) => x.mine);
  const others = (items || []).filter((x) => !x.mine);
  return h("div", { className: "page" },
    h(PageHead, { title: "Handovers",
                  lede: "When you leave or change roles, hand over what isn't written down. Your drafts stay yours until you share them." },
      starting ? null : h("button", { type: "button", className: "btn btn-primary", onClick: () => { setStarting(true); setDone(null); } },
        "Start a handover")),
    error ? h(Alert, { title: "That didn't work.", onRetry: load }, error) : null,
    done ? h("p", { className: "handover-done", role: "status" }, h(Status, { tone: "ok" }, done)) : null,
    starting ? h(StartForm, { me, names, onCancel: () => setStarting(false),
                              onAsked: (person) => { setStarting(false); setDone(`Asked ${person} for a handover. It starts when they accept.`); load(); } }) : null,
    review && review.length ? h(Review, { units: review, names, onSettled: (text) => { setDone(text); load(); } }) : null,
    h("section", { className: "panel" },
      h("div", { className: "panel-head" }, h("h2", null, "Your handovers")),
      items === null ? h(SkeletonRows, { rows: 2, widths: [14, 20, 18, 14] })
        : mine.length ? h(Table, null,
            h("thead", null, h("tr", null, h("th", { className: "w-status" }, "Status"), h("th", null, "Team"),
              h("th", { className: "hide-sm" }, "Why"), h("th", { className: "hide-sm" }, "Successor"), h("th", { className: "hide-sm" }, "Lead"),
              h("th", { className: "num" }, "Shared"),
              h("th", { className: "actions-cell" }, h("span", { className: "sr-only" }, "Actions")))),
            h("tbody", null, mine.map((x) => h("tr", { key: x.id },
              h("td", null, h(HandoverStatus, { handover: x })),
              h("td", null, names[x.team] || x.team, x.status === "requested" ? h("span", { className: "sub" }, ` asked by ${x.requested_by}`) : null),
              h("td", { className: "hide-sm" }, x.reason),
              h("td", { className: "hide-sm" }, x.successor || "None named"),
              h("td", { className: "hide-sm" }, x.lead || "None named"),
              h("td", { className: "num" }, `${x.shared} of ${x.prompts}`),
              h("td", { className: "actions-cell" },
                x.status === "requested" ? [
                  h("button", { key: "a", type: "button", className: "btn btn-sm", onClick: () => accept(x) }, "Accept"),
                  h("button", { key: "d", type: "button", className: "btn btn-sm btn-ghost", onClick: () => setDeclining(x) }, "Decline")]
                  : x.status === "open" || x.status === "closed"
                    ? h("a", { className: "btn btn-sm", href: `#/handovers/${x.id}` }, x.status === "open" ? "Continue" : "Open")
                    : null)))))
          : h(Empty, { icon: "handover" }, "You haven't started a handover. Start one when you're leaving or changing roles.")),
    h("section", { className: "panel" },
      h("div", { className: "panel-head" }, h("h2", null, "Naming you, or asked by you"),
        h("p", null, "How far each has come. Drafts are their person's alone.")),
      items === null ? h(SkeletonRows, { rows: 2, widths: [16, 14, 14, 18] })
        : others.length ? h(Table, null,
            h("thead", null, h("tr", null, h("th", null, "Person"), h("th", { className: "hide-sm" }, "Team"), h("th", null, "Your part"),
              h("th", { className: "w-status" }, "Status"), h("th", { className: "num" }, "Shared"), h("th", { className: "num hide-sm w-time" }, "Started"))),
            h("tbody", null, others.map((x) => h("tr", { key: x.id },
              h("td", null, x.person),
              h("td", { className: "hide-sm" }, names[x.team] || x.team),
              h("td", null, [x.successor === me.username && "Successor", x.lead === me.username && "Lead",
                             x.requested_by === me.username && "You asked"].filter(Boolean).join(", ")),
              h("td", null, h(HandoverStatus, { handover: x })),
              h("td", { className: "num" }, `${x.shared} of ${x.prompts}`),
              h("td", { className: "num hide-sm", title: new Date(x.created_at).toLocaleString() }, ago(x.created_at))))))
          : h(Empty, { icon: "user" }, "No handover names you yet. When someone names you as their successor or lead, it shows here.")),
    declining ? h(Confirm, { title: "Decline this handover?", action: "Decline", onConfirm: decline, onCancel: () => setDeclining(null) },
      `${declining.requested_by} asked you for a handover to ${names[declining.team] || declining.team}. Declining ends this request, and nothing of yours is kept.`) : null);
}

function StartForm({ me, names, onCancel, onAsked }) {
  const teams = me.workspaces;
  const [whose, setWhose] = useState("mine");
  const [form, setForm] = useState({ person: "", team: teams[0] || "", reason: "changing roles", successor: "", lead: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const first = useRef(null);
  useEffect(() => { if (first.current) first.current.focus(); }, [whose]);
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });
  const someone = whose === "someone";
  const ready = form.team && (!someone || form.person.trim());

  async function submit(event) {
    event.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    const body = { team: form.team, reason: form.reason };
    for (const key of ["successor", "lead", ...(someone ? ["person"] : [])]) if (form[key].trim()) body[key] = form[key].trim();
    try {
      const made = await kl("handovers", { method: "POST", body });
      if (someone) onAsked(made.person);
      else window.location.hash = `#/handovers/${made.id}`;
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (!teams.length) {
    return h("section", { className: "panel" }, h(Empty, { icon: "handover", action: h("button", { type: "button", className: "btn btn-sm", onClick: onCancel }, "Close") },
      "A handover belongs to a team, and you aren't on one yet."));
  }
  return h("section", { className: "panel" },
    h("div", { className: "panel-head" }, h("h2", { id: "start-handover" }, "Start a handover")),
    h("form", { className: "panel-body handover-form", onSubmit: submit, "aria-labelledby": "start-handover" },
      h("div", { className: "field" }, h("span", { id: "whose" }, "Whose handover"),
        h("div", { className: "seg", role: "group", "aria-labelledby": "whose" },
          [["mine", "Mine"], ["someone", "Someone on my team"]].map(([value, label]) => h("button", {
            key: value, type: "button", "aria-pressed": whose === value, onClick: () => setWhose(value) }, label)))),
      someone ? h("label", { className: "field" }, h("span", null, "Person"),
        h("input", { ref: first, className: "input", value: form.person, onChange: set("person"), autoComplete: "off", placeholder: "Their username" }),
        h("span", { className: "hint" }, "Their manager or one of the team's admins can ask. It starts when they accept.")) : null,
      h("div", { className: "handover-fields" },
        h("label", { className: "field" }, h("span", null, "Team"),
          h("select", { ref: someone ? null : first, className: "select", value: form.team, onChange: set("team") },
            teams.map((t) => h("option", { key: t, value: t }, names[t] || t)))),
        h("div", { className: "field" }, h("span", { id: "why" }, "Why"),
          h("div", { className: "seg", role: "group", "aria-labelledby": "why" }, REASONS.map(([value, label]) => h("button", {
            key: value, type: "button", "aria-pressed": form.reason === value, onClick: () => setForm({ ...form, reason: value }) }, label))))),
      h("div", { className: "handover-fields" },
        h("label", { className: "field" }, h("span", null, "Successor"),
          h("input", { className: "input", value: form.successor, onChange: set("successor"), autoComplete: "off", placeholder: "Their username" }),
          h("span", { className: "hint" }, "Who takes over. They confirm what's shared.")),
        h("label", { className: "field" }, h("span", null, "Lead"),
          h("input", { className: "input", value: form.lead, onChange: set("lead"), autoComplete: "off", placeholder: "Their username" }),
          h("span", { className: "hint" }, someone ? "You, unless you name someone." : "Your manager, as your directory says, unless you name someone."))),
      error ? h(Alert, { title: "The handover wasn't started." }, error) : null,
      h("div", { className: "handover-actions" },
        h("button", { type: "button", className: "btn", onClick: onCancel }, "Cancel"),
        h("button", { type: "submit", className: "btn btn-primary", disabled: busy || !ready }, busy ? h(Spinner) : null,
          someone ? "Ask for a handover" : "Start my handover"))));
}

// What handovers naming you proposed: confirm it, or say it's not right.
function Review({ units, names, onSettled }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [dismissing, setDismissing] = useState(null);
  async function settle(unit, verb) {
    setBusy(unit.id);
    setError(null);
    try {
      await kl(`units/${enc(unit.id)}/${verb}`, { method: "POST" });
      onSettled(verb === "confirm" ? `Confirmed "${unit.title}".` : `Dismissed "${unit.title}".`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }
  return h("section", { className: "panel" },
    h("div", { className: "panel-head" }, h("h2", null, "Waiting for your review"),
      h("p", null, "Shared in handovers that name you. Once you confirm one, answers can use it.")),
    error ? h("div", { className: "panel-body" }, h(Alert, { title: "That didn't work." }, error)) : null,
    h("ul", { className: "handover-review" }, units.map((u) => h("li", { key: u.id },
      h("div", { className: "handover-review-text" }, h("b", null, u.title), u.text !== u.title ? h("p", null, u.text) : null,
        h("span", { className: "sub" }, `For ${names[u.owner.team] || u.owner.team}`)),
      h("div", { className: "actions" },
        h("button", { type: "button", className: "btn btn-sm", disabled: busy === u.id, onClick: () => settle(u, "confirm") },
          busy === u.id ? h(Spinner) : null, "Confirm"),
        h("button", { type: "button", className: "btn btn-sm btn-ghost", disabled: busy === u.id, onClick: () => setDismissing(u) }, "Not right"))))),
    dismissing ? h(Confirm, { title: "Dismiss this answer?", action: "Dismiss",
                              onConfirm: () => { const u = dismissing; setDismissing(null); settle(u, "retire"); }, onCancel: () => setDismissing(null) },
      `"${dismissing.title}" won't be used in answers. Its history keeps that you dismissed it.`) : null);
}

// ------------------------------------------------------------------ the interview
function Interview({ me, id, names }) {
  const [handover, setHandover] = useState(undefined); // undefined while loading, null when it isn't yours
  const [answers, setAnswers] = useState(null);
  const [error, setError] = useState(null);

  const load = () => {
    setError(null);
    kl("handovers").then((r) => {
      const found = r.handovers.find((x) => x.id === id && x.mine) || null;
      setHandover(found);
      if (found && (found.status === "open" || found.status === "closed")) {
        return kl(`handovers/${enc(id)}/answers`).then((a) => setAnswers(a.answers));
      }
      return null;
    }).catch((e) => setError(e.message));
  };
  useEffect(load, [id]);

  const back = h("a", { className: "btn btn-sm btn-ghost", href: "#/handovers" }, "All handovers");
  if (error) return h("div", { className: "page narrow" }, h(Alert, { title: "Your handover didn't load.", onRetry: load }, error));
  if (handover === undefined || !me) return h("div", { className: "page narrow" }, h("div", { className: "panel" }, h(SkeletonRows, { rows: 5, widths: [70, 50] })));
  if (handover === null) {
    return h("div", { className: "page narrow" }, h("div", { className: "panel" },
      h(Empty, { icon: "shield", action: back },
        "Only the person a handover belongs to can read its drafts. You can see how far it's come on Handovers.")));
  }
  const audience = audienceOf(handover, names);
  const team = names[handover.team] || handover.team;
  const people = [handover.successor && `Successor: ${handover.successor}.`, handover.lead && `Lead: ${handover.lead}.`].filter(Boolean).join(" ");
  const shared = answers ? answers.filter((a) => a.shared).length : handover.shared;
  return h("div", { className: "page narrow" },
    h(PageHead, { title: "Your handover", lede: `${team}, ${handover.reason}. ${people}` }, back),
    handover.status === "requested" ? h(Alert, { tone: "warn", title: `${handover.requested_by} asked you for this handover.` },
      "Accept or decline it on Handovers.")
      : handover.status === "declined" ? h(Alert, { tone: "warn", title: "You declined this handover." }, "Nothing of yours was kept.")
      : null,
    answers ? h("div", null,
      h("p", { className: "handover-intro" },
        `Only you can read your drafts. Share an answer when it's ready: it's kept for the people you choose, and `
        + (handover.successor || handover.lead ? `${[handover.successor, handover.lead].filter(Boolean).join(" or ")} confirms it. `
          : "the team's curators confirm it. ") + "You can withdraw it until you leave."),
      h("p", { className: "handover-progress", role: "status" }, `${shared} of ${answers.length} answers shared.`),
      handover.status === "closed" ? h(Alert, { tone: "warn", title: "This handover is closed." }, "What you shared stays with the people you chose.") : null,
      h("ol", { className: "handover-prompts" }, answers.map((a) => h(Prompt, {
        key: a.id, answer: a, handover, audience, closed: handover.status !== "open",
        onChange: (next) => setAnswers((list) => list.map((x) => (x.id === next.id ? next : x))),
      })))) : null);
}

function Prompt({ answer, handover, audience, closed, onChange }) {
  const [text, setText] = useState(answer.answer || "");
  const [to, setTo] = useState(answer.audience || "named");
  const [state, setState] = useState(null); // { tone, text } said beside the actions
  const [busy, setBusy] = useState(false);
  const [withdrawing, setWithdrawing] = useState(false);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const base = `handovers/${enc(handover.id)}/answers/${enc(answer.id)}`;
  const changed = text !== (answer.answer || "") || to !== (answer.audience || "named");

  // The worker keeps or removes a shared answer: wait until it has, a try every two seconds.
  async function until(shared) {
    for (let i = 0; i < POLL_TRIES && alive.current; i += 1) {
      await new Promise((r) => setTimeout(r, POLL_MS));
      const found = (await kl(`handovers/${enc(handover.id)}/answers`)).answers.find((x) => x.id === answer.id);
      if (found && found.shared === shared) return found;
    }
    return null;
  }
  async function save() {
    await kl(base, { method: "PUT", body: { answer: text, audience: to } });
    onChange({ ...answer, answer: text.trim() || null, audience: to });
  }
  async function act(run) {
    setBusy(true);
    setState(null);
    try { await run(); } catch (e) { setState({ tone: "bad", text: e.message }); } finally { if (alive.current) setBusy(false); }
  }
  const saveDraft = () => act(async () => { await save(); setState({ tone: "ok", text: "Draft saved." }); });
  const share = () => act(async () => {
    if (changed) await save();
    await kl(`${base}/share`, { method: "POST" });
    setState({ tone: "pending", text: "Sharing…" });
    const kept = await until(true);
    if (!alive.current) return;
    if (kept) { onChange(kept); setState(null); } // its status, beside Withdraw, says who reads it now
    else setState({ tone: "warn", text: "Still sharing. It shows as shared once it's kept." });
  });
  const withdraw = () => { setWithdrawing(false); act(async () => {
    await kl(`${base}/withdraw`, { method: "POST" });
    setState({ tone: "pending", text: "Withdrawing…" });
    const draft = await until(false);
    if (!alive.current) return;
    if (draft) { onChange(draft); setState({ tone: "ok", text: "Withdrawn. It's a draft again." }); }
    else setState({ tone: "warn", text: "Still withdrawing. It shows as a draft once it's removed." });
  }); };

  const qid = `prompt-${answer.id}`;
  return h("li", { className: "handover-prompt" },
    h("p", { className: "handover-question", id: qid }, answer.prompt),
    answer.shared
      ? h("div", { className: "handover-shared" }, h("p", null, answer.answer),
          h("div", { className: "handover-bar" },
            h("span", { role: "status" }, h(Status, { tone: "ok" }, `Shared with ${answer.audience === "org" ? "everyone at acme" : audience}`)),
            h("span", { className: "spacer" }),
            state ? h("span", { role: "status" }, h(Status, { tone: state.tone }, state.text)) : null,
            closed ? null : h("button", { type: "button", className: "btn btn-sm btn-ghost", disabled: busy, onClick: () => setWithdrawing(true) }, "Withdraw")))
      : h("div", null,
          h("textarea", { className: "handover-answer", value: text, onChange: (e) => setText(e.target.value), disabled: closed || busy,
                          "aria-labelledby": qid, maxLength: 4000, rows: 3, placeholder: closed ? "" : "Your answer, in your own words" }),
          closed ? null : h("div", { className: "handover-bar" },
            h("span", { className: "handover-to", id: `${qid}-to` }, "Share with"),
            h("div", { className: "seg", role: "group", "aria-labelledby": `${qid}-to` },
              [["named", audience], ["org", "Everyone at acme"]].map(([value, name]) => h("button", {
                key: value, type: "button", "aria-pressed": to === value, onClick: () => setTo(value) }, name))),
            h("span", { className: "spacer" }),
            state ? h("span", { role: "status" }, h(Status, { tone: state.tone }, state.text)) : null,
            h("button", { type: "button", className: "btn btn-sm", disabled: busy || !changed, onClick: saveDraft }, "Save draft"),
            h("button", { type: "button", className: "btn btn-sm", disabled: busy || !text.trim(), onClick: share },
              busy ? h(Spinner) : null, "Share"))),
    withdrawing ? h(Confirm, { title: "Withdraw this answer?", action: "Withdraw", onConfirm: withdraw, onCancel: () => setWithdrawing(false) },
      `It's removed for ${answer.audience === "org" ? "everyone at acme" : audience}, with what it proposed, confirmed or not. It stays here as your draft.`) : null);
}
