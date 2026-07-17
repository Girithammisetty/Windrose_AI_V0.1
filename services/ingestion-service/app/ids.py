"""UUIDv7 generation (MASTER-FR-021: time-ordered resource IDs, exposed as opaque strings)."""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> str:
    """Generate a UUIDv7 string (48-bit unix-ms timestamp + random)."""
    ts_ms = time.time_ns() // 1_000_000
    raw = bytearray(ts_ms.to_bytes(6, "big") + os.urandom(10))
    raw[6] = (raw[6] & 0x0F) | 0x70  # version 7
    raw[8] = (raw[8] & 0x3F) | 0x80  # RFC 4122 variant
    return str(uuid.UUID(bytes=bytes(raw)))
