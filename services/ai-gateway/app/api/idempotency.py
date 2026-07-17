"""Idempotency-Key support for side-effecting admin POSTs (MASTER-FR-025,
BR-11: data-plane calls are deliberately NOT idempotent)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from app.domain.ports import UowFactory
from app.utils import sha256_hex


async def idempotent(
    request: Request,
    response: Response,
    uow_factory: UowFactory,
    tenant_id: str,
    work: Callable[[], Awaitable[tuple[int, dict]]],
) -> dict:
    """Run `work` (returning (status_code, body)) once per Idempotency-Key."""
    key = request.headers.get("idempotency-key")
    if not key:
        status, body = await work()
        response.status_code = status
        return body

    async with uow_factory(tenant_id) as uow:
        record = await uow.idempotency.get(key)
    if record is not None:
        response.status_code = record["status_code"]
        response.headers["Idempotency-Replayed"] = "true"
        return record["body"]

    raw = await request.body()
    status, body = await work()
    try:
        async with uow_factory(tenant_id) as uow:
            await uow.idempotency.put(key, sha256_hex(raw), status, body)
            await uow.commit()
    except Exception:  # noqa: BLE001 - concurrent same-key insert; original response stands
        pass
    response.status_code = status
    return body
