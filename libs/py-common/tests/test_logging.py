"""JSON logging + trace-id/span-id correlation (BRD 58 WS2). No live collector
needed -- these exercise the formatter/filter directly against real
logging.LogRecord objects and a real (SDK, unexported) OTel span context."""

from __future__ import annotations

import json
import logging

from opentelemetry import trace

from datacern_common.logging import JsonFormatter, TraceContextFilter


def _record(msg: str = "hello") -> logging.LogRecord:
    return logging.LogRecord("svc", logging.INFO, __file__, 1, msg, (), None)


def test_json_formatter_basic_shape():
    payload = json.loads(JsonFormatter("eval-service").format(_record("hi")))
    assert payload["message"] == "hi"
    assert payload["service"] == "eval-service"
    assert payload["level"] == "INFO"


def test_trace_context_filter_adds_ids_when_span_active():
    span_context = trace.SpanContext(
        trace_id=0x0102030405060708090A0B0C0D0E0F10,
        span_id=0x1112131415161718,
        is_remote=False,
        trace_flags=trace.TraceFlags(trace.TraceFlags.SAMPLED),
    )
    span = trace.NonRecordingSpan(span_context)
    ctx = trace.set_span_in_context(span)

    from opentelemetry import context as otel_context

    token = otel_context.attach(ctx)
    try:
        record = _record()
        assert TraceContextFilter().filter(record) is True
        assert record.trace_id == format(span_context.trace_id, "032x")
        assert record.span_id == format(span_context.span_id, "016x")

        payload = json.loads(JsonFormatter("svc").format(record))
        assert payload["trace_id"] == record.trace_id
        assert payload["span_id"] == record.span_id
    finally:
        otel_context.detach(token)


def test_trace_context_filter_noop_without_active_span():
    record = _record()
    assert TraceContextFilter().filter(record) is True
    assert not hasattr(record, "trace_id")
    assert not hasattr(record, "span_id")

    payload = json.loads(JsonFormatter("svc").format(record))
    assert "trace_id" not in payload
    assert "span_id" not in payload
