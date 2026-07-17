"""Small shared helpers: uuid7, clock, cursors, hashing (vendored, wave-1)."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from datetime import UTC, datetime


def uuid7() -> uuid.UUID:
    """RFC 9562 UUIDv7 (time-ordered)."""
    ts_ms = int(time.time() * 1000)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (ts_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Clock:
    """Injectable clock; tests replace with FakeClock."""

    def now(self) -> datetime:
        return utcnow()


def encode_cursor(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, default=str).encode()).decode()


def decode_cursor(cursor: str) -> dict:
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception as exc:  # noqa: BLE001 - normalize any decode failure
        raise ValueError("invalid cursor") from exc


def sha256_hex(value: str | bytes) -> str:
    data = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def estimate_tokens(text: str) -> int:
    """Deterministic prompt-size heuristic (~4 chars/token) used for budget
    reservations and the semantic-cache minimum-length gate. Provider-reported
    usage is authoritative at settlement."""
    return max(1, len(text) // 4)
