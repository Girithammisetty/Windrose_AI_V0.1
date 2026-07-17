"""Webhook push ingestion (ING-FR-024, BR-11, AC-11).

`POST /hooks/{path_token}/events` is authenticated by per-ingestion HMAC, not
JWT. The path token is prefixed with the tenant id so the tenant DB context
(RLS) is established before lookup. Events buffer to the object store as
JSONL; the periodic flush to Iceberg is a stub (TODO wave-2 Temporal timer),
but accepted-event accounting is exact-once via event_id dedup.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.container import Container
from app.domain.errors import (
    NotFoundError,
    PayloadTooLargeError,
    SignatureInvalidError,
    ValidationFailedError,
)
from app.ids import uuid7
from app.store.models import Ingestion, WebhookEndpoint, WebhookEventDedup


class WebhookService:
    def __init__(self, container: Container) -> None:
        self.c = container

    async def receive(self, path_token: str, body: bytes, signature: str | None) -> dict[str, Any]:
        if len(body) > self.c.settings.webhook_max_payload_bytes:
            raise PayloadTooLargeError(
                "payload exceeds 1MB",
                details={"max_bytes": self.c.settings.webhook_max_payload_bytes},
            )
        tenant_id = path_token.split(".", 1)[0]
        try:
            uuid.UUID(tenant_id)
        except ValueError as exc:
            raise NotFoundError() from exc

        async with self.c.db.tenant_session(tenant_id) as session:
            endpoint = (
                await session.execute(
                    sa.select(WebhookEndpoint).where(
                        WebhookEndpoint.path_token == path_token,
                        WebhookEndpoint.tenant_id == tenant_id,
                        WebhookEndpoint.enabled.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if endpoint is None:
                raise NotFoundError()

            secret_data = await self.c.secrets.get(endpoint.hmac_vault_ref) or {}
            signing_secret = secret_data.get("signing_secret", "")
            expected = hmac_mod.new(signing_secret.encode(), body, hashlib.sha256).hexdigest()
            provided = (signature or "").removeprefix("sha256=").strip()
            if not provided or not hmac_mod.compare_digest(expected, provided):
                raise SignatureInvalidError("invalid webhook signature")

            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                raise ValidationFailedError(
                    "body must be JSON", details=[{"field": "body", "message": str(exc)}]
                ) from exc
            events = payload if isinstance(payload, list) else [payload]
            if not all(isinstance(e, dict) for e in events):
                raise ValidationFailedError(
                    "events must be JSON objects",
                    details=[{"field": "body", "message": "each event must be an object"}],
                )

            accepted: list[dict[str, Any]] = []
            duplicates = 0
            for event in events:
                event_id = event.get("event_id")
                if event_id is not None:
                    try:  # BR-11: dedup by client event_id (24h window in prod Redis)
                        async with session.begin_nested():
                            session.add(
                                WebhookEventDedup(
                                    ingestion_id=endpoint.ingestion_id,
                                    event_id=str(event_id),
                                    tenant_id=tenant_id,
                                )
                            )
                            await session.flush()
                    except IntegrityError:
                        duplicates += 1
                        continue
                accepted.append(event)

            if accepted:
                buffer_key = (
                    f"tenants/{tenant_id}/webhook-buffer/{endpoint.ingestion_id}/{uuid7()}.jsonl"
                )
                data = ("\n".join(json.dumps(e) for e in accepted) + "\n").encode()

                async def _chunks(payload_bytes: bytes = data):
                    yield payload_bytes

                await self.c.object_store.put(buffer_key, _chunks())

            ing = (
                await session.execute(
                    sa.select(Ingestion).where(
                        Ingestion.id == endpoint.ingestion_id, Ingestion.tenant_id == tenant_id
                    )
                )
            ).scalar_one_or_none()
            if ing is not None:
                # AC-11: duplicates acknowledged but never double-counted
                ing.rows_appended += len(accepted)
                ing.bytes_received += len(body)
            await session.commit()
            return {"accepted": len(accepted), "duplicates": duplicates}

    async def flush_to_iceberg(self, tenant_id: str, ingestion_id: str) -> None:
        """ING-FR-024 flush (every flush_interval or 100MB).

        NOT IMPLEMENTED — and gated honestly upstream: because this flush does
        not exist, `POST /ingestions` REJECTS ingestion_mode=webhook_batch with
        a 501 (see IngestionService._validate_mode), so no caller can enqueue
        webhook events that would silently never reach Iceberg.

        TODO(wave-2): Temporal timer workflow — read buffered JSONL objects,
        decode via app.domain.decode, single Iceberg append per flush (BR-9/11),
        then delete the buffer objects.
        """
        from app.domain.errors import NotImplementedFeatureError

        raise NotImplementedFeatureError(
            "webhook buffer flush to Iceberg is not implemented "
            "(TODO wave-2 Temporal timer workflow)"
        )
