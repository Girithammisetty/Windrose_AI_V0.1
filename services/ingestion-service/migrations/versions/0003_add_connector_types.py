"""Extend the connectors.connector_type CHECK with the wave-2 connector matrix.

Adds redshift, databricks, spanner and salesforce to the allowed set so the
new real (credential-gated) drivers can persist connections. Forward-only.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_ALL_TYPES = (
    "postgres",
    "mysql",
    "mariadb",
    "oracle",
    "sqlserver",
    "synapse",
    "presto",
    "bigquery",
    "snowflake",
    "redshift",
    "databricks",
    "spanner",
    "salesforce",
    "s3",
    "azure_blob",
    "gcs",
    "sftp",
    "ftp",
    "http_api",
)

_ORIGINAL_TYPES = (
    "postgres",
    "mysql",
    "mariadb",
    "oracle",
    "sqlserver",
    "synapse",
    "presto",
    "bigquery",
    "snowflake",
    "s3",
    "azure_blob",
    "gcs",
    "sftp",
    "ftp",
    "http_api",
)


def _values(types: tuple[str, ...]) -> str:
    return ", ".join(f"'{t}'" for t in types)


def upgrade() -> None:
    op.execute("ALTER TABLE connections DROP CONSTRAINT connections_connector_type_check")
    op.execute(
        "ALTER TABLE connections ADD CONSTRAINT connections_connector_type_check "
        f"CHECK (connector_type IN ({_values(_ALL_TYPES)}))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE connections DROP CONSTRAINT connections_connector_type_check")
    op.execute(
        "ALTER TABLE connections ADD CONSTRAINT connections_connector_type_check "
        f"CHECK (connector_type IN ({_values(_ORIGINAL_TYPES)}))"
    )
