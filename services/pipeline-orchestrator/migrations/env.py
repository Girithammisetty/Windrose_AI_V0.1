import os
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

config = context.config


def _url() -> str:
    url = os.environ.get("PPL_MIGRATE_URL") or os.environ.get("PPL_DATABASE_URL") or (
        config.get_main_option("sqlalchemy.url")
    )
    return url.replace("+asyncpg", "+psycopg")


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
