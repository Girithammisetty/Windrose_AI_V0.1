"""Dataset row reader: at run time a ``read-from-warehouse`` node materializes an
uploaded dataset's rows by calling dataset-service's internal rows API.

The real adapter (:class:`HttpDatasetReader`) is a REAL dependency — on any failure
it raises, never fabricates rows (Windrose no-fakes rule). The in-memory adapter is
unit-tier only and never reachable from the shipped runtime wiring.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx

from app.domain.errors import DependencyUnavailable


@runtime_checkable
class DatasetReader(Protocol):
    async def read_rows(
        self, tenant_id: str, dataset_urn: str, limit: int = 10000
    ) -> list[dict]:
        """Return the dataset's rows as a list of column->value dicts."""
        ...


def dataset_id_from_urn(dataset_urn: str) -> str:
    """Extract the dataset id from a URN like
    ``wr:{tenant}:dataset:dataset/{dataset_id}`` — the last ``/``-delimited segment."""
    if not dataset_urn or "/" not in dataset_urn:
        raise ValueError(f"malformed dataset URN {dataset_urn!r}")
    dataset_id = dataset_urn.rsplit("/", 1)[-1]
    if not dataset_id:
        raise ValueError(f"malformed dataset URN {dataset_urn!r}")
    return dataset_id


class HttpDatasetReader:
    """Reads dataset rows from dataset-service's internal rows API. Real dependency:
    a non-200 response or a connection error raises :class:`DependencyUnavailable`."""

    def __init__(self, base_url: str, spiffe: str, *, timeout: float = 30):
        self._base_url = base_url.rstrip("/")
        self._spiffe = spiffe
        self._timeout = timeout
        # Reuse one client (and its TCP+TLS connection pool) across calls rather
        # than paying a fresh handshake per request.
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def read_rows(
        self, tenant_id: str, dataset_urn: str, limit: int = 10000
    ) -> list[dict]:
        dataset_id = dataset_id_from_urn(dataset_urn)
        url = f"{self._base_url}/internal/v1/datasets/{dataset_id}/rows"
        headers = {
            "x-client-spiffe-id": self._spiffe,
            "x-windrose-tenant-id": tenant_id,
        }
        try:
            client = self._http()
            resp = await client.get(url, params={"limit": limit}, headers=headers)
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPStatusError as exc:
            raise DependencyUnavailable(
                f"dataset-service returned {exc.response.status_code} for dataset "
                f"{dataset_id!r}: {exc.response.text[:200]}") from exc
        except httpx.HTTPError as exc:
            raise DependencyUnavailable(
                f"dataset-service unreachable at {self._base_url}: {exc}") from exc
        rows = ((body or {}).get("data") or {}).get("rows")
        if rows is None:
            raise DependencyUnavailable(
                f"dataset-service response for {dataset_id!r} missing data.rows")
        return list(rows)


class InMemoryDatasetReader:
    """Unit-tier double: seeded with ``{dataset_urn: [row, ...]}``; unknown URNs → []."""

    def __init__(self, by_urn: dict[str, list[dict]] | None = None):
        self._by_urn = by_urn or {}

    async def read_rows(
        self, tenant_id: str, dataset_urn: str, limit: int = 10000
    ) -> list[dict]:
        return list(self._by_urn.get(dataset_urn, []))[:limit]
