"""Allow 'xml' as a file-upload format (ING-FR-021).

decode.FILE_FORMATS gained 'xml' (a real streaming XML decoder), but the
inline CHECK constraint from 0001 still only permitted csv/tsv/json/jsonl/
parquet/avro -> a file_upload ingestion with file_format='xml' passed the
Python validation and then failed at INSERT with
``ingestions_file_format_check`` (CheckViolationError). Widen the constraint on
the partitioned parent (partitions inherit it) to include 'xml'.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_OLD = "('csv','tsv','json','jsonl','parquet','avro')"
_NEW = "('csv','tsv','json','jsonl','parquet','avro','xml')"


def _set_check(values: str) -> None:
    op.execute("ALTER TABLE ingestions DROP CONSTRAINT IF EXISTS ingestions_file_format_check")
    op.execute(
        "ALTER TABLE ingestions ADD CONSTRAINT ingestions_file_format_check "
        f"CHECK (file_format IS NULL OR file_format IN {values})"
    )


def upgrade() -> None:
    _set_check(_NEW)


def downgrade() -> None:
    _set_check(_OLD)
