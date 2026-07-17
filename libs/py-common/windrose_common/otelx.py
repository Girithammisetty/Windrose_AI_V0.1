"""Thin OpenTelemetry helper: configure a real OTLP/gRPC tracer provider pointed
at the local collector (localhost:4317), or no-op cleanly if telemetry is not
configured. Kept intentionally small — instrumentation call sites stay in the
services; this only owns provider setup + a span context manager.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

_CONFIGURED = False


def configure_tracing(
    service_name: str,
    *,
    endpoint: str | None = None,
    enabled: bool | None = None,
) -> bool:
    """Install an OTLP span exporter. Returns True if tracing was configured.

    ``enabled`` defaults to the ``WINDROSE_OTEL_ENABLED`` env var (off unless set),
    so unit tests never reach the collector. ``endpoint`` defaults to
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` or ``http://localhost:4317``.
    """
    global _CONFIGURED
    if enabled is None:
        enabled = os.getenv("WINDROSE_OTEL_ENABLED", "").lower() in ("1", "true", "yes")
    if not enabled:
        return False
    if _CONFIGURED:
        return True

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    endpoint = endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _CONFIGURED = True
    return True


def get_tracer(name: str):
    from opentelemetry import trace

    return trace.get_tracer(name)


@contextmanager
def span(name: str, tracer_name: str = "windrose"):
    """Start a span if a provider is configured; a cheap no-op otherwise."""
    from opentelemetry import trace

    tracer = trace.get_tracer(tracer_name)
    with tracer.start_as_current_span(name) as current:
        yield current
