"""Snowflake dialect for the DB-API driver harness (snowflake-connector-python).

Credential-gated: the adapter is real (drives the vendor SDK) but a live pull
needs a real Snowflake account. Snowflake's paramstyle is ``pyformat`` so the
watermark binds as ``%(watermark)s`` with its typed value out-of-band.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.domain.drivers.dbapi import DbapiDialect
from app.domain.drivers.sql import to_pyformat


def _connect(config: BaseModel, secrets: dict[str, str], timeout: float) -> Any:
    import snowflake.connector  # lazy: only imported on the real runtime path

    kwargs: dict[str, Any] = {
        "account": config.account,
        "user": config.username,
        "warehouse": config.warehouse,
        "database": config.database,
        "schema": getattr(config, "schema", "PUBLIC"),
        "login_timeout": int(timeout),
        "network_timeout": int(timeout),
    }
    if getattr(config, "role", None):
        kwargs["role"] = config.role
    if secrets.get("private_key"):
        kwargs["private_key"] = secrets["private_key"]
    elif secrets.get("password"):
        kwargs["password"] = secrets["password"]
    return snowflake.connector.connect(**kwargs)


def snowflake_dialect() -> DbapiDialect:
    return DbapiDialect(name="snowflake", connect=_connect, translate=to_pyformat)
