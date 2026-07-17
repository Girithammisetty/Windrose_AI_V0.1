"""Per-request context (trace id propagation, MASTER-FR-028/050)."""

from __future__ import annotations

from contextvars import ContextVar

from app.ids import uuid7

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_trace_id() -> str:
    trace_id = uuid7()
    _trace_id.set(trace_id)
    return trace_id


def set_trace_id(trace_id: str) -> None:
    _trace_id.set(trace_id)


def current_trace_id() -> str:
    trace_id = _trace_id.get()
    if trace_id is None:
        trace_id = new_trace_id()
    return trace_id
