# Runbook: alerts and incidents

Alerts appear on the app's Alerts page (and in Slack or a webhook, if an org admin set one). Each says what's wrong and where. Acknowledge an alert when you start on it, so others see someone has; it resolves on its own once its condition clears.

## What each alert means and what to do

**Permissions drift** (page). Rows passed the index's permissions but failed the authorization check, so the two disagree. Nothing leaked: the post-check dropped them. Find out why before it grows.
1. Check recent syncs (Admin → connections) and recent permission changes in the audit trail (`team.grant`, `team.revoke`).
2. Re-sync the affected connection; a sync re-mirrors its permissions.
3. If drift continues on a document after a re-sync, its source permissions and OpenFGA tuples disagree: compare them, and treat it as a defect.

**Server errors** (page). The API answered several requests with 5xx in five minutes; the alert names the worst route.
1. `docker compose logs api --tail 200` for the error; with `make obs`, Jaeger has the failing requests' traces.
2. Common causes so far: a model key missing from the secrets backend (the response says so), a dependency down (OpenFGA, OpenBao, Postgres).

**Connection failing** (warning). A connection's last sync failed. Check its error in Admin, fix the cause (an expired GitHub App installation, revoked access, a network problem), and sync it again.

**Sync falling behind** (warning). A connection hasn't synced in three re-sync windows, so changes and permission updates aren't arriving. Check that the worker is running and the connection isn't failing.

**Failing jobs** (warning). More than a fifth of the worker's recent jobs failed. Each job's error is in `kl.jobs.last_error`; `docker compose logs worker --tail 200` shows the rest.

**Withheld sources** (warning). Far more content than usual is being kept from models by egress policy. Usually a reclassification (a reviewed personal-data flag, say) or a model registered with too few allowed classifications. Expected after a reclassification; otherwise review model settings.

**Budget spent** (notice). A team's model reached its monthly budget; asks and compiles on it are refused until the 1st. An org admin raises the budget, or the team waits.

**Worker stopped** (page). The worker's heartbeat is stale: nothing syncs, indexes, applies retention, or raises other alerts. `docker compose ps worker`, then `docker compose logs worker --tail 200`; restart it with `docker compose up -d worker`.

## When it's an incident

If customer data may have been exposed, systems are down for users, or an alert doesn't clear after the steps above:

1. Name an incident lead and open a record: start time, what's affected, who's working on it.
2. Preserve evidence before changing anything: export the relevant audit trail and alert history.
3. Contain, fix, and confirm with `make smoke`.
4. Afterwards, write down the cause and add a smoke check or golden question that would have caught it. Personal-data breaches may need notifying within deadlines (GDPR Art. 33: 72 hours); that decision belongs to the incident lead and counsel.
