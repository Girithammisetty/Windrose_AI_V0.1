"""Per-tenant schema naming (MEM §4: tenant data in ``mem_t_<tenant>``)."""

from __future__ import annotations


def tenant_schema(tenant_id: str) -> str:
    # UUIDs contain '-', invalid unquoted in identifiers; hex form is safe.
    return "mem_t_" + tenant_id.replace("-", "").lower()
