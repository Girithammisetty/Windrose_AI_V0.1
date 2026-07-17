"""Engine/session management with per-request tenant context (MASTER-FR-001).

On Postgres every tenant session sets `app.tenant_id` (from the verified JWT,
never from request payloads) so RLS policies apply. On SQLite (unit tier) the
services' explicit tenant_id filters act as the in-memory policy fake required
by CONVENTIONS.md testing tier 3.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.store.models import Base


def build_engine(database_url: str) -> AsyncEngine:
    if database_url.startswith("sqlite"):
        engine = create_async_engine(database_url, poolclass=NullPool)

        @sa.event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        return engine
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
    )


class Database:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.is_postgres = engine.url.get_backend_name().startswith("postgres")
        self.session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def create_all(self) -> None:
        """Unit-tier schema creation. Postgres uses alembic migrations."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def tenant_session(self, tenant_id: str) -> AsyncIterator[AsyncSession]:
        uuid.UUID(tenant_id)  # defensive: tenant ids are UUIDs from verified JWTs
        async with self.session_factory() as session:
            if self.is_postgres:
                await session.execute(
                    sa.text("SELECT set_config('app.tenant_id', :tid, false)"), {"tid": tenant_id}
                )
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
