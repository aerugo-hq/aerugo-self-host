"use client";
// Shared building blocks, so every screen states things the same way: status is a dot and a word,
// loading is a skeleton of the real layout, empty says what's missing and the one thing to do, errors offer a retry.
import { createElement as h, useEffect, useRef } from "react";
import { Icon } from "./icons.js";

export const TIERS = {
  gold: { label: "Gold", hint: "Verified by a person", color: "#13403a" },          // Verdigris tones: the darker, the more reviewed
  silver: { label: "Silver", hint: "Reviewed and merged", color: "#2f7f6f" },
  evidence: { label: "Evidence", hint: "Synced from your sources", color: "#7d8a99" },  // neutral, as --evidence (slice D1)
  entity: { label: "Entities", hint: "People, systems, and teams your documents name", color: "#a4572a" }, // copper
};
export const tierOf = (t) => (TIERS[t] ? t : "evidence"); // bronze concepts are evidence
// OKF frontmatter, including an empty one ("---\n---"), which some sources have.
export const FRONT = /^---\n(?:[\s\S]*?\n)?---[ \t]*\n?/;
export const concept = (id) => `#/concepts/${encodeURIComponent(id)}`;

export function Tier({ tier }) {
  const t = tierOf(tier);
  return h("span", { className: "status", title: TIERS[t].hint }, h("span", { className: `dot ${t}` }), TIERS[t].label);
}

export function Status({ tone = "idle", children }) {
  return h("span", { className: "status" }, h("span", { className: `dot ${tone}` }), children);
}

export const Spinner = () => h("span", { className: "spin", "aria-hidden": true });

// Placeholder rows shaped like the table they stand in for.
export function SkeletonRows({ rows = 4, widths = [40, 20, 14] }) {
  return h("div", { className: "skeleton-rows", "aria-busy": "true", "aria-label": "Loading" },
    Array.from({ length: rows }, (_, i) => h("div", { key: i, className: "row" },
      widths.map((w, j) => h("span", { key: j, className: "skeleton", style: { width: `${Math.max(8, w - ((i * 7 + j * 5) % 9))}%` } })))));
}

// Empty, after Datavine (slice D3): a quiet icon, a title when one helps, what's missing in a sentence, and the one
// thing to do next.
export function Empty({ children, action, title, icon = "docs" }) {
  return h("div", { className: "empty" },
    h("span", { className: "empty-icon", "aria-hidden": true }, h(Icon, { name: icon, size: 28 })),
    title ? h("b", { className: "empty-title" }, title) : null,
    h("span", { className: "empty-text" }, children),
    action || null);
}

export function Alert({ tone = "error", title, children, onRetry }) {
  const dot = { error: "bad", ok: "ok", warn: "warn" }[tone] || "idle";
  return h("div", { className: `alert ${tone}`, role: tone === "error" ? "alert" : "status" },
    h("span", { className: `dot ${dot}` }),
    h("div", { className: "alert-body" }, title ? h("b", null, title, " ") : null, children),
    onRetry ? h("button", { type: "button", className: "btn btn-sm", onClick: onRetry }, "Retry") : null);
}

export function PageHead({ title, lede, children }) {
  return h("div", { className: "page-head" },
    h("div", null, h("h1", null, title), lede ? h("p", { className: "lede" }, lede) : null),
    children ? h("div", { className: "actions" }, children) : null);
}

export function Crumbs({ items }) {
  return h("nav", { className: "crumbs", "aria-label": "Breadcrumb" }, items.flatMap((item, i) => [
    i ? h("span", { key: `s${i}`, className: "sep", "aria-hidden": true }, "/") : null,
    item.href ? h("a", { key: i, href: item.href }, item.label)
      : h("span", { key: i, className: item.mono ? "mono" : "", "aria-current": "page" }, item.label),
  ]));
}

// One team at a time: a segmented control for a few teams, a select for many.
export function TeamPicker({ teams, value, onChange }) {
  if (teams.length > 4) {
    return h("select", { className: "select", value, onChange: (e) => onChange(e.target.value), "aria-label": "Team" },
      teams.map((t) => h("option", { key: t, value: t }, t)));
  }
  return h("div", { className: "seg", role: "group", "aria-label": "Team" }, teams.map((t) => h("button", {
    key: t, type: "button", "aria-pressed": value === t, onClick: () => onChange(t),
  }, t)));
}

// Every table scrolls inside its own panel on narrow screens, never the page.
export const Table = ({ className = "tbl", children }) => h("div", { className: "tbl-wrap" }, h("table", { className }, children));

export const stamp = (iso) => new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
export const ArrowIcon = () => h(Icon, { name: "arrowRight", size: 14 });


// A page's sections as tabs. Each section is its own address (#/admin/people), so back, forward, and links all work.
export function SectionNav({ base, sections, current, label }) {
  return h("nav", { className: "section-nav", "aria-label": label }, sections.map(([id, name, hint]) => h("a", {
    key: id, href: `${base}/${id}`, title: hint, "aria-current": id === current ? "page" : undefined,
  }, name)));
}

// Confirmations are modals: the page waits until you choose. Focus starts on Cancel, so Enter never destroys by
// accident; Escape backs out, Tab stays inside, and focus returns where it was.
export function Confirm({ title, children, action, onConfirm, onCancel }) {
  const cancel = useRef(null);
  const confirm = useRef(null);
  useEffect(() => {
    const before = document.activeElement;
    if (cancel.current) cancel.current.focus();
    const keys = (e) => {
      if (e.key === "Escape") { e.preventDefault(); onCancel(); }
      if (e.key === "Tab") {
        e.preventDefault();
        (document.activeElement === cancel.current ? confirm.current : cancel.current).focus();
      }
    };
    window.addEventListener("keydown", keys);
    return () => { window.removeEventListener("keydown", keys); if (before && before.focus) before.focus(); };
  }, []);
  return h("div", { className: "overlay", onMouseDown: (e) => { if (e.target === e.currentTarget) onCancel(); } },
    h("div", { className: "modal", role: "alertdialog", "aria-modal": "true", "aria-labelledby": "confirm-title", "aria-describedby": "confirm-body" },
      h("h2", { id: "confirm-title" }, title),
      h("p", { id: "confirm-body" }, children),
      h("div", { className: "modal-actions" },
        h("button", { type: "button", className: "btn", ref: cancel, onClick: onCancel }, "Cancel"),
        h("button", { type: "button", className: "btn btn-danger-solid", ref: confirm, onClick: onConfirm }, action))));
}
