"""Dependency-free HTTP RED (Rate, Errors, Duration) metrics for FastAPI
services (MASTER-FR-050). No prometheus_client dependency — the registry renders
Prometheus text exposition directly (same approach as ai-gateway's in-house
Metrics), so it works in every service venv without new installs.

Usage in app/main.py::

    from windrose_common.metricsx import RedMiddleware, metrics_asgi_app, REGISTRY
    app.add_middleware(RedMiddleware, service="dataset-service")
    # in the /metrics route:  return PlainTextResponse(REGISTRY.render())
"""

from __future__ import annotations

import time
from threading import Lock

# Latency histogram buckets (seconds), matching the Go RED middleware.
_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class _Registry:
    """Process-wide RED registry. Thread-safe; renders Prometheus text."""

    def __init__(self) -> None:
        self._lock = Lock()
        # key = (method, route, status) -> count
        self._req_total: dict[tuple[str, str, str], int] = {}
        # key = (method, route) -> [bucket_counts..., +inf], sum, count
        self._hist: dict[tuple[str, str], list] = {}
        self._inflight = 0

    def observe(self, method: str, route: str, status: int, dur_s: float) -> None:
        st = str(status)
        with self._lock:
            self._req_total[(method, route, st)] = self._req_total.get((method, route, st), 0) + 1
            h = self._hist.get((method, route))
            if h is None:
                h = [[0] * (len(_BUCKETS) + 1), 0.0, 0]  # bucket counts (+inf), sum, count
                self._hist[(method, route)] = h
            buckets, total, count = h
            for i, b in enumerate(_BUCKETS):
                if dur_s <= b:
                    buckets[i] += 1
            buckets[len(_BUCKETS)] += 1  # +Inf
            h[1] = total + dur_s
            h[2] = count + 1

    def inc_inflight(self, delta: int) -> None:
        with self._lock:
            self._inflight += delta

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            lines.append("# HELP http_requests_total HTTP requests by method, route and status.")
            lines.append("# TYPE http_requests_total counter")
            for (method, route, st), v in sorted(self._req_total.items()):
                lines.append(
                    f'http_requests_total{{method="{method}",route="{route}",status="{st}"}} {v}'
                )
            lines.append("# HELP http_request_duration_seconds HTTP request latency in seconds.")
            lines.append("# TYPE http_request_duration_seconds histogram")
            for (method, route), (buckets, total, count) in sorted(self._hist.items()):
                cumulative = 0
                for i, b in enumerate(_BUCKETS):
                    cumulative += buckets[i]
                    lines.append(
                        f'http_request_duration_seconds_bucket{{method="{method}",'
                        f'route="{route}",le="{b}"}} {cumulative}'
                    )
                cumulative += buckets[len(_BUCKETS)]
                lines.append(
                    f'http_request_duration_seconds_bucket{{method="{method}",'
                    f'route="{route}",le="+Inf"}} {cumulative}'
                )
                lines.append(
                    f'http_request_duration_seconds_sum{{method="{method}",route="{route}"}} {total}'
                )
                lines.append(
                    f'http_request_duration_seconds_count{{method="{method}",route="{route}"}} {count}'
                )
            lines.append("# HELP http_requests_in_flight In-flight HTTP requests.")
            lines.append("# TYPE http_requests_in_flight gauge")
            lines.append(f"http_requests_in_flight {self._inflight}")
        return "\n".join(lines) + "\n"


# Shared process-wide registry.
REGISTRY = _Registry()


class RedMiddleware:
    """Starlette/ASGI middleware recording RED metrics. Route label is the
    matched path template (bounded cardinality), not the raw path."""

    def __init__(self, app, service: str = "") -> None:
        self.app = app
        self.service = service

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET")
        start = time.perf_counter()
        REGISTRY.inc_inflight(1)
        status_holder = {"code": 500}

        async def _send(message):
            if message.get("type") == "http.response.start":
                status_holder["code"] = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            REGISTRY.inc_inflight(-1)
            route = _route_label(scope)
            REGISTRY.observe(method, route, status_holder["code"], time.perf_counter() - start)


def _route_label(scope) -> str:
    """Prefer the matched route template (e.g. /datasets/{id}); fall back to a
    bounded 'other' rather than the raw path to avoid label explosion."""
    r = scope.get("route")
    path = getattr(r, "path", None)
    if path:
        return path
    # Starlette sets scope["route"] only after matching; endpoints that 404 have none.
    return scope.get("path", "other") if scope.get("path", "").count("/") <= 2 else "other"


def instrument_app(app, service_name: str) -> None:
    """Best-effort OTel FastAPI + httpx auto-instrumentation. Soft-imports the
    instrumentation packages so services without them installed simply get the
    tracer provider (via configure_tracing) with no auto HTTP spans — never an
    ImportError. Safe to call unconditionally."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        pass
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception:
        pass
