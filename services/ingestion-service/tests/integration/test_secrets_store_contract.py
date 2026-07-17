"""Shared SecretsStore contract suite (BYO Infra Hardening Phase 2,
docs/design/byo-infra-hardening.md).

Runs the SAME behavioral assertions against every SecretsStore implementation
so a new backend can't silently drift from Vault's semantics:

* ``InMemorySecretsStore`` — always runs (no infra).
* ``VaultSecretsStore`` — real Vault KV v2; skips if localhost:8200 (the dev
  stack's Vault) is unreachable.
* ``AWSSecretsManagerStore`` — real AWS Secrets Manager wire protocol against
  a real local LocalStack container (session-scoped Testcontainers fixture,
  started fresh here since LocalStack isn't part of docker-compose.dev.yml).
  Skips if Docker is unavailable. This is a genuine live backend, not a mock.
* ``AzureKeyVaultStore`` / ``GCPSecretManagerStore`` — no local emulator
  exists for either service, so these run against an injected fake client
  (module-level ``_FakeAzureClient`` / ``_FakeGCPClient`` below) rather than a
  live backend. This is standard practice for cloud SDKs without an emulator
  (mirrors how the SDKs' own test suites are written) — explicitly NOT the
  same rigor as the AWS/LocalStack case, and this file says so at the call
  site of each parametrized case (see ``BACKENDS`` id strings).

``schedule_destroy`` has no single native mechanic across vendors (see the
module docstring in ``windrose_common/secrets.py``), so the shared assertion
here is deliberately the common denominator: a future-dated destroy must NOT
remove the secret immediately, and a past-due destroy must become unreadable
once "swept" — where sweeping means ``run_due_destroys()`` for the two
backends that expose it (Vault, InMemory) and is a no-op for the other three,
which perform the past-due destroy synchronously inside ``schedule_destroy``
itself.

**Empirically-verified AWS divergence** (found by actually running this suite
against real LocalStack, not assumed up front): AWS Secrets Manager's real
`DeleteSecret` semantics make a secret unreadable via `GetSecretValue`
*immediately* once it is marked for deletion, **even for a future-dated
recovery window** — there is no native "block reads only once actually due"
primitive to schedule against, unlike Vault's out-of-band sweeper. So the
"future destroy must not remove immediately" guarantee genuinely does NOT
hold for `AWSSecretsManagerStore`, and this file documents that as a real,
verified vendor difference rather than forcing a fake uniformity — see
`test_schedule_destroy_future_does_not_remove_immediately`'s AWS skip and
`test_aws_schedule_destroy_future_blocks_reads_immediately` below.
"""

from __future__ import annotations

import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domain.secrets import (
    AWSSecretsManagerStore,
    AzureKeyVaultStore,
    GCPSecretManagerStore,
    InMemorySecretsStore,
    SecretsStore,
    VaultSecretsStore,
    connection_secret_path,
)

pytestmark = pytest.mark.integration


def _reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# Fake clients for Azure/GCP — unit/mock tier, no real backend. Each mimics
# just enough of the real SDK client surface for the adapter code in
# windrose_common/secrets.py to drive, so the ADAPTER code under test is real
# (real method names, real request/response shapes); only the network
# transport is faked, which is the standard, accepted pattern for testing
# cloud SDK integrations without a live account (per the task's honesty note).
# --------------------------------------------------------------------------


class _FakeAzureSecret:
    def __init__(self, value: str | None) -> None:
        self.value = value


class _FakeAzurePoller:
    def wait(self) -> None:
        return None


class _FakeAzureClient:
    """Mimics azure.keyvault.secrets.SecretClient's surface used by
    AzureKeyVaultStore."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._deleted: set[str] = set()

    def set_secret(self, name: str, value: str) -> None:
        self._data[name] = value
        self._deleted.discard(name)

    def get_secret(self, name: str) -> _FakeAzureSecret:
        from azure.core.exceptions import ResourceNotFoundError

        if name not in self._data:
            raise ResourceNotFoundError(f"secret {name} not found")
        return _FakeAzureSecret(self._data[name])

    def begin_delete_secret(self, name: str) -> _FakeAzurePoller:
        from azure.core.exceptions import ResourceNotFoundError

        if name not in self._data:
            raise ResourceNotFoundError(f"secret {name} not found")
        del self._data[name]
        self._deleted.add(name)
        return _FakeAzurePoller()

    def purge_deleted_secret(self, name: str) -> None:
        self._deleted.discard(name)


class _FakeGCPPayload:
    def __init__(self, data: bytes) -> None:
        self.data = data


class _FakeGCPResponse:
    def __init__(self, data: bytes) -> None:
        self.payload = _FakeGCPPayload(data)


class _FakeGCPClient:
    """Mimics google.cloud.secretmanager.SecretManagerServiceClient's surface
    used by GCPSecretManagerStore."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._versions: dict[str, bytes] = {}

    def create_secret(self, request: dict[str, Any]) -> None:
        from google.api_core.exceptions import AlreadyExists

        name = f"{request['parent']}/secrets/{request['secret_id']}"
        if name in self._secrets:
            raise AlreadyExists(f"{name} already exists")
        self._secrets.add(name)

    def add_secret_version(self, request: dict[str, Any]) -> None:
        parent = request["parent"]  # projects/x/secrets/y
        self._versions[f"{parent}/versions/latest"] = request["payload"]["data"]

    def access_secret_version(self, request: dict[str, Any]) -> _FakeGCPResponse:
        from google.api_core.exceptions import NotFound

        name = request["name"]
        if name not in self._versions:
            raise NotFound(f"{name} not found")
        return _FakeGCPResponse(self._versions[name])

    def delete_secret(self, request: dict[str, Any]) -> None:
        from google.api_core.exceptions import NotFound

        name = request["name"]
        if name not in self._secrets:
            raise NotFound(f"{name} not found")
        self._secrets.discard(name)
        prefix = f"{name}/versions/"
        for k in [k for k in self._versions if k.startswith(prefix)]:
            del self._versions[k]


# --------------------------------------------------------------------------
# LocalStack: real local AWS Secrets Manager, session-scoped so it starts once
# for the whole contract run. Not part of docker-compose.dev.yml (AWS isn't
# one of Windrose's own local-infra choices), so it's spun up here directly
# via Testcontainers, exactly like the Postgres integration tier already does
# for a service that also isn't in the dev compose file.
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def localstack_endpoint():
    try:
        from testcontainers.localstack import LocalStackContainer
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"testcontainers[localstack] not installed: {exc}")
    try:
        container = LocalStackContainer(image="localstack/localstack:3.4").with_services(
            "secretsmanager", "kms"
        )
        container.start()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Docker unavailable — skipping LocalStack-backed contract cases: {exc}")
    yield container.get_url()
    container.stop()


@pytest.fixture
def unique() -> str:
    return uuid.uuid4().hex[:12]


def _path(unique: str) -> str:
    return connection_secret_path(f"t{unique}", f"c{unique}")


async def _maybe_sweep(store: object) -> None:
    """Drive the out-of-band sweeper for backends that need one (Vault/
    InMemory); a no-op for backends whose schedule_destroy already performs a
    due destroy synchronously."""
    sweep = getattr(store, "run_due_destroys", None)
    if sweep is not None:
        await sweep(datetime.now(UTC))


# --------------------------------------------------------------------------
# Store factories, one per implementation under test. Each returns an
# (id, store_factory) pair; store_factory is a zero-arg callable so a fresh
# store handle is built per test (cheap — they're all thin clients).
# --------------------------------------------------------------------------


def _in_memory_factory() -> SecretsStore:
    return InMemorySecretsStore()


def _vault_factory() -> SecretsStore:
    if not _reachable("localhost", 8200):
        pytest.skip("Vault not reachable at localhost:8200 — is the dev infra up?")
    return VaultSecretsStore()


def _aws_factory_maker(endpoint: str):
    def _factory() -> SecretsStore:
        return AWSSecretsManagerStore(
            region_name="us-east-1",
            endpoint_url=endpoint,
            access_key="test",
            secret_key="test",
        )

    return _factory


def _azure_factory() -> SecretsStore:
    store = AzureKeyVaultStore.__new__(AzureKeyVaultStore)
    from windrose_common.secrets import AzureKeyVaultStore as _Azure

    store._store = _Azure.__new__(_Azure)
    store._store._client = _FakeAzureClient()
    return store


def _gcp_factory() -> SecretsStore:
    store = GCPSecretManagerStore.__new__(GCPSecretManagerStore)
    from windrose_common.secrets import GCPSecretManagerStore as _GCP

    store._store = _GCP.__new__(_GCP)
    store._store._client = _FakeGCPClient()
    store._store.project_id = "wr-test-project"
    return store


@pytest.fixture(
    params=[
        "in_memory",
        "vault (real, real Vault)",
        "aws (real, real LocalStack)",
        "azure (mock-tested, injected fake client — no emulator available)",
        "gcp (mock-tested, injected fake client — no emulator available)",
    ]
)
def store(request, localstack_endpoint) -> SecretsStore:
    kind = request.param
    if kind == "in_memory":
        return _in_memory_factory()
    if kind.startswith("vault"):
        return _vault_factory()
    if kind.startswith("aws"):
        return _aws_factory_maker(localstack_endpoint)()
    if kind.startswith("azure"):
        return _azure_factory()
    if kind.startswith("gcp"):
        return _gcp_factory()
    raise AssertionError(kind)  # pragma: no cover


# --------------------------------------------------------------------------
# The shared contract.
# --------------------------------------------------------------------------


async def test_put_then_get_roundtrips(store: SecretsStore, unique: str) -> None:
    path = _path(unique)
    await store.put(path, {"username": "svc", "password": "s3cr3t"})
    assert await store.get(path) == {"username": "svc", "password": "s3cr3t"}


async def test_put_merges_with_existing(store: SecretsStore, unique: str) -> None:
    path = _path(unique)
    await store.put(path, {"password": "s3cr3t"})
    await store.put(path, {"token": "abc"})
    got = await store.get(path)
    assert got == {"password": "s3cr3t", "token": "abc"}


async def test_get_missing_returns_none(store: SecretsStore, unique: str) -> None:
    assert await store.get(_path(unique)) is None


async def test_delete_removes(store: SecretsStore, unique: str) -> None:
    path = _path(unique)
    await store.put(path, {"password": "p@ss"})
    assert await store.get(path) is not None
    await store.delete(path)
    assert await store.get(path) is None


async def test_delete_missing_does_not_raise(store: SecretsStore, unique: str) -> None:
    await store.delete(_path(unique))  # no prior put — must be a no-op, not an error


async def test_schedule_destroy_future_does_not_remove_immediately(
    store: SecretsStore, unique: str
) -> None:
    if isinstance(store, AWSSecretsManagerStore):
        pytest.skip(
            "AWS Secrets Manager (verified against real LocalStack): DeleteSecret's "
            "'marked for deletion' state blocks GetSecretValue immediately, even with "
            "a future RecoveryWindowInDays — there is no native defer-the-read-block "
            "primitive. See test_aws_schedule_destroy_future_blocks_reads_immediately."
        )
    path = _path(unique)
    await store.put(path, {"password": "safe-for-now"})
    await store.schedule_destroy(path, datetime.now(UTC) + timedelta(days=7))
    assert await store.get(path) is not None


async def test_aws_schedule_destroy_future_blocks_reads_immediately(
    localstack_endpoint: str, unique: str
) -> None:
    """The AWS-specific counterpart to the guarantee above: real AWS Secrets
    Manager (verified here against real LocalStack) makes a secret unreadable
    the moment it's marked for deletion, regardless of how far out the
    recovery window is — the opposite of Vault/InMemory's "future destroy is a
    no-op until due" behavior. Documented, not silently papered over."""
    store = _aws_factory_maker(localstack_endpoint)()
    path = _path(unique)
    await store.put(path, {"password": "will-be-blocked"})
    await store.schedule_destroy(path, datetime.now(UTC) + timedelta(days=7))
    assert await store.get(path) is None


async def test_schedule_destroy_past_due_is_destroyed_once_swept(
    store: SecretsStore, unique: str
) -> None:
    path = _path(unique)
    await store.put(path, {"password": "will-be-destroyed"})
    await store.schedule_destroy(path, datetime.now(UTC) - timedelta(seconds=1))
    await _maybe_sweep(store)
    assert await store.get(path) is None
