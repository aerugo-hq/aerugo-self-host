# ADR 0005: Alerts live in the app

**Status:** accepted, October 2026 (replaces the first version of slice P)

## Context

Slice P first routed alerts the way ops teams usually do: Prometheus alert rules, Alertmanager, and a mail catcher for dev. It worked only while the optional observability stack ran, spread alerts across three tools with three URLs, and put them in front of operators rather than the people who own the knowledge. The people who act on these alerts are org admins and team admins, and they never open Prometheus.

## Decision

Alerts are a feature of the app.

- **Signals.** The API records a signal where something worth counting happens: rows the post-check dropped (ACL drift), sources withheld from models, server errors, rate-limit refusals, spent budgets. A signal is a kind, a subject (a surface, route, or team slug), and a count: ids only, never content.
- **Evaluation.** On every scheduler tick, and on demand, the worker evaluates rules over signals and its own tables (connection syncs, job outcomes). A condition that holds raises an alert or keeps it firing, one per rule and subject; one that stops holding resolves it.
- **Reading.** Org admins see every alert, a team's admins see their team's, and members see none. Admins acknowledge alerts in the app.
- **Delivery.** An org admin can have page and warn alerts posted to a webhook; Slack's incoming webhooks read the payload's `text`. The URL is a credential, so it lives in the secrets backend.
- **Liveness.** A worker that has stopped can't raise its own alert, so the API raises "worker stopped" when the worker's heartbeat is stale.

## Alternatives

- **Prometheus rules and Alertmanager** (P's first version). Standard for ops teams, but it needs the observability stack plus two more services, and it keeps alerts outside the product. Teams that run Prometheus still get every metric (slice G) and can write their own rules on top.
- **A paging service only** (PagerDuty and the like). Costs money and still keeps alerts outside the app. The webhook covers it for teams that want it.

## Consequences

- No new infrastructure: alerts are four tables, a worker pass, and API endpoints, and smoke tests them on every run without the observability stack.
- Detection runs at the scheduler tick (60 seconds by default), or immediately through `POST /v1/alerts/evaluate`.
- Rules and thresholds live in code (`kl/alerting.py`), tunable through `KL_ALERT_*` variables. Per-org tuning in the app can come later.
- A signal row is written only when something worth counting happens, not on every request, and signals are kept for 7 days.
- The API and worker post to whatever URL an org admin sets, so production deployments should limit outbound traffic to the destinations they expect.
