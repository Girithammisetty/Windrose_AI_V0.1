"""Amazon Redshift dialect for the DB-API driver harness (redshift-connector).

Credential-gated: the adapter is real (drives the AWS redshift-connector SDK)
but a live pull needs a reachable Redshift endpoint + credentials. Redshift's
paramstyle is ``format`` so the watermark binds as positional ``%s`` with its
typed value carried out-of-band (never spliced).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.domain.drivers.dbapi import DbapiDialect
from app.domain.drivers.sql import to_format


def _connect(config: BaseModel, secrets: dict[str, str], timeout: float) -> Any:
    import redshift_connector  # lazy: only imported on the real runtime path

    return redshift_connector.connect(
        host=config.host,
        port=getattr(config, "port", 5439),
        database=config.database,
        user=config.username,
        password=secrets.get("password") or "",
        timeout=int(timeout),
    )


def redshift_dialect() -> DbapiDialect:
    return DbapiDialect(name="redshift", connect=_connect, translate=to_format)
