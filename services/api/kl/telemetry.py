"""Telemetry (slice G): traces and metrics with OpenTelemetry, off unless OTEL_EXPORTER_OTLP_ENDPOINT is set.

The content rule: telemetry carries route templates, ids (workspace slugs, model names, job kinds,
connection names), counts, and durations. Never document text, search queries, questions, prompts,
answers, or tokens. So there's no auto-instrumentation (it would record URLs with their query strings,
and SQL), spans never record exception messages (only the exception's type), and every attribute is
set by hand here or at a call site. smoke.py checks the exported data for canary strings.

When the OpenTelemetry libraries are missing, or no endpoint is set, everything here does nothing.
"""
import contextlib
import logging
import os

log = logging.getLogger("kl.telemetry")
ENABLED = bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))

try:
    from opentelemetry import metrics, trace
    from opentelemetry.trace import SpanKind, Status, StatusCode
except ImportError:  # optional at import time: tools and tests run without it
    metrics = trace = None

_started = False
GAUGES: dict[str, dict] = {"queue": {}, "lag": {}}  # filled by the worker, read by observable gauges


def setup(service: str) -> None:
    """Install the exporters once per process. OTEL_SERVICE_NAME, when set, names the service."""
    global _started
    if _started or not ENABLED or trace is None:
        return
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME") or service})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(),
                                           export_interval_millis=int(os.environ.get("OTEL_METRIC_EXPORT_INTERVAL", "15000")))
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
    _started = True
    log.info("telemetry on: %s exports to %s", resource.attributes["service.name"], os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"])


class _Noop:
    def add(self, *_args, **_kwargs):
        pass

    def record(self, *_args, **_kwargs):
        pass


def _instruments() -> dict:
    names = ("http", "drift", "withheld", "webhooks", "tokens", "jobs", "job_time", "ratelimit", "budget")
    if metrics is None:
        return {name: _Noop() for name in names}
    meter = metrics.get_meter("kl")  # a proxy until setup() installs the real provider
    return {
        "http": meter.create_histogram("kl.http.server.duration", unit="s", description="Request latency by route template"),
        "drift": meter.create_counter("kl.acl_drift.drops", description="Rows the index allowed but OpenFGA denied"),
        "withheld": meter.create_counter("kl.egress.withheld", description="Sources withheld from a model that may not receive them"),
        "webhooks": meter.create_counter("kl.webhooks.rejected", description="Webhook deliveries refused, by reason"),
        "tokens": meter.create_counter("kl.model.tokens", description="Model tokens by workspace, model, and kind"),
        "jobs": meter.create_counter("kl.jobs", description="Finished jobs by kind and outcome"),
        "job_time": meter.create_histogram("kl.job.duration", unit="s", description="Job run time by kind"),
        "ratelimit": meter.create_counter("kl.ratelimit.rejected", description="Requests refused for being over a rate limit, by route"),
        "budget": meter.create_counter("kl.budget.refused", description="Model calls refused because the model's monthly budget is spent"),
    }


_I = _instruments()


def http_request(route: str, method: str, status: int, seconds: float) -> None:
    _I["http"].record(seconds, {"route": route, "method": method, "status": status})


def drift(count: int, surface: str) -> None:
    if count:
        _I["drift"].add(count, {"surface": surface})


def withheld(count: int, surface: str) -> None:
    if count:
        _I["withheld"].add(count, {"surface": surface})


def webhook_rejected(reason: str) -> None:
    _I["webhooks"].add(1, {"reason": reason})


def rate_limited(route: str) -> None:
    _I["ratelimit"].add(1, {"route": route})


def budget_refused(workspace: str) -> None:
    _I["budget"].add(1, {"workspace": workspace})


def tokens(usage: dict, workspace: str, model: str) -> None:
    for kind in ("prompt", "completion"):
        if usage.get(f"{kind}_tokens"):
            _I["tokens"].add(int(usage[f"{kind}_tokens"]), {"workspace": workspace, "model": model, "kind": kind})


def job_finished(kind: str, outcome: str, seconds: float) -> None:
    _I["jobs"].add(1, {"kind": kind, "outcome": outcome})
    _I["job_time"].record(seconds, {"kind": kind})


@contextlib.contextmanager
def span(name: str, server: bool = False, **attributes):
    """A span carrying only the attributes given here. Errors set the status to the exception's type,
    never its message."""
    if trace is None:
        yield None
        return
    tracer = trace.get_tracer("kl")
    with tracer.start_as_current_span(name, kind=SpanKind.SERVER if server else SpanKind.INTERNAL,
                                      record_exception=False, set_status_on_exception=False) as s:
        for key, value in attributes.items():
            s.set_attribute(key, value)
        try:
            yield s
        except BaseException as e:
            s.set_attribute("error.type", type(e).__name__)
            s.set_status(Status(StatusCode.ERROR, type(e).__name__))
            raise


def set_attributes(s, **attributes) -> None:
    if s is not None:
        for key, value in attributes.items():
            s.set_attribute(key, value)


def register_worker_gauges() -> None:
    if metrics is None or not ENABLED:
        return
    from opentelemetry.metrics import Observation
    meter = metrics.get_meter("kl")
    meter.create_observable_gauge("kl.jobs.queue_depth", description="Queued jobs by kind",
                                  callbacks=[lambda _o: [Observation(v, {"kind": k}) for k, v in GAUGES["queue"].items()]])
    meter.create_observable_gauge("kl.sync.lag", unit="s", description="Seconds since each connection last synced",
                                  callbacks=[lambda _o: [Observation(v, {"connection": k}) for k, v in GAUGES["lag"].items()]])


def observe_worker(conn) -> None:
    """Refresh the worker's gauges from the database (called on the scheduler tick)."""
    queue = {r["kind"]: r["n"] for r in conn.execute(
        "SELECT kind, count(*) AS n FROM jobs WHERE status = 'queued' GROUP BY kind").fetchall()}
    GAUGES["queue"] = {**queue, "all": sum(queue.values())}
    GAUGES["lag"] = {r["name"]: float(r["lag"]) for r in conn.execute(
        "SELECT name, extract(epoch FROM now() - last_synced_at) AS lag FROM connections "
        "WHERE last_synced_at IS NOT NULL").fetchall()}
