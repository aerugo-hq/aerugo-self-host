"use client";
import { createElement as h, useEffect, useState } from "react";
import { ago, kl } from "../lib/client.js";
import { Icon } from "./icons.js";
import { Alert, Crumbs, FRONT, SkeletonRows, Tier, concept, stamp, tierOf } from "./ui.js";

// History reads like a deploy log: good outcomes green, refusals red, the rest neutral.
const TONE = { indexed: "ok", merged: "ok", refused: "bad", closed: "idle", pr_opened: "idle", reviewed: "ok", bronze_version: "idle" };
const KIND = { bronze_version: "Bronze version", indexed: "Indexed", pr_opened: "Pull request opened", reviewed: "Reviewed",
               merged: "Merged", closed: "Closed", refused: "Refused" };

function split(okf) {
  const m = (okf || "").match(FRONT);
  if (!m) return { front: "", body: okf || "" };
  return { front: m[0].replace(/^---\n/, "").replace(/---[ \t]*\n?$/, "").trim(), body: okf.slice(m[0].length).replace(/^\s+/, "") };
}

export default function ConceptView({ id }) {
  const [state, setState] = useState({ loading: true });
  const load = () => {
    const key = encodeURIComponent(id);
    setState({ loading: true });
    Promise.all([kl(`concepts/${key}`), kl(`concepts/${key}/history`), kl(`concepts/${key}/related`)])
      .then(([c, history, related]) => setState({ c, history, related }))
      .catch((e) => setState({ error: e.message }));
  };
  useEffect(() => { if (id) load(); }, [id]);

  const crumbs = (label) => h(Crumbs, { items: [{ href: "#/map", label: "Map" }, { label, mono: true }] });
  if (state.loading) {
    return h("div", { className: "page" }, crumbs(id),
      h("span", { className: "skeleton", style: { width: "40%", height: 20, marginBottom: 16 } }),
      h("div", { className: "grid-main" }, h("div", { className: "panel" }, h(SkeletonRows, { rows: 6, widths: [90] })),
        h("div", { className: "panel" }, h(SkeletonRows, { rows: 4, widths: [70] }))));
  }
  if (state.error) {
    return h("div", { className: "page" }, crumbs(id), h(Alert, { title: "This page isn't available.", onRetry: load }, state.error));
  }
  const { c, history, related } = state;
  const { front, body } = split(c.okf);
  const events = [...history.events].reverse(); // newest first

  return h("div", { className: "page" },
    crumbs(c.id),
    h("header", { style: { marginBottom: 24 } },
      h("h1", null, c.title),
      h("div", { className: "meta", style: { marginTop: 8 } }, h(Tier, { tier: tierOf(c.tier) }),
        h("span", { className: "mono" }, c.id),
        c.source_version ? h("span", { className: "mono" }, `version ${String(c.source_version).slice(0, 12)}`) : null)),
    h("div", { className: "grid-main" },
      h("article", { className: "panel" },
        h("div", { className: "panel-body" },
          h("div", { className: "prose" }, body),
          front ? h("details", { className: "raw" }, h("summary", null, "OKF metadata"), h("pre", null, front)) : null)),
      h("aside", null,
        h("section", { className: "panel" },
          h("div", { className: "panel-head" }, h("h2", null, "History"), h("span", { className: "faint mono" }, events.length)),
          h("div", { className: "panel-body" },
            events.length ? h("ol", { className: "timeline-list" }, events.map((e, i) => h("li", { key: i },
              h("span", { className: `dot ${TONE[e.kind] || "idle"}` }),
              h("div", null,
                h("b", null, KIND[e.kind] || e.kind.replaceAll("_", " ")),
                h("span", { className: "when", title: new Date(e.at).toLocaleString() },
                  `${stamp(e.at)} · ${ago(e.at)}${e.detail && e.detail.by ? ` · ${e.detail.by}` : ""}`),
                e.detail && typeof e.detail.pr === "string" && e.detail.pr.startsWith("https://")
                  ? h("a", { className: "a small", href: e.detail.pr, target: "_blank", rel: "noreferrer" }, "Pull request ", h(Icon, { name: "external", size: 12 }))
                  : null))))
              : h("p", { className: "muted small" }, "No history yet."))),
        related.related.length ? h("section", { className: "panel" },
          h("div", { className: "panel-head" }, h("h2", null, "Related"), h("span", { className: "faint mono" }, related.related.length)),
          h("div", { className: "panel-body" }, h("ul", { className: "linklist" }, related.related.map((r) => h("li", { key: r.id },
            h("a", { href: concept(r.id) }, h("span", { className: `dot ${tierOf(r.tier)}` }), h("span", null, r.title),
              h("small", null, r.relation))))))) : null)));
}
