"""Alerts in the app (slice P, ADR 0005).

The API records signals as things happen: rows the post-check dropped (ACL drift), sources withheld from
models, server errors, rate-limit refusals, spent budgets. A signal is a kind, a subject (a surface, route,
or team slug), and a count, never content. On every scheduler tick the worker turns signals and the
database's own state (connection syncs, job outcomes) into alerts. A condition that holds raises an alert
or keeps it firing; one that no longer holds resolves it. Admins read and acknowledge alerts in the app,
and an org admin can have firing page and warn alerts posted to a webhook. Slack's incoming webhooks read
the payload's `text`; anything else can read its `alert`.
"""
import json
import logging
import os

import httpx

log = logging.getLogger("kl.alerting")
DRIFT_MIN = int(os.environ.get("KL_ALERT_DRIFT_MIN", "5"))  # dropped rows in 5 minutes
ERRORS_MIN = int(os.environ.get("KL_ALERT_ERRORS_MIN", "5"))  # server errors in 5 minutes
WITHHELD_MIN = int(os.environ.get("KL_ALERT_WITHHELD_MIN", "20"))  # withheld sources in 15 minutes...
WITHHELD_SURGE = 3.0  # ...at three times the rate of the six hours before
FIELDS = ("id", "rule", "subject", "workspace", "severity", "status", "summary", "details", "first_seen",
          "last_seen", "resolved_at", "acknowledged_by", "acknowledged_at")


def signal(conn, kind: str, subject: str, n: int = 1) -> None:
    """Record a signal inside the caller's transaction. A savepoint keeps a failure here from failing the
    request it observes: alerting must never break the thing it watches."""
    if n <= 0:
        return
    try:
        with conn.transaction():
            conn.execute("INSERT INTO signals (org_id, kind, subject, n) VALUES (kl.current_org(), %s, %s, %s)",
                         (kind, subject, n))
    except Exception as e:  # noqa: BLE001
        log.warning("signal %s not recorded (%s)", kind, type(e).__name__)


# ------------------------------------------------------------------ rules
def conditions(conn, resync_minutes: float) -> list[dict]:
    """Every alert condition that holds right now: rule, subject, team (None for the whole org), severity,
    a one-line summary, and details made of ids and counts."""
    found = []

    def hold(rule, subject, severity, summary, workspace=None, **details):
        found.append({"rule": rule, "subject": subject, "workspace": workspace, "severity": severity,
                      "summary": summary, "details": details})

    for r in conn.execute(
            "SELECT subject, sum(n)::int AS n FROM signals WHERE kind = 'drift' AND at > now() - interval '5 minutes' "
            "GROUP BY subject HAVING sum(n) >= %s", (DRIFT_MIN,)).fetchall():
        hold("acl_drift", r["subject"], "page",
             f"ACL drift on {r['subject']}: {r['n']} rows in 5 minutes passed the index's permissions but failed "
             "the authorization check. Nothing leaked; check recent syncs and permission changes.", dropped=r["n"])

    errors = conn.execute(
        "SELECT sum(n)::int AS n, (array_agg(subject ORDER BY n DESC))[1] AS worst FROM signals "
        "WHERE kind = 'server_error' AND at > now() - interval '5 minutes'").fetchone()
    if errors and (errors["n"] or 0) >= ERRORS_MIN:
        hold("server_errors", "api", "page",
             f"The API answered {errors['n']} requests with server errors in 5 minutes, most on {errors['worst']}.",
             errors=errors["n"], route=errors["worst"])

    for r in conn.execute(
            """SELECT subject,
                      coalesce(sum(n) FILTER (WHERE at > now() - interval '15 minutes'), 0)::int AS recent,
                      coalesce(sum(n) FILTER (WHERE at <= now() - interval '15 minutes'), 0)::int AS before
               FROM signals WHERE kind = 'withheld' AND at > now() - interval '6 hours 15 minutes'
               GROUP BY subject""").fetchall():
        if r["recent"] >= WITHHELD_MIN and r["recent"] / 15 > WITHHELD_SURGE * r["before"] / 360:
            hold("withheld_surge", r["subject"], "warn",
                 f"Sources withheld from models on {r['subject']} are surging: {r['recent']} in 15 minutes. "
                 "A reclassification, or a model allowed too few classifications?", withheld=r["recent"])

    jobs = conn.execute(
        "SELECT count(*) FILTER (WHERE status = 'failed')::int AS failed, "
        "count(*) FILTER (WHERE status IN ('done', 'failed'))::int AS finished "
        "FROM jobs WHERE updated_at > now() - interval '15 minutes'").fetchone()
    if jobs["failed"] >= 3 and jobs["failed"] > 0.2 * jobs["finished"]:
        hold("jobs_failing", "worker", "warn",
             f"{jobs['failed']} of the worker's last {jobs['finished']} jobs failed in 15 minutes.",
             failed=jobs["failed"], finished=jobs["finished"])

    for r in conn.execute(
            """SELECT c.name, w.slug, c.status, extract(epoch FROM now() - c.last_synced_at)::int AS lag
               FROM connections c JOIN workspaces w ON w.id = c.workspace_id
               WHERE c.status <> 'disabled'
                 AND (c.status = 'error' OR c.last_synced_at < now() - make_interval(mins => %s))""",
            (int(3 * resync_minutes),)).fetchall():
        if r["status"] == "error":
            hold("connection_error", r["name"], "warn", f"Connection {r['name']} failed its last sync.",
                 workspace=r["slug"], connection=r["name"])
        else:
            hold("sync_stale", r["name"], "warn",
                 f"Connection {r['name']} hasn't synced in {r['lag'] // 60} minutes, so its changes and permission "
                 "updates aren't arriving.", workspace=r["slug"], connection=r["name"], minutes=r["lag"] // 60)

    for r in conn.execute("SELECT w.slug, count(*)::int AS n FROM knowledge_units u JOIN workspaces w ON w.id = u.owner_team "
                          "WHERE u.status = 'stale' GROUP BY w.slug").fetchall():  # F6: owners told, in the app
        hold("units_stale", r["slug"], "info",
             f"{r['n']} of {r['slug']}'s knowledge units are due for review: their review came due or their evidence changed. "
             "The team's review queue lists them; confirming one restarts its review.", workspace=r["slug"], stale=r["n"])

    for r in conn.execute(
            "SELECT subject, sum(n)::int AS n FROM signals WHERE kind = 'budget_exhausted' "
            "AND at > now() - interval '30 minutes' GROUP BY subject").fetchall():
        hold("budget_exhausted", r["subject"], "info",
             f"Team {r['subject']} has spent a model's monthly budget; asks and compiles on it are refused until "
             "the 1st. An org admin can raise it.", workspace=r["subject"], refused=r["n"])
    return found


# ------------------------------------------------------------------ the worker's pass
def evaluate(conn, resync_minutes: float) -> dict:
    """Raise, update, and resolve alerts from what holds now, then post what the webhook hasn't seen."""
    found = {(c["rule"], c["subject"]): c for c in conditions(conn, resync_minutes)}
    raised = resolved = 0
    with conn.transaction():
        firing = {(a["rule"], a["subject"]): a for a in conn.execute(
            "SELECT id, rule, subject FROM alerts WHERE status = 'firing'").fetchall()}
        for key, c in found.items():
            args = (c["workspace"], c["severity"], c["summary"], json.dumps(c["details"]))
            if key in firing:
                conn.execute("UPDATE alerts SET last_seen = now(), workspace = %s, severity = %s, summary = %s, "
                             "details = %s::jsonb WHERE id = %s", (*args, firing[key]["id"]))
            else:
                conn.execute("INSERT INTO alerts (org_id, rule, subject, workspace, severity, status, summary, details) "
                             "VALUES (kl.current_org(), %s, %s, %s, %s, 'firing', %s, %s::jsonb)", (c["rule"], c["subject"], *args))
                raised += 1
        for key, a in firing.items():
            if key not in found:
                conn.execute("UPDATE alerts SET status = 'resolved', resolved_at = now() WHERE id = %s", (a["id"],))
                resolved += 1
        conn.execute("DELETE FROM signals WHERE at < now() - interval '7 days'")
    return {"firing": len(found), "raised": raised, "resolved": resolved, "posted": notify(conn, webhook_url(conn))}


def payload(alert: dict) -> dict:
    """Slack reads `text`; anything else can read `alert`. Ids and counts only, like the alert itself."""
    state = "Resolved" if alert["status"] == "resolved" else {"page": "Page", "warn": "Warning", "info": "Notice"}[alert["severity"]]
    out = {k: (v.isoformat() if hasattr(v, "isoformat") else str(v) if k == "id" else v)
           for k, v in alert.items() if k in FIELDS}
    return {"text": f"[knowledge-layer] {state}: {alert['summary']}", "alert": out}


def post(url: str, body: dict) -> int | None:
    """POST to the webhook; the HTTP status, or None if it couldn't be reached. Never raises."""
    try:
        return httpx.post(url, json=body, timeout=5).status_code
    except httpx.HTTPError as e:
        log.warning("alert webhook unreachable (%s)", type(e).__name__)
        return None


def webhook_url(conn) -> str | None:
    """The org's alert webhook, from the secrets backend (a webhook URL is a credential). None when none
    is set, or when its secret can't be read: a missing secret mustn't stop the alert pass."""
    row = conn.execute("SELECT webhook_ref FROM alert_settings").fetchone()
    if not row or not row["webhook_ref"]:
        return None
    from .vault import vault
    try:
        return vault.get(row["webhook_ref"]).get("url")
    except Exception as e:  # noqa: BLE001
        log.warning("alert webhook secret unreadable (%s)", type(e).__name__)
        return None


def notify(conn, url: str | None) -> int:
    """Post page and warn alerts whose state the webhook hasn't seen: new ones firing, and resolutions of
    ones it saw fire."""
    if not url:
        return 0
    due = conn.execute(
        f"SELECT {', '.join(FIELDS)} FROM alerts WHERE severity IN ('page', 'warn') "
        "AND notified IS DISTINCT FROM status AND (status = 'firing' OR notified = 'firing') "
        "ORDER BY first_seen LIMIT 50").fetchall()
    posted = 0
    for alert in due:
        status = post(url, payload(alert))
        if status is not None and 200 <= status < 300:
            conn.execute("UPDATE alerts SET notified = %s WHERE id = %s", (alert["status"], alert["id"]))
            posted += 1
    return posted
