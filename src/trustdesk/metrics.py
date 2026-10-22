"""Prometheus metrics for this app, using the same metric names every app in the family exposes
(http_requests_total, http_request_duration_seconds, db_up, app_info, process_*), so one agent preset reads them all."""
from prometheus_client import Counter, Histogram
from prometheus_client.core import GaugeMetricFamily

from .core import metrics as _kit

APP = "trustdesk"
REGISTRY = _kit.registry          # the kit's registry already carries http_requests_total, http_request_duration_seconds, app_info, process_*
observe = _kit.observe
render = _kit.render
SEARCHES = Counter("trustdesk_searches_total", "Searches and questions answered", ["kind"], registry=REGISTRY)
VALIDATIONS = Counter("trustdesk_validations_total", "AI pre-validations finished", ["state", "recommendation"], registry=REGISTRY)
VALIDATION_SECONDS = Histogram("trustdesk_validation_duration_seconds", "AI pre-validation duration", registry=REGISTRY,
                               buckets=(0.5, 1, 2.5, 5, 10, 20, 40, 80, 160))


class DatabaseCollector:
    """Evaluated at scrape time: is the database reachable, and how big is the knowledge base."""

    def collect(self):
        from .db import connect

        up, docs = 0, 0
        try:
            with connect(retries=1) as conn:
                docs = conn.execute("SELECT count(*) AS n FROM kb_document").fetchone()["n"]
            up = 1
        except Exception:  # noqa: BLE001 - a scrape must never fail because the database is down; that is the signal
            pass
        by_status: dict[str, int] = {}
        oldest = 0.0
        try:
            with connect(retries=1) as conn:
                by_status = {r["status"]: r["n"] for r in conn.execute(
                    "SELECT status, count(*) AS n FROM request GROUP BY status")}
                oldest = float(conn.execute(
                    "SELECT coalesce(extract(epoch FROM now() - min(submitted_at)), 0) AS s FROM request "
                    "WHERE status IN ('submitted','pending_review','needs_info')").fetchone()["s"])
        except Exception:  # noqa: BLE001 - tables may not exist yet; this must not flip db_up
            pass
        g = GaugeMetricFamily("db_up", "1 if the database answers", labels=["app"])
        g.add_metric([APP], up)
        yield g
        o = GaugeMetricFamily("trustdesk_documents", "Documents in the knowledge base", labels=["app"])
        o.add_metric([APP], docs)
        yield o
        r = GaugeMetricFamily("trustdesk_requests", "Requests by status", labels=["app", "status"])
        for status, n in by_status.items():
            r.add_metric([APP, status], n)
        yield r
        a = GaugeMetricFamily("trustdesk_oldest_open_request_seconds", "Age of the oldest request awaiting a decision",
                              labels=["app"])
        a.add_metric([APP], oldest)
        yield a


REGISTRY.register(DatabaseCollector())
