"""Alembic environment (sync psycopg for migrations)."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine

from app.config import Settings


def _url() -> str:
    override = os.environ.get("MEM_MIGRATE_URL")
    if override:
        return override
    url = Settings().admin_database_url
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg://")


def run_migrations_online() -> None:
    engine = create_engine(_url(), future=True)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_online()
