# ADR 0003: Observability with OpenTelemetry, hand-instrumented, exported to Prometheus, Jaeger, and Grafana

**Status:** accepted (slice G)

## Context

Operators need latency, freshness, and policy decisions as they happen: ACL drift, egress withholds,
rejected webhooks, model use, job outcomes. Telemetry is also a leak channel. URLs carry search text,
exceptions carry document text, and SQL carries values. Whatever we pick must never let content out.

## Decision

**OpenTelemetry SDK, instrumented by hand.** The API, worker, and MCP server export traces and metrics
over OTLP/HTTP. There is no auto-instrumentation: the FastAPI, httpx, and psycopg instrumentors record
URLs with their query strings and SQL statements. Instead `kl/telemetry.py` sets every attribute
explicitly, and the rule is: route templates, ids (workspace slugs, model names, job kinds, connection
names), counts, and durations only. Spans record an exception's type, never its message. Telemetry is off
unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set, and missing libraries make it a no-op, so it can never take
the API down.

**Collector, then Prometheus, Jaeger, and Grafana** (`make obs`, Compose profile `observability`).
The OpenTelemetry Collector receives everything, forwards traces to Jaeger, exposes metrics for
Prometheus, and writes a file export that `make smoke` checks for canary strings: confidential document
text, questions, model answers, and tokens must never appear. Grafana's datasources and dashboard are
provisioned as code. All four are Apache-2.0 or AGPL-3.0 (Grafana) open source and run in the cell, so
telemetry never leaves the customer's environment.

## Alternatives considered

- **Auto-instrumentation.** Less code, but it captures exactly what the content rule forbids, and
  filtering after the fact is fragile.
- **Grafana's all-in-one LGTM image.** One container, but its built-in collector can't add the file
  export the canary test needs, and its versions move together.
- **A hosted backend (Datadog, Honeycomb, Grafana Cloud).** Good products, but telemetry would leave the
  customer's cell, which regulated customers won't accept by default. The collector can add one later as
  an exporter for customers who opt in.

## Consequences

- Cells that want telemetry run four more containers. Production cells will size and retain them (slice E).
- New code paths need their own spans and counters; anything not instrumented is simply invisible.
- Grafana's AGPL license applies only to Grafana itself, which we run unmodified.
