"""Resource URNs (MASTER-FR-013): wr:<tenant>:<service>:<type>/<id>."""

from __future__ import annotations


def model_urn(tenant_id: str, model_id: str) -> str:
    return f"wr:{tenant_id}:semantic:model/{model_id}"


def version_urn(tenant_id: str, model_id: str, version_no: int) -> str:
    return f"wr:{tenant_id}:semantic:model/{model_id}/version/{version_no}"


def verified_query_urn(tenant_id: str, vq_id: str) -> str:
    return f"wr:{tenant_id}:semantic:verified_query/{vq_id}"


def tool_urn(tenant_id: str, tool: str) -> str:
    return f"wr:{tenant_id}:semantic:tool/{tool}"
