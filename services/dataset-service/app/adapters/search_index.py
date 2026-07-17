"""SearchIndex implementations (DST-FR-060/062).

Postgres full-text search now; OpenSearch projection later (stub). The
in-memory index backs the unit tier.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import Dataset


class InMemorySearchIndex:
    def __init__(self):
        self.docs: dict[tuple[str, str], str] = {}

    async def index_dataset(self, dataset: Dataset) -> None:
        blob = " ".join(
            [dataset.name, dataset.description or "", " ".join(dataset.tags)]
        ).lower()
        self.docs[(dataset.tenant_id, dataset.id)] = blob

    async def remove_dataset(self, tenant_id: str, dataset_id: str) -> None:
        self.docs.pop((tenant_id, dataset_id), None)

    async def search(self, tenant_id: str, q: str, limit: int = 500) -> list[str]:
        terms = q.lower().split()
        hits = []
        for (tid, did), blob in self.docs.items():
            if tid != tenant_id:
                continue
            score = sum(1 for t in terms if t in blob)
            if score:
                hits.append((score, did))
        hits.sort(key=lambda h: -h[0])
        return [did for _, did in hits[:limit]]


class PostgresFTSSearchIndex:
    """Full-text search over datasets in the service's own Postgres.

    Source of truth stays in the datasets table; the tsvector is computed from
    name/description/tags (expression GIN index created in the migration), so
    no separate write path is needed — index_dataset/remove_dataset are no-ops.
    """

    def __init__(self, session_factory: Callable[[str], AsyncSession]):
        self._session_for_tenant = session_factory

    async def index_dataset(self, dataset: Dataset) -> None:  # CDC-equivalent: same table
        return None

    async def remove_dataset(self, tenant_id: str, dataset_id: str) -> None:
        return None

    async def search(self, tenant_id: str, q: str, limit: int = 500) -> list[str]:
        async with self._session_for_tenant(tenant_id) as session:
            rows = await session.execute(
                text(
                    """
                    SELECT id::text,
                           ts_rank(
                               to_tsvector('english',
                                   dataset_search_text(name, description, tags)),
                               plainto_tsquery('english', :q)) AS rank
                    FROM datasets
                    WHERE deleted_at IS NULL
                      AND (to_tsvector('english',
                               dataset_search_text(name, description, tags))
                               @@ plainto_tsquery('english', :q)
                           OR name ILIKE '%' || :q || '%')
                    ORDER BY rank DESC, created_at DESC
                    LIMIT :limit
                    """
                ),
                {"q": q, "limit": limit},
            )
            return [r[0] for r in rows]


class OpenSearchIndex:
    """TODO(prod): OpenSearch projection fed by CDC on dataset.events.v1
    (DST-FR-060). Read path replaces PostgresFTSSearchIndex behind the same port."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("TODO: OpenSearch adapter — use PostgresFTSSearchIndex in dev")
