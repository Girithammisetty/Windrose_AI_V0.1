"""Alembic env: sync psycopg engine. URL from AR_MIGRATE_URL (tests) or
AR_ADMIN_DATABASE_URL, normalised to the psycopg driver."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool


def _url() -> str:
    url = os.environ.get("AR_MIGRATE_URL") or os.environ.get(
        "AR_ADMIN_DATABASE_URL",
        "postgresql+psycopg://agent_runtime:agent_runtime@localhost:5432/agent_runtime",
    )
    return (
        url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
        .replace("postgresql://", "postgresql+psycopg://")
    )


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
