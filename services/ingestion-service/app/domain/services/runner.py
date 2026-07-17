"""Ingestion execution engine (ING-FR-023/041/043/080/081/082, BR-3/7/9).

In production the finalize pipeline is a Temporal workflow with each step a
retryable activity (stub: TemporalScheduler / worker TODO). This in-process
runner implements the same step sequence deterministically for dev/tests:

    slot acquire -> [attempt: decode/stream -> stage] -> committing ->
    single atomic commit -> completed
    with transient-error retries (5 attempts, exponential backoff).

Memory bound (ING-FR-041): all data flows through async byte/row-batch
iterators; nothing ever holds a whole file.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from app.container import Container
from app.domain import connectors
from app.domain.decode import DecodeOptions, DecodeStats, decode_stream
from app.domain.errors import (
    ErrorCategory,
    PermanentJobError,
    TransientSourceError,
)
from app.domain.services.ingestions import (
    bronze_table_ident,
    find_upload_parts,
    parse_dataset_urn,
)
from app.domain.services.transitions import record_transition
from app.domain.state_machine import TransitionContext
from app.domain.tablewriter import RowBatch
from app.domain.watermark import (
    WatermarkSpec,
    build_incremental_query,
    coerce_watermark,
    serialize_watermark,
)
from app.events.outbox import emit_event
from app.store.models import Connection, Ingestion


class _ProgressReporter:
    """Throttled ingestion.progress emission (ING-FR-026, AC-6)."""

    def __init__(
        self, runner: IngestionRunner, tenant_id: str, ingestion_id: str, phase: str
    ) -> None:
        self.runner = runner
        self.tenant_id = tenant_id
        self.ingestion_id = ingestion_id
        self.phase = phase
        self.rows = 0
        self.chunk_count = 0
        self._last_emit = 0.0

    async def bump(self, rows: int) -> None:
        self.rows += rows
        self.chunk_count += 1
        now = time.monotonic()
        if now - self._last_emit < self.runner.c.settings.progress_min_interval_s:
            return
        self._last_emit = now
        await self.flush()

    async def flush(self) -> None:
        c = self.runner.c
        async with c.db.tenant_session(self.tenant_id) as session:
            ing = (
                await session.execute(sa.select(Ingestion).where(Ingestion.id == self.ingestion_id))
            ).scalar_one()
            ing.rows_appended = max(ing.rows_appended, self.rows)
            emit_event(
                session,
                tenant_id=self.tenant_id,
                event_type="ingestion.progress",
                resource_urn=f"wr:{self.tenant_id}:ingestion:ingestion/{self.ingestion_id}",
                payload={
                    "ingestion_id": self.ingestion_id,
                    "phase": self.phase,
                    "bytes_received": ing.bytes_received,
                    "bytes_total": ing.bytes_total,
                    "rows_appended": self.rows,
                    "chunk_count": self.chunk_count,
                },
            )
            await session.commit()


class IngestionRunner:
    def __init__(self, container: Container) -> None:
        self.c = container

    # ------------------------------------------------------------------ slot
    async def _acquire_slot(self, tenant_id: str, ingestion_id: str) -> bool:
        async with self.c.db.tenant_session(tenant_id) as session:
            ing = (
                await session.execute(sa.select(Ingestion).where(Ingestion.id == ingestion_id))
            ).scalar_one_or_none()
            if ing is None or ing.status != "queued":
                return False
            running = (
                await session.execute(
                    sa.select(sa.func.count())
                    .select_from(Ingestion)
                    .where(Ingestion.tenant_id == tenant_id, Ingestion.status == "running")
                )
            ).scalar_one()
            same_dataset_running = (
                await session.execute(
                    sa.select(sa.func.count())
                    .select_from(Ingestion)
                    .where(
                        Ingestion.tenant_id == tenant_id,
                        Ingestion.dataset_urn == ing.dataset_urn,
                        Ingestion.status.in_(("running", "committing")),
                    )
                )
            ).scalar_one()
            # ING-FR-082 tenant cap + BR-7 one running job per dataset_urn
            if running >= self.c.settings.max_running_per_tenant or same_dataset_running > 0:
                return False  # stays queued FIFO
            record_transition(session, ing, "running", TransitionContext(slot_available=True))
            ing.started_at = datetime.now(UTC)
            await session.commit()
            return True

    # --------------------------------------------------------------- execute
    async def execute(
        self, tenant_id: str, ingestion_id: str, watermark: WatermarkSpec | None = None
    ) -> dict[str, Any]:
        """Run a queued job to a terminal state. Returns {status, observed_watermark?}."""
        if not await self._acquire_slot(tenant_id, ingestion_id):
            async with self.c.db.tenant_session(tenant_id) as session:
                status = (
                    await session.execute(
                        sa.select(Ingestion.status).where(Ingestion.id == ingestion_id)
                    )
                ).scalar_one_or_none()
            return {"status": status or "unknown"}

        max_attempts = self.c.settings.retry_max_attempts
        attempt = 1
        while True:
            try:
                observed = await self._attempt(tenant_id, ingestion_id, watermark)
                return {"status": "completed", "observed_watermark": observed}
            except TransientSourceError as exc:
                if attempt >= max_attempts:
                    await self._fail(tenant_id, ingestion_id, exc.category, exc.message, attempt)
                    return {"status": "failed"}
                await self._transition_retry(tenant_id, ingestion_id, attempt)
                await asyncio.sleep(self._backoff(attempt))
                await self._transition_resume(tenant_id, ingestion_id)
                attempt += 1
            except PermanentJobError as exc:
                await self._fail(
                    tenant_id,
                    ingestion_id,
                    exc.category,
                    exc.message,
                    attempt,
                    samples=exc.samples,
                    hint=exc.hint,
                )
                return {"status": "failed"}

    def _backoff(self, attempt: int) -> float:
        base = self.c.settings.retry_backoff_base_s
        if base <= 0:
            return 0.0
        delay = min(base * (2 ** (attempt - 1)), self.c.settings.retry_backoff_cap_s)
        return delay * (1 + random.random() * 0.1)  # jitter (ING-FR-081)

    async def _transition_retry(self, tenant_id: str, ingestion_id: str, attempt: int) -> None:
        async with self.c.db.tenant_session(tenant_id) as session:
            ing = (
                await session.execute(sa.select(Ingestion).where(Ingestion.id == ingestion_id))
            ).scalar_one()
            record_transition(
                session,
                ing,
                "retrying",
                TransitionContext(
                    attempts=attempt, max_attempts=self.c.settings.retry_max_attempts
                ),
                detail={"attempt": attempt},
            )
            ing.attempts = attempt
            await session.commit()

    async def _transition_resume(self, tenant_id: str, ingestion_id: str) -> None:
        async with self.c.db.tenant_session(tenant_id) as session:
            ing = (
                await session.execute(sa.select(Ingestion).where(Ingestion.id == ingestion_id))
            ).scalar_one()
            record_transition(session, ing, "running", TransitionContext())
            await session.commit()

    async def _fail(
        self,
        tenant_id: str,
        ingestion_id: str,
        category: ErrorCategory | str,
        message: str,
        attempts: int,
        *,
        samples: list[dict[str, Any]] | None = None,
        hint: str | None = None,
    ) -> None:
        """ING-FR-080: categorized error_log (<=20 samples, values truncated)."""
        async with self.c.db.tenant_session(tenant_id) as session:
            ing = (
                await session.execute(sa.select(Ingestion).where(Ingestion.id == ingestion_id))
            ).scalar_one()
            ing.error_log = {
                "category": str(category),
                "message": message[:2000],
                "samples": (samples or [])[:20],
                "hint": hint,
            }
            ing.attempts = attempts
            ing.finished_at = datetime.now(UTC)
            record_transition(
                session,
                ing,
                "failed",
                TransitionContext(error_log_present=True),
                event_payload={
                    "ingestion_id": ing.id,
                    "error_category": str(category),
                    "error_digest": hashlib.sha256(message.encode()).hexdigest()[:16],
                    "attempts": attempts,
                },
            )
            await session.commit()

    # -------------------------------------------------------------- attempts
    async def _attempt(
        self, tenant_id: str, ingestion_id: str, watermark: WatermarkSpec | None
    ) -> str | None:
        async with self.c.db.tenant_session(tenant_id) as session:
            ing = (
                await session.execute(sa.select(Ingestion).where(Ingestion.id == ingestion_id))
            ).scalar_one()
            mode = ing.ingestion_mode
        if mode in ("file_upload",):
            await self._attempt_file(tenant_id, ingestion_id)
            return None
        if mode in ("query", "scheduled_run"):
            return await self._attempt_query(tenant_id, ingestion_id, watermark)
        raise PermanentJobError(ErrorCategory.INTERNAL, f"mode {mode} is not runnable")

    async def _concat_parts(self, part_keys: list[str]) -> AsyncIterator[bytes]:
        for key in part_keys:
            async for chunk in self.c.object_store.open_stream(key):
                yield chunk

    async def _attempt_file(self, tenant_id: str, ingestion_id: str) -> None:
        async with self.c.db.tenant_session(tenant_id) as session:
            ing = (
                await session.execute(sa.select(Ingestion).where(Ingestion.id == ingestion_id))
            ).scalar_one()
            parts = await find_upload_parts(session, ing)
            file_format = ing.file_format or "csv"
            error_row_limit = ing.error_row_limit
            allow_empty = ing.allow_empty
            table = bronze_table_ident(tenant_id, ing.dataset_urn or "")
        if not parts:
            raise PermanentJobError(
                ErrorCategory.INTERNAL, "no completed upload parts found for file job"
            )

        # BR-9: never double-append (e.g. crash-after-commit then retry)
        if await self.c.table_writer.has_snapshot(table, ingestion_id):
            raise PermanentJobError(
                ErrorCategory.INTERNAL, "snapshot already committed for this ingestion (BR-9)"
            )

        stats = DecodeStats()
        opts = DecodeOptions(
            file_format=file_format,
            error_row_limit=error_row_limit,
            batch_size=self.c.settings.decode_batch_size,
        )
        progress = _ProgressReporter(self, tenant_id, ingestion_id, phase="decoding")

        async def batches() -> AsyncIterator[RowBatch]:
            byte_iter = self._concat_parts([p.storage_key for p in parts])
            async for batch in decode_stream(byte_iter, opts, stats):
                await progress.bump(len(batch.rows))
                yield batch

        staged = await self.c.table_writer.stage(
            table, batches(), {"ingestion_id": ingestion_id, "source": "upload"}
        )
        await self._commit_staged(tenant_id, ingestion_id, table, staged, stats, allow_empty)

    async def _attempt_query(
        self, tenant_id: str, ingestion_id: str, watermark: WatermarkSpec | None
    ) -> str | None:
        async with self.c.db.tenant_session(tenant_id) as session:
            ing = (
                await session.execute(sa.select(Ingestion).where(Ingestion.id == ingestion_id))
            ).scalar_one()
            conn = (
                await session.execute(
                    sa.select(Connection).where(
                        Connection.id == ing.connection_id, Connection.tenant_id == tenant_id
                    )
                )
            ).scalar_one_or_none()
            if conn is None:
                raise PermanentJobError(
                    ErrorCategory.INTERNAL, "connection not found for query job"
                )
            statement = ing.statement or ""
            allow_empty = ing.allow_empty
            table = bronze_table_ident(tenant_id, ing.dataset_urn or "")
            connector_type = conn.connector_type
            config_dict = conn.config
            vault_ref = conn.vault_ref

        if await self.c.table_writer.has_snapshot(table, ingestion_id):
            raise PermanentJobError(
                ErrorCategory.INTERNAL, "snapshot already committed for this ingestion (BR-9)"
            )

        config_model = connectors.validate_config(connector_type, config_dict)
        secrets = (await self.c.secrets.get(vault_ref) or {}) if vault_ref else {}
        source = self.c.query_sources.get(connector_type)

        if watermark is not None:
            columns = await source.columns(config_model, secrets, statement)
            if watermark.column not in columns:
                # BR-5: fail before executing
                raise PermanentJobError(
                    ErrorCategory.SCHEMA_MISMATCH,
                    f"watermark column {watermark.column!r} absent from source schema",
                    hint=f"source columns: {columns}",
                )
            sql, params = build_incremental_query(statement, watermark)
        else:
            sql, params = statement, {}

        stats = DecodeStats()
        progress = _ProgressReporter(self, tenant_id, ingestion_id, phase="querying")
        observed: list[Any] = []

        async def batches() -> AsyncIterator[RowBatch]:
            columns: list[str] | None = None
            async for rows in source.execute(
                config_model, secrets, sql, params, self.c.settings.query_batch_size
            ):
                if not rows:
                    continue
                if columns is None:
                    columns = list(rows[0].keys())
                if watermark is not None:
                    for row in rows:
                        if watermark.column in row and row[watermark.column] is not None:
                            value = coerce_watermark(
                                watermark.value_type, str(row[watermark.column])
                            )
                            if not observed or value > observed[0]:
                                observed[:] = [value]
                stats.rows_ok += len(rows)
                await progress.bump(len(rows))
                yield RowBatch(columns=columns, rows=[[r.get(c) for c in columns] for r in rows])

        try:  # ING-FR-023: per-job query timeout (default 1600s, max 3600s)
            staged = await asyncio.wait_for(
                self.c.table_writer.stage(
                    table,
                    batches(),
                    {"ingestion_id": ingestion_id, "source": f"query:{connector_type}"},
                ),
                timeout=self.c.settings.query_timeout_s,
            )
        except TimeoutError as exc:
            raise PermanentJobError(
                ErrorCategory.TIMEOUT,
                f"query exceeded the {self.c.settings.query_timeout_s}s job timeout",
                hint="raise the per-job timeout or narrow the statement",
            ) from exc
        await self._commit_staged(tenant_id, ingestion_id, table, staged, stats, allow_empty)
        return serialize_watermark(observed[0]) if observed else None

    # ----------------------------------------------------------- commit path
    async def _commit_staged(
        self,
        tenant_id: str,
        ingestion_id: str,
        table: str,
        staged: Any,
        stats: DecodeStats,
        allow_empty: bool,
    ) -> None:
        if stats.rows_ok == 0 and not allow_empty:
            await self.c.table_writer.discard(staged)
            # BR-3: empty source fails with DECODE_ERROR + hint
            raise PermanentJobError(
                ErrorCategory.DECODE_ERROR,
                "source produced 0 decodable rows",
                hint="set allow_empty=true to create an empty-schema dataset version",
            )
        async with self.c.db.tenant_session(tenant_id) as session:
            ing = (
                await session.execute(sa.select(Ingestion).where(Ingestion.id == ingestion_id))
            ).scalar_one()
            record_transition(
                session,
                ing,
                "committing",
                TransitionContext(rows_decoded=stats.rows_ok, allow_empty=allow_empty),
            )
            await session.commit()

        result = await self.c.table_writer.commit(staged)  # exactly one snapshot (BR-9)

        async with self.c.db.tenant_session(tenant_id) as session:
            ing = (
                await session.execute(sa.select(Ingestion).where(Ingestion.id == ingestion_id))
            ).scalar_one()
            ing.iceberg_snapshot_id = result.snapshot_id
            ing.rows_appended = result.rows_appended
            ing.finished_at = datetime.now(UTC)
            # The dataset id THIS service minted (or was given) lives inside
            # dataset_urn. Surface it explicitly so the dataset-service consumer
            # registers the dataset row under the SAME id — the URN in case rows,
            # lineage and this event must all resolve to one dataset (BR-13).
            dataset_id: str | None = None
            if ing.dataset_urn:
                _, dataset_id = parse_dataset_urn(ing.dataset_urn)
            # Bronze columns are always written string-typed (ParquetFileTableWriter /
            # IcebergTableWriter both coerce every value to a pyarrow string column), so
            # this is exact, not inferred - it's what dataset-service's own catalog
            # fallback (dataset_detail()) would derive anyway if this were left empty.
            schema = {
                col: {"type": "string", "nullable": True, "tags": []} for col in staged.columns
            }
            record_transition(
                session,
                ing,
                "completed",
                TransitionContext(commit_ok=True),
                event_payload={
                    "ingestion_id": ing.id,
                    "dataset_urn": ing.dataset_urn,
                    "dataset_id": dataset_id,
                    # Fields the dataset-service consumer needs to auto-register the
                    # dataset + version from this event (BRD §6): workspace, target
                    # dataset name (for new_dataset ingestions) and the bronze table.
                    "workspace_id": str(ing.workspace_id),
                    "dataset_name": (ing.new_dataset.get("name") if ing.new_dataset else None),
                    "iceberg_table": table,
                    "iceberg_snapshot_id": result.snapshot_id,
                    "rows_appended": result.rows_appended,
                    "row_count": result.rows_appended,
                    "bytes": result.bytes_written,
                    "file_format": ing.file_format,
                    "skip_profiling": ing.skip_profiling,
                    "schema": schema,
                    # HONEST LIMITATION (ING-FR-083, Should-priority): no PII scan
                    # is implemented yet, so this is ALWAYS [] — downstream
                    # consumers must not treat it as "scanned and clean".
                    # TODO wave-2: Presidio column scan over the staged batches.
                    "pii_tags": [],
                    "renamed_columns": stats.renamed_columns,  # BR-4 flag
                    "rows_bad": stats.rows_bad,
                    "source": {"table": table},
                },
            )
            await session.commit()
