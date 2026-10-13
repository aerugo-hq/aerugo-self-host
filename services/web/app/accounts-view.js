"use client";
// Connected accounts: proving which source identities are yours (GitHub, and Google once an org admin sets up its
// sign-in), so content synced from them shows up for you, still within each source's own permissions.
import { createElement as h, useEffect, useState } from "react";
import { ago, kl } from "../lib/client.js";
import { Icon } from "./icons.js";
import { Alert, Empty, PageHead, SkeletonRows, Spinner, Table } from "./ui.js";

const isGoogle = (source) => source.startsWith("ext:google.");
const provider = (source) => (isGoogle(source) ? "Google" : "GitHub");
const label = (source) => (isGoogle(source) ? source.slice("ext:google.".length) : source.replace(/^ext:github\./, "@"));

export default function AccountsView({ outcome }) {
  const [links, setLinks] = useState(null);
  const [google, setGoogle] = useState(false); // whether Google sign-in is set up
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const load = () => kl("identities").then((r) => { setLinks(r.identities); setError(null); }).catch((e) => setError(e.message));
  useEffect(() => {
    load();
    kl("identities/google/client").then((r) => setGoogle(Boolean(r.configured))).catch(() => setGoogle(false));
  }, []);
  const [kind, ...rest] = (outcome || "").split(":");
  const detail = rest.join(":");

  async function link(source) {
    setBusy(source);
    setError(null);
    try {
      const r = await kl(`identities/${source}/start`, { method: "POST" });
      window.location.assign(r.url); // off to the provider's sign-in; it sends you back to /link/<source>/callback
    } catch (e) {
      setError(e.message);
      setBusy(null);
    }
  }
  async function unlink(source) {
    setError(null);
    try {
      await kl(`identities/${encodeURIComponent(source)}`, { method: "DELETE" });
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  const button = (source, name, primary) => h("button", { type: "button", className: primary ? "btn btn-primary" : "btn",
    disabled: busy !== null, onClick: () => link(source) }, busy === source ? h(Spinner) : h(Icon, { name: "link", size: 14 }), `Link ${name}`);
  const buttons = h("div", { className: "actions" }, button("github", "GitHub", true), google ? button("google", "Google", false) : null);
  return h("div", { className: "page narrow" },
    h(PageHead, { title: "Connected accounts",
                  lede: "Link an account by signing in to it. Documents synced from it then show up for you, still within its own permissions." }, buttons),
    kind === "linked" ? h(Alert, { tone: "ok", title: "Linked." }, `${provider(detail)} ${label(detail)} is now yours here.`) : null,
    kind === "error" ? h(Alert, { title: "Not linked." }, detail) : null,
    error ? h(Alert, { onRetry: load }, error) : null,
    h("section", { className: "panel", style: { marginTop: kind || error ? 16 : 0 } },
      links === null && !error ? h(SkeletonRows, { rows: 2, widths: [30, 20, 14] })
        : links && links.length ? h(Table, null,
            h("thead", null, h("tr", null, h("th", null, "Account"), h("th", { className: "hide-sm" }, "Verified by"), h("th", { className: "num" }, "Linked"),
              h("th", { className: "actions-cell" }, h("span", { className: "sr-only" }, "Actions")))),
            h("tbody", null, links.map((l) => h("tr", { key: l.source },
              h("td", null, h("span", { className: "title" }, `${provider(l.source)} `), h("span", { className: "mono" }, label(l.source))),
              h("td", { className: "hide-sm mono muted" }, l.verified_by),
              h("td", { className: "num", title: new Date(l.linked_at).toLocaleString() }, ago(l.linked_at)),
              h("td", { className: "actions-cell" }, h("button", { type: "button", className: "btn btn-ghost btn-sm", onClick: () => unlink(l.source) }, "Unlink"))))))
          : links ? h(Empty, { action: buttons },
              "No accounts linked yet. Synced documents stay hidden until you link the accounts they're shared with.") : null));
}
