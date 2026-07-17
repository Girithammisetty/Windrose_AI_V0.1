"""Dependency-free JSON stdout logging for Python services (MASTER-FR-050).

Mirrors the Go convention already used in every Go service's ``main()``:

    slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil))) // MASTER-FR-050

Go's structured JSON logs are already forwarder-friendly (Fluent Bit, Vector,
the cloud-native log agents); Python services previously logged plain text via
the stdlib default handler. This gives Python the same shape with no new
dependency (no ``python-json-logger`` or similar) — a single ``logging.Formatter``
subclass plus one setup call.

Usage, first thing in ``app/main.py`` (before any other logger is configured):

    from windrose_common.logging import configure_json_logging
    configure_json_logging("eval-service")

Output is one JSON object per line on stdout, e.g.::

    {"time": "2026-07-16T18:04:22.148231+00:00", "level": "INFO",
     "logger": "eval-service", "message": "eval flywheel/SLO consumers started",
     "service": "eval-service"}

Exception info (``logger.exception(...)`` / ``exc_info=True``) is rendered as
an ``"exc_info"`` string field so the record stays valid single-line JSON.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

__all__ = ["JsonFormatter", "configure_json_logging"]

# Attributes every stdlib LogRecord carries that we don't want to re-emit
# verbatim (already surfaced under friendlier names, or internal bookkeeping).
_RESERVED = frozenset(logging.LogRecord(
    "", 0, "", 0, "", (), None
).__dict__.keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Renders each LogRecord as a single-line JSON object on stdout.

    Any ``extra={...}`` fields passed to the logging call are merged into the
    record verbatim (mirroring slog's key/value attrs), as long as they don't
    collide with the reserved field names above.
    """

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self._service_name,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        for key, value in record.__dict__.items():
            if key in _RESERVED or key in payload:
                continue
            try:
                json.dumps(value)  # only carry values that actually serialize
            except TypeError:
                value = repr(value)
            payload[key] = value
        return json.dumps(payload, default=str)


def configure_json_logging(service_name: str, *, level: int = logging.INFO) -> None:
    """Install the JSON formatter on the root logger's stdout handler.

    Idempotent: safe to call once at process start (e.g. module import time in
    ``app/main.py``) even under ``uvicorn --reload`` re-exec. Replaces any
    handlers the stdlib default logging config may have already attached, so
    every ``logging.getLogger(__name__)`` call site downstream gets JSON
    output for free with no per-module change.
    """
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter(service_name))
    root.handlers = [handler]
