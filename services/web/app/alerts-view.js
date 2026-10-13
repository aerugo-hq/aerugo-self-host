"use client";
// Alerts (slice P, ADR 0005): what needs attention, raised by the app itself as it watches permissions,
// syncs, jobs, models, and its own errors. Org admins see every alert and choose where page and warning
// alerts are posted (Slack or any webhook); a team's admins see that team's alerts.
import { createElement as h, useEffect, useState } from "react";
import { ago, kl } from "../lib/client.js";
import { Icon } from "./icons.js";
import { Alert, Empty, PageHead, SkeletonRows, Spinner, Status, stamp } from "./ui.js";

const SEVERITY = { page: ["bad", "Page"], warn: ["warn", "Warning"], info: ["idle", "Notice"] };
const RULES = {
  acl_drift: "Permissions drift", server_errors: "Server errors", withheld_surge: "Withheld sources",
  jobs_failing: "Failing jobs", connection_error: "Connection failing", sync_stale: "Sync falling behind",
  budget_exhausted: "Budget spent", worker_down: "Worker stopped",
};

export default function AlertsView({ me, onCount }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [recent, setRecent] = useState(false);
  const [hook, setHook] = useState(null);
  const [url, setUrl] = useState("");
  const [hookNote, setHookNote] = useState(null);
  const [busy, setBusy] = useState(null); // which action is running: an alert id, or "save" / "test" / "remove"

  const load = () => {
    setError(null);
    kl(`alerts?state=${recent ? "recent" : "firing"}`)
      .then((d) => { setData(d); if (onCount) onCount(d.alerts.filter((a) => a.status === "firing").length); })
      .catch((e) => setError(e.message));
  };
  useEffect(load, [recent]);
  useEffect(() => {
    if (me && me.org_admin) kl("alerts/webhook").then(setHook).catch(() => setHook(null));
  }, [me]);

  async function act(key, run) {
    setBusy(key);
    try {
      await run();
    } catch (e) {
      setHookNote(e.message);
    } finally {
      setBusy(null);
    }
  }
  const acknowledge = (id) => act(id, async () => { await kl(`alerts/${id}/acknowledge`, { method: "POST" }); load(); });
  const save = () => act("save", async () => {
    await kl("alerts/webhook", { method: "PUT", body: { url: url.trim() } });
    setHook(await kl("alerts/webhook"));
    setUrl("");
    setHookNote("Saved. Page and warning alerts will be posted there.");
  });
  const test = () => act("test", async () => {
    const r = await kl("alerts/webhook/test", { method: "POST" });
    setHookNote(r.delivered ? "Test message delivered." : `The webhook answered ${r.status ?? "nothing"}; check the URL.`);
  });
  const remove = () => act("remove", async () => {
    await kl("alerts/webhook", { method: "DELETE" });
    setHook(await kl("alerts/webhook"));
    setHookNote("Removed. Alerts stay here in the app.");
  });

  const firing = data ? data.alerts.filter((a) => a.status === "firing") : [];
  const resolved = data ? data.alerts.filter((a) => a.status === "resolved") : [];
  const row = (a) => {
    const [tone, label] = SEVERITY[a.severity] || SEVERITY.info;
    return h("li", { key: a.id, className: `alert-row${a.status === "resolved" ? " resolved" : ""}` },
      h("div", { className: "alert-main" },
        h("div", { className: "alert-title" },
          a.status === "resolved" ? h(Status, { tone: "ok" }, "Resolved") : h(Status, { tone }, label),
          h("b", null, RULES[a.rule] || a.rule)),
        h("p", null, a.summary),
        h("div", { className: "meta" },
          h("span", null, a.workspace ? `Team ${a.workspace}` : "Whole organization"),
          h("span", { title: stamp(a.first_seen) }, a.status === "resolved" ? `Resolved ${ago(a.resolved_at)}` : `Since ${ago(a.first_seen)}`),
          a.acknowledged_by ? h("span", null, `Seen by ${a.acknowledged_by}`) : null)),
      a.status === "firing" && !a.acknowledged_by && a.id !== "worker"
        ? h("button", { type: "button", className: "btn btn-sm", disabled: busy === a.id, onClick: () => acknowledge(a.id) },
            busy === a.id ? h(Spinner) : null, "Acknowledge")
        : null);
  };

  return h("div", { className: "page" },
    h(PageHead, { title: "Alerts", lede: "What needs attention, raised by the app as it watches permissions, syncs, jobs, and models." },
      h("button", { type: "button", className: "toggle", "aria-pressed": recent, onClick: () => setRecent(!recent) }, "Show resolved"),
      h("button", { type: "button", className: "btn btn-icon btn-sm", onClick: load, "aria-label": "Refresh" }, h(Icon, { name: "refresh" }))),
    error ? h(Alert, { title: "Alerts didn't load.", onRetry: load }, error) : null,
    !data && !error ? h("div", { className: "panel" }, h(SkeletonRows, { rows: 3, widths: [48, 24] })) : null,
    data ? h("div", { className: "panel" }, firing.length
      ? h("ul", { className: "alert-list" }, firing.map(row))
      : h(Empty, null, "Nothing needs attention right now.")) : null,
    data && recent && resolved.length ? [
      h("h2", { key: "h", className: "section-title" }, "Resolved in the last 7 days"),
      h("div", { key: "p", className: "panel" }, h("ul", { className: "alert-list" }, resolved.map(row)))] : null,
    me && me.org_admin ? h("section", { className: "panel webhook-card", "aria-label": "Where alerts are posted" },
      h("h2", null, "Post alerts to Slack or a webhook"),
      h("p", { className: "faint small" }, hook && hook.set
        ? `Page and warning alerts are posted to your webhook (set by ${hook.set_by}, ${stamp(hook.set_at)}). Notices stay here.`
        : "Alerts appear here. To also get page and warning alerts in Slack, paste an incoming-webhook URL."),
      h("div", { className: "webhook-form" },
        h("input", { className: "input", type: "url", value: url, onChange: (e) => setUrl(e.target.value), "aria-label": "Webhook URL",
                     placeholder: hook && hook.set ? "Paste a new URL to replace it" : "https://hooks.slack.com/services/…" }),
        h("button", { type: "button", className: "btn btn-primary btn-sm", disabled: !/^https?:\/\/\S+$/.test(url.trim()) || busy === "save", onClick: save },
          busy === "save" ? h(Spinner) : null, hook && hook.set ? "Replace" : "Save"),
        hook && hook.set ? h("button", { type: "button", className: "btn btn-sm", disabled: busy === "test", onClick: test },
          busy === "test" ? h(Spinner) : null, "Send test") : null,
        hook && hook.set ? h("button", { type: "button", className: "btn btn-ghost btn-sm", disabled: busy === "remove", onClick: remove }, "Remove") : null),
      hookNote ? h("p", { className: "small", role: "status" }, hookNote) : null) : null);
}
