"""Cursor pagination (MASTER-FR-022): UUIDv7 ids are time-ordered, so the
cursor is simply the last id of the previous page (base64url-encoded)."""

from __future__ import annotations

import base64
import binascii
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import ValidationFailedError

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if limit < 1 or limit > MAX_LIMIT:
        raise ValidationFailedError(
            "invalid limit",
            details=[{"field": "limit", "message": f"must be between 1 and {MAX_LIMIT}"}],
        )
    return limit


def encode_cursor(last_id: str) -> str:
    return base64.urlsafe_b64encode(last_id.encode()).decode()


def decode_cursor(cursor: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        uuid.UUID(raw)
        return raw
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ValidationFailedError(
            "invalid cursor", details=[{"field": "cursor", "message": "malformed cursor"}]
        ) from exc


async def paginate(
    session: AsyncSession,
    stmt: sa.Select,
    id_column: Any,
    *,
    limit: int | None,
    cursor: str | None,
) -> tuple[list[Any], dict[str, Any]]:
    """Returns (items, page) with the standard {next_cursor, has_more} envelope."""
    size = clamp_limit(limit)
    if cursor:
        stmt = stmt.where(id_column < decode_cursor(cursor))
    stmt = stmt.order_by(id_column.desc()).limit(size + 1)
    rows = (await session.execute(stmt)).scalars().all()
    has_more = len(rows) > size
    items = list(rows[:size])
    next_cursor = encode_cursor(items[-1].id) if has_more and items else None
    return items, {"next_cursor": next_cursor, "has_more": has_more}
