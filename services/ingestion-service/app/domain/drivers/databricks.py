"""Databricks SQL dialect for the DB-API harness (databricks-sql-connector).

Credential-gated: the adapter is real (drives the databricks-sql-connector SDK)
but a live pull needs a Databricks workspace + SQL warehouse token. The
connector's paramstyle is ``pyformat`` so the watermark binds as
``%(watermark)s`` with its typed value out-of-band.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.domain.drivers.dbapi import DbapiDialect
from app.domain.drivers.sql import to_pyformat


def _connect(config: BaseModel, secrets: dict[str, str], timeout: float) -> Any:
    from databricks import sql as dbsql  # lazy: only imported on the real runtime path

    kwargs: dict[str, Any] = {
        "server_hostname": config.server_hostname,
        "http_path": config.http_path,
        "access_token": secrets.get("access_token") or "",
        "_socket_timeout": int(timeout),
    }
    if getattr(config, "catalog", None):
        kwargs["catalog"] = config.catalog
    if getattr(config, "schema", None):
        kwargs["schema"] = config.schema
    return dbsql.connect(**kwargs)


def databricks_dialect() -> DbapiDialect:
    return DbapiDialect(name="databricks", connect=_connect, translate=to_pyformat)
