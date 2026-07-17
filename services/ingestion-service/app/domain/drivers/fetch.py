"""Remote-file source fetcher port (ING-FR-041).

An SFTP/HTTP source is not a SQL query source — it produces a byte stream that
is copied, memory-bounded, into the object store for the normal decode/append
pipeline. The fetcher streams chunk-by-chunk; it never buffers a whole file.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from app.domain.objectstore import ObjectStore, PutResult


class SourceFetcher(Protocol):
    async def fetch(
        self,
        config: BaseModel,
        secrets: dict[str, str],
        request: dict[str, Any],
        object_store: ObjectStore,
        dest_key: str,
    ) -> PutResult: ...


class FetcherRegistry:
    def __init__(self, default: SourceFetcher | None = None) -> None:
        self._default = default
        self._by_type: dict[str, SourceFetcher] = {}

    def set(self, connector_type: str, fetcher: SourceFetcher) -> None:
        self._by_type[connector_type] = fetcher

    def get(self, connector_type: str) -> SourceFetcher:
        fetcher = self._by_type.get(connector_type, self._default)
        if fetcher is None:
            raise NotImplementedError(f"no source fetcher registered for {connector_type}")
        return fetcher
