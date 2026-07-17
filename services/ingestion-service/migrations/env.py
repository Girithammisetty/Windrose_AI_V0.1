"""Alembic environment — sync engine (psycopg) driven by DATABASE_URL."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine

config = context.config


def _url() -> str:
    # Migrations create the app role + FORCE RLS, so they must run under a
    # privileged role. INGESTION_MIGRATE_URL (default = DATABASE_URL) lets the
    # runtime DSN point at the NON-superuser ingestion_app role while migrations
    # still connect as windrose.
    url = (
        config.get_main_option("sqlalchemy.url")
        or os.environ.get("INGESTION_MIGRATE_URL", "")
        or os.environ.get("DATABASE_URL", "")
    )
    if not url:
        raise RuntimeError("INGESTION_MIGRATE_URL or DATABASE_URL is required for migrations")
    # normalize async URLs to the sync psycopg driver
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg://").replace(
        "postgresql://", "postgresql+psycopg://"
    )


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
