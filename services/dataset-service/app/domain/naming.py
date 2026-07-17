"""Physical-relation naming shared by the API serializers (resolve_payload) and
the domain resolver / internal detail lookups.

Kept in the domain layer so services can normalize a dataset name to its
DuckDB-safe relation without importing the API layer.
"""

from __future__ import annotations

import re

# Physical-identifier namespace query-service materializes resolved datasets
# into (CREATE OR REPLACE TABLE main.<relation>). Decoupled from the real Iceberg
# identifier so it lines up with a semantic model's entity.table = main.<name>.
RESOLVE_NAMESPACE = "main"


def safe_relation(name: str) -> str:
    """DuckDB-safe relation name = dataset name lowercased with every run of
    non-alphanumeric characters collapsed to a single underscore (leading digit
    prefixed so the identifier is always a valid unquoted-safe token too).
    e.g. "auto-claims-1783755028" -> "auto_claims_1783755028"."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not slug:
        slug = "dataset"
    if slug[0].isdigit():
        slug = f"t_{slug}"
    return slug
