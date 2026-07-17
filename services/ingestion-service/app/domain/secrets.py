"""SecretsStore port (ING-FR-003/006, BR-1).

Credentials live only behind this interface: Postgres stores `vault_ref` only.
InMemorySecretsStore backs the unit tier. Four real runtime stores wrap the
shared ``windrose_common`` adapters, selected via ``SECRETS_BACKEND`` (BYO
Infra Hardening Phase 2, ``docs/design/byo-infra-hardening.md``):
VaultSecretsStore (default), AWSSecretsManagerStore, AzureKeyVaultStore,
GCPSecretManagerStore. All four implement the identical SecretsStore Protocol
below and are asserted behaviorally identical by
``tests/integration/test_secrets_store_contract.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


def connection_secret_path(tenant_id: str, connection_id: str) -> str:
    return f"secret/data/tenants/{tenant_id}/connections/{connection_id}"


def webhook_secret_path(tenant_id: str, ingestion_id: str) -> str:
    return f"secret/data/tenants/{tenant_id}/webhooks/{ingestion_id}"


class SecretsStore(Protocol):
    async def put(self, path: str, data: dict[str, str]) -> None: ...

    async def get(self, path: str) -> dict[str, str] | None: ...

    async def delete(self, path: str) -> None: ...

    async def schedule_destroy(self, path: str, destroy_at: datetime) -> None:
        """ING-FR-006: destroy after the 7-day grace period."""
        ...


class InMemorySecretsStore:
    """Dev/test fake. Also records scheduled destroys so tests can assert AC-10."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, str]] = {}
        self.scheduled_destroys: dict[str, datetime] = {}

    async def put(self, path: str, data: dict[str, str]) -> None:
        existing = self._data.get(path, {})
        self._data[path] = {**existing, **data}

    async def get(self, path: str) -> dict[str, str] | None:
        value = self._data.get(path)
        return dict(value) if value is not None else None

    async def delete(self, path: str) -> None:
        self._data.pop(path, None)

    async def schedule_destroy(self, path: str, destroy_at: datetime) -> None:
        self.scheduled_destroys[path] = destroy_at

    async def run_due_destroys(self, now: datetime | None = None) -> int:
        """Mirrors VaultSecretsStore's sweeper so InMemorySecretsStore is a
        fully SecretsStore-contract-conformant double, not just an AC-10
        introspection fixture (BYO Infra Hardening Phase 2 contract tests)."""
        from datetime import UTC

        now = now or datetime.now(UTC)
        due = [p for p, at in self.scheduled_destroys.items() if at <= now]
        for p in due:
            self._data.pop(p, None)
            del self.scheduled_destroys[p]
        return len(due)

    # test helper: assert a raw secret value never appears outside the store
    def dump_all_values(self) -> list[str]:
        return [v for d in self._data.values() for v in d.values()]


class VaultSecretsStore:
    """Real Vault KV v2 secrets store via the shared ``windrose_common`` hvac
    adapter. Credentials live at
    ``secret/data/tenants/<tenant_id>/connections/<connection_id>``;
    ``schedule_destroy`` enqueues a 7-day-grace destroy swept by
    ``run_due_destroys`` (ING-FR-006). Runtime secrets store."""

    def __init__(
        self, addr: str = "http://localhost:8200", token: str = "windrose_dev_root"
    ) -> None:
        from windrose_common.secrets import VaultSecretsStore as _Vault

        self.addr = addr
        self.token = token
        self._store = _Vault(addr, token)

    async def put(self, path: str, data: dict[str, str]) -> None:
        await self._store.put(path, data)

    async def get(self, path: str) -> dict[str, str] | None:
        return await self._store.get(path)

    async def delete(self, path: str) -> None:
        await self._store.delete(path)

    async def schedule_destroy(self, path: str, destroy_at: datetime) -> None:
        await self._store.schedule_destroy(path, destroy_at)

    async def run_due_destroys(self, now: datetime | None = None) -> int:
        return await self._store.run_due_destroys(now)


class AWSSecretsManagerStore:
    """Real AWS Secrets Manager secrets store via the shared ``windrose_common``
    boto3 adapter (BYO Infra Hardening Phase 2). Selected by
    ``SECRETS_BACKEND=aws``. Live-verified against a real local LocalStack
    container (see the contract test suite)."""

    def __init__(
        self,
        *,
        region_name: str = "us-east-1",
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
    ) -> None:
        from windrose_common.secrets import AWSSecretsManagerStore as _AWS

        self._store = _AWS(
            region_name=region_name,
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    async def put(self, path: str, data: dict[str, str]) -> None:
        await self._store.put(path, data)

    async def get(self, path: str) -> dict[str, str] | None:
        return await self._store.get(path)

    async def delete(self, path: str) -> None:
        await self._store.delete(path)

    async def schedule_destroy(self, path: str, destroy_at: datetime) -> None:
        await self._store.schedule_destroy(path, destroy_at)


class AzureKeyVaultStore:
    """Real Azure Key Vault secrets store via the shared ``windrose_common``
    adapter (BYO Infra Hardening Phase 2). Selected by
    ``SECRETS_BACKEND=azure``. No local Key Vault emulator exists; unit/mock-
    tested only (see the contract test suite's honesty note)."""

    def __init__(self, *, vault_url: str | None = None) -> None:
        from windrose_common.secrets import AzureKeyVaultStore as _Azure

        self._store = _Azure(vault_url=vault_url)

    async def put(self, path: str, data: dict[str, str]) -> None:
        await self._store.put(path, data)

    async def get(self, path: str) -> dict[str, str] | None:
        return await self._store.get(path)

    async def delete(self, path: str) -> None:
        await self._store.delete(path)

    async def schedule_destroy(self, path: str, destroy_at: datetime) -> None:
        await self._store.schedule_destroy(path, destroy_at)


class GCPSecretManagerStore:
    """Real GCP Secret Manager secrets store via the shared ``windrose_common``
    adapter (BYO Infra Hardening Phase 2). Selected by
    ``SECRETS_BACKEND=gcp``. No local Secret Manager emulator exists; unit/mock-
    tested only (see the contract test suite's honesty note)."""

    def __init__(self, *, project_id: str | None = None) -> None:
        from windrose_common.secrets import GCPSecretManagerStore as _GCP

        self._store = _GCP(project_id=project_id)

    async def put(self, path: str, data: dict[str, str]) -> None:
        await self._store.put(path, data)

    async def get(self, path: str) -> dict[str, str] | None:
        return await self._store.get(path)

    async def delete(self, path: str) -> None:
        await self._store.delete(path)

    async def schedule_destroy(self, path: str, destroy_at: datetime) -> None:
        await self._store.schedule_destroy(path, destroy_at)
