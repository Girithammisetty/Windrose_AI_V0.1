"""MCP tool facade (ING-FR-002/005, ART-FR-012).

Agents access ingestion-service through these governed tools. Two READ-tier
tools ground the data-onboarding agent:

* ``ingestion.connector_types.list`` — the connector-type catalog (display name,
  category, dynamic-form field schema per type).
* ``ingestion.connection.preview`` — a saved connection's previewed source schema
  (<=100 rows, never persisted).

and one WRITE-tier tool executes an approved onboarding proposal:

* ``ingestion.create`` — create an ingestion job (a NEW dataset is registered
  from the completed run; reversible side effect).

This module is the callable surface tool-plane's mcp-gateway federates to (the
MCP transport / gateway registration is owned by tool-plane). Every method
delegates to the same domain services the REST API uses, so the governed path and
the human path share one implementation — no shadow write path.
"""

from __future__ import annotations

from typing import Any

from app.api.auth import Principal
from app.api.schemas import IngestionCreate, NewDataset, PreviewRequest
from app.domain.connectors import CONNECTOR_TYPES, connector_catalog
from app.domain.services.connections import ConnectionService
from app.domain.services.ingestions import IngestionService


class McpFacade:
    def __init__(self, container) -> None:
        self.c = container

    # ---- READ tools --------------------------------------------------------
    async def connector_types_list(self, principal: Principal) -> dict[str, Any]:
        """ingestion.connector_types.list — the full connector-type catalog."""
        return {"connector_types": connector_catalog(),
                "count": len(CONNECTOR_TYPES)}

    async def connection_preview(
        self, principal: Principal, connection_id: str, *, table: str | None = None,
        path: str | None = None, query: str | None = None, limit: int = 50,
    ) -> dict[str, Any]:
        """ingestion.connection.preview — previewed source schema for a saved
        connection (columns + a bounded row sample; never persisted)."""
        body = PreviewRequest(table=table, path=path, query=query,
                              limit=max(1, min(limit, 100)))
        return await ConnectionService(self.c).preview(principal, connection_id, body)

    # ---- WRITE tool (proposal-gated) --------------------------------------
    async def create_ingestion(
        self, principal: Principal, *, ingestion_mode: str,
        connection_id: str | None = None, file_format: str | None = None,
        statement: str | None = None, dataset_urn: str | None = None,
        new_dataset: dict | None = None, skip_profiling: bool = False,
        allow_empty: bool = False, workspace_id: str | None = None,
        **_ignored: Any,
    ) -> dict[str, Any]:
        """ingestion.create — create an ingestion job. Onboarding-agent governance
        fields (``connector_type``, ``column_mapping``) travel on the PROPOSAL for
        the human record and are ignored here: the create contract is
        :class:`IngestionCreate`, and the dataset schema is derived from the real
        source at ingest time."""
        body = IngestionCreate(
            ingestion_mode=ingestion_mode,
            connection_id=connection_id,
            statement=statement,
            file_format=file_format,
            dataset_urn=dataset_urn,
            new_dataset=NewDataset(**new_dataset) if new_dataset else None,
            skip_profiling=skip_profiling,
            allow_empty=allow_empty,
            workspace_id=workspace_id or IngestionCreate.model_fields["workspace_id"].default,
        )
        status, data = await IngestionService(self.c).create(principal, body)
        return {"status": status, "ingestion": data}
