"""Small shared helpers: uuid7, clock, cursors, canonical json, hashing, math."""

from __future__ import annotations

import base64
import hashlib
import json
import math
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


def new_id() -> str:
    return str(uuid7())


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


def clamp_limit_or_default(limit: int | None, default: int = 50, maximum: int = 200) -> int:
    if not limit or limit < 1:
        return default
    return min(limit, maximum)


def canonical_json(doc) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str)


def json_size_bytes(doc) -> int:
    return len(json.dumps(doc, default=str).encode())


def sha256_hex(value: str | bytes) -> str:
    data = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def recency_decay(age_seconds: float, half_life_seconds: float) -> float:
    """Exponential recency in [0,1]; 1.0 at age 0, 0.5 at one half-life."""
    if half_life_seconds <= 0:
        return 1.0
    return math.exp(-math.log(2) * max(age_seconds, 0.0) / half_life_seconds)
