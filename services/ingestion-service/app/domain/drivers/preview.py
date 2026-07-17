"""Per-connector-type preview dispatch (ING-FR-005).

Keeps the single ``SourcePreviewer`` shape the connection service expects while
routing each request to the real driver-backed previewer for local-protocol
types (postgres/mysql/sftp/http_api). Cloud/SaaS types fall back to the
deterministic ``FakeSourcePreviewer`` default until credentials are wired.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.domain.probers import PreviewResult, SourcePreviewer


class DispatchingSourcePreviewer:
    def __init__(self, default: SourcePreviewer) -> None:
        self._default = default
        self._by_type: dict[str, SourcePreviewer] = {}

    def set(self, connector_type: str, previewer: SourcePreviewer) -> None:
        self._by_type[connector_type] = previewer

    def get(self, connector_type: str) -> SourcePreviewer:
        return self._by_type.get(connector_type, self._default)

    async def preview(
        self,
        config: BaseModel,
        secrets: dict[str, str],
        request: dict[str, Any],
        limit: int,
    ) -> PreviewResult:
        connector_type = getattr(config, "connector_type", "")
        previewer = self.get(str(connector_type))
        return await previewer.preview(config, secrets, request, limit)
