"""Idempotency-Key handling (MASTER-FR-025, ING AC-14).

Insert-first with a unique (tenant_id, key) constraint: exactly one concurrent
request wins and executes; the others poll for the stored response and replay
it with `Idempotency-Replayed: true`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.domain.errors import ConflictError
from app.ids import uuid7
from app.store.db import Database
from app.store.models import IdempotencyKey

REPLAY_POLL_INTERVAL_S = 0.05
REPLAY_POLL_TIMEOUT_S = 10.0

Handler = Callable[[], Awaitable[tuple[int, dict[str, Any]]]]


def request_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


async def run_idempotent(
    db: Database,
    tenant_id: str,
    idempotency_key: str | None,
    payload_hash: str,
    handler: Handler,
) -> tuple[int, dict[str, Any], bool]:
    """Returns (status_code, body, replayed)."""
    if not idempotency_key:
        status, body = await handler()
        return status, body, False

    async with db.tenant_session(tenant_id) as session:
        session.add(
            IdempotencyKey(
                id=uuid7(), tenant_id=tenant_id, key=idempotency_key, request_hash=payload_hash
            )
        )
        try:
            await session.commit()
            claimed = True
        except IntegrityError:
            await session.rollback()
            claimed = False

    if not claimed:
        deadline = asyncio.get_event_loop().time() + REPLAY_POLL_TIMEOUT_S
        while True:
            async with db.tenant_session(tenant_id) as session:
                row = (
                    await session.execute(
                        sa.select(IdempotencyKey).where(
                            IdempotencyKey.tenant_id == tenant_id,
                            IdempotencyKey.key == idempotency_key,
                        )
                    )
                ).scalar_one_or_none()
            if row is not None and row.status_code is not None:
                if row.request_hash != payload_hash:
                    raise ConflictError("Idempotency-Key reused with a different payload")
                return row.status_code, dict(row.response_body or {}), True
            if row is None or asyncio.get_event_loop().time() > deadline:
                raise ConflictError("idempotent request still in flight; retry later")
            await asyncio.sleep(REPLAY_POLL_INTERVAL_S)

    try:
        status, body = await handler()
    except BaseException:
        # release the claim so a client retry can re-execute
        async with db.tenant_session(tenant_id) as session:
            await session.execute(
                sa.delete(IdempotencyKey).where(
                    IdempotencyKey.tenant_id == tenant_id, IdempotencyKey.key == idempotency_key
                )
            )
            await session.commit()
        raise

    async with db.tenant_session(tenant_id) as session:
        await session.execute(
            sa.update(IdempotencyKey)
            .where(IdempotencyKey.tenant_id == tenant_id, IdempotencyKey.key == idempotency_key)
            .values(status_code=status, response_body=body)
        )
        await session.commit()
    return status, body, False
