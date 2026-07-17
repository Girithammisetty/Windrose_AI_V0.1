"""Real secrets adapters implementing the SecretsStore port (BYO Infra
Hardening Phase 2, ``docs/design/byo-infra-hardening.md``).

Four backends, selected at the service composition root via
``SECRETS_BACKEND=vault|aws|azure|gcp`` (default ``vault``, unchanged behavior
when unset):

* ``VaultSecretsStore`` — Vault KV v2 (hvac). The original, still first-class.
* ``AWSSecretsManagerStore`` — AWS Secrets Manager (boto3). Live-verified
  against a real local LocalStack container (Secrets Manager is fully
  supported there) — genuinely exercises the wire protocol, not a mock.
* ``AzureKeyVaultStore`` — Azure Key Vault secrets (``azure-keyvault-secrets``).
  No local Key Vault emulator exists; unit-tested with an injected fake client
  (standard practice for this SDK without a real tenant).
* ``GCPSecretManagerStore`` — GCP Secret Manager (``google-cloud-secret-manager``).
  Same honesty note as Azure: unit-tested with an injected fake client, no real
  GCP project available in this environment.

All four speak the same path scheme Windrose already uses for Vault refs
(``secret/data/tenants/<tenant>/connections/<id>``); each backend has its own
naming restrictions (Key Vault/Secret Manager secret IDs can't contain ``/``),
so AWS/Azure/GCP each sanitize the path into a backend-legal name internally —
callers only ever see the original Vault-shaped path.

``schedule_destroy`` (ING-FR-006, 7-day grace) has no single native mechanic
across vendors, so each backend maps it onto its own closest primitive: Vault
stamps custom metadata + enqueues for the ``run_due_destroys`` sweeper; AWS
Secrets Manager natively supports a scheduled-deletion window
(``RecoveryWindowInDays``, 7-30 days) for a future ``destroy_at`` and a real
force-delete for one that's already due; Azure/GCP (no native grace-window
primitive) delete for real once due and otherwise no-op until due. See
``services/ingestion-service/tests/integration/test_secrets_store_contract.py``
for the exact shared behavioral guarantees asserted across all four.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import re
from datetime import UTC, datetime

import hvac

DESTROY_QUEUE_PREFIX = "_destroy_queue"


def _parse_ref(path: str) -> tuple[str, str]:
    """Translate a ``<mount>/data/<rel>`` (or ``<mount>/metadata/<rel>``) ref into
    ``(mount_point, relative_path)`` for the hvac KV v2 helpers."""
    segments = path.strip("/").split("/")
    if len(segments) >= 3 and segments[1] in ("data", "metadata"):
        return segments[0], "/".join(segments[2:])
    # already a relative path; assume the default `secret` mount
    return "secret", path.strip("/")


class VaultSecretsStore:
    def __init__(
        self,
        addr: str = "http://localhost:8200",
        token: str = "windrose_dev_root",
        *,
        default_mount: str = "secret",
    ) -> None:
        self.addr = addr
        self.token = token
        self.default_mount = default_mount
        self._client = hvac.Client(url=addr, token=token)

    def _kv(self):
        return self._client.secrets.kv.v2

    async def put(self, path: str, data: dict[str, str]) -> None:
        mount, rel = _parse_ref(path)
        # merge with any existing secret (matches InMemorySecretsStore semantics)
        existing = await self.get(path) or {}
        merged = {**existing, **data}
        await asyncio.to_thread(
            self._kv().create_or_update_secret,
            path=rel,
            secret=merged,
            mount_point=mount,
        )

    async def get(self, path: str) -> dict[str, str] | None:
        mount, rel = _parse_ref(path)

        def _read() -> dict[str, str] | None:
            try:
                resp = self._kv().read_secret_version(
                    path=rel, mount_point=mount, raise_on_deleted_version=False
                )
            except hvac.exceptions.InvalidPath:
                return None
            data = (resp or {}).get("data", {}).get("data")
            return dict(data) if data else None

        return await asyncio.to_thread(_read)

    async def delete(self, path: str) -> None:
        mount, rel = _parse_ref(path)
        await asyncio.to_thread(
            self._kv().delete_metadata_and_all_versions, path=rel, mount_point=mount
        )

    async def schedule_destroy(self, path: str, destroy_at: datetime) -> None:
        mount, rel = _parse_ref(path)

        def _schedule() -> None:
            # stamp the secret's own metadata (real Vault custom metadata)
            try:
                self._kv().update_metadata(
                    path=rel,
                    mount_point=mount,
                    custom_metadata={"destroy_at": destroy_at.astimezone(UTC).isoformat()},
                )
            except hvac.exceptions.InvalidPath:
                pass
            # enqueue for the sweeper
            token = base64.urlsafe_b64encode(f"{mount}:{rel}".encode()).decode()
            self._kv().create_or_update_secret(
                path=f"{DESTROY_QUEUE_PREFIX}/{token}",
                secret={
                    "target_mount": mount,
                    "target_path": rel,
                    "destroy_at": destroy_at.astimezone(UTC).isoformat(),
                },
                mount_point=self.default_mount,
            )

        await asyncio.to_thread(_schedule)

    async def run_due_destroys(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        return await asyncio.to_thread(self._run_due_destroys_sync, now)

    def _run_due_destroys_sync(self, now: datetime) -> int:
        kv = self._kv()
        try:
            listing = kv.list_secrets(
                path=DESTROY_QUEUE_PREFIX, mount_point=self.default_mount
            )
        except hvac.exceptions.InvalidPath:
            return 0
        destroyed = 0
        for key in listing.get("data", {}).get("keys", []):
            queue_path = f"{DESTROY_QUEUE_PREFIX}/{key}"
            resp = kv.read_secret_version(
                path=queue_path,
                mount_point=self.default_mount,
                raise_on_deleted_version=False,
            )
            entry = resp.get("data", {}).get("data", {})
            due = entry.get("destroy_at")
            if not due:
                continue
            if datetime.fromisoformat(due) <= now:
                kv.delete_metadata_and_all_versions(
                    path=entry["target_path"], mount_point=entry["target_mount"]
                )
                kv.delete_metadata_and_all_versions(
                    path=queue_path, mount_point=self.default_mount
                )
                destroyed += 1
        return destroyed

    def scheduled_destroys(self) -> dict[str, str]:
        """Introspection helper (mirrors the in-memory fake's attribute)."""
        kv = self._kv()
        out: dict[str, str] = {}
        try:
            listing = kv.list_secrets(
                path=DESTROY_QUEUE_PREFIX, mount_point=self.default_mount
            )
        except hvac.exceptions.InvalidPath:
            return out
        for key in listing.get("data", {}).get("keys", []):
            resp = kv.read_secret_version(
                path=f"{DESTROY_QUEUE_PREFIX}/{key}",
                mount_point=self.default_mount,
                raise_on_deleted_version=False,
            )
            entry = resp.get("data", {}).get("data", {})
            out[json.dumps({"m": entry.get("target_mount"), "p": entry.get("target_path")})] = (
                entry.get("destroy_at", "")
            )
        return out


def _sanitize_name(prefix: str, path: str) -> str:
    """Map a Vault-shaped path (``secret/data/tenants/<t>/connections/<c>``)
    onto a name legal for backends that reject ``/`` in secret IDs (Key Vault,
    Secret Manager). Deterministic and collision-safe enough for Windrose's own
    generated paths (tenant/connection ids are already unique)."""
    rel = path.strip("/")
    safe = re.sub(r"[^a-zA-Z0-9-]", "-", rel)
    safe = re.sub(r"-+", "-", safe).strip("-")
    return f"{prefix}-{safe}"[:255]


class AWSSecretsManagerStore:
    """Real AWS Secrets Manager adapter (boto3) implementing SecretsStore.

    Live-verified against a real local LocalStack container in
    ``services/ingestion-service/tests/integration/test_secrets_store_contract.py``
    — Secrets Manager is fully emulated there, so this is a genuine
    put/get/delete/schedule round trip against the real API shape, not a mock.
    """

    def __init__(
        self,
        *,
        region_name: str = "us-east-1",
        endpoint_url: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        client: object | None = None,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            import boto3

            self._client = boto3.client(
                "secretsmanager",
                region_name=region_name,
                endpoint_url=endpoint_url,
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
            )

    @staticmethod
    def _name(path: str) -> str:
        return _sanitize_name("wr", path)

    async def put(self, path: str, data: dict[str, str]) -> None:
        name = self._name(path)
        existing = await self.get(path) or {}
        merged = {**existing, **data}
        payload = json.dumps(merged)

        def _put() -> None:
            try:
                self._client.put_secret_value(SecretId=name, SecretString=payload)
            except self._client.exceptions.ResourceNotFoundException:
                self._client.create_secret(Name=name, SecretString=payload)

        await asyncio.to_thread(_put)

    async def get(self, path: str) -> dict[str, str] | None:
        name = self._name(path)

        def _get() -> dict[str, str] | None:
            try:
                resp = self._client.get_secret_value(SecretId=name)
            except self._client.exceptions.ResourceNotFoundException:
                return None
            except self._client.exceptions.InvalidRequestException:
                # secret is scheduled for deletion (pending/due destroy)
                return None
            raw = resp.get("SecretString")
            return json.loads(raw) if raw else None

        return await asyncio.to_thread(_get)

    async def delete(self, path: str) -> None:
        name = self._name(path)

        def _delete() -> None:
            try:
                self._client.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)
            except self._client.exceptions.ResourceNotFoundException:
                pass

        await asyncio.to_thread(_delete)

    async def schedule_destroy(self, path: str, destroy_at: datetime) -> None:
        """Maps onto Secrets Manager's native scheduled-deletion window.

        A ``destroy_at`` already due is force-deleted now (matching the
        observable "past-due destroys become inaccessible immediately"
        guarantee the Vault/InMemory sweeper gives); a future ``destroy_at``
        is translated to the nearest legal ``RecoveryWindowInDays`` (AWS only
        accepts 7-30, not an arbitrary timestamp) — the real native grace-
        period primitive, not an approximation bolted on top.
        """
        name = self._name(path)
        now = datetime.now(UTC)
        due_at = destroy_at.astimezone(UTC)

        def _schedule() -> None:
            try:
                if due_at <= now:
                    self._client.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)
                else:
                    days = math.ceil((due_at - now).total_seconds() / 86400)
                    days = min(max(days, 7), 30)
                    self._client.delete_secret(SecretId=name, RecoveryWindowInDays=days)
            except self._client.exceptions.ResourceNotFoundException:
                pass

        await asyncio.to_thread(_schedule)


class AzureKeyVaultStore:
    """Real Azure Key Vault secrets adapter (``azure-keyvault-secrets``)
    implementing SecretsStore.

    No local Key Vault emulator exists (per BYO Infra Hardening Phase 2's
    scoping), so this is unit-tested against an injected fake client rather
    than live-verified — honestly documented as mock-tested, not the same
    rigor as the AWS/LocalStack path. The adapter code itself makes real SDK
    calls; only the test double stands in for the network transport.
    """

    def __init__(
        self,
        *,
        vault_url: str | None = None,
        credential: object | None = None,
        client: object | None = None,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient

            if not vault_url:
                raise ValueError("AzureKeyVaultStore requires vault_url")
            self._client = SecretClient(
                vault_url=vault_url, credential=credential or DefaultAzureCredential()
            )

    @staticmethod
    def _name(path: str) -> str:
        return _sanitize_name("wr", path)

    async def put(self, path: str, data: dict[str, str]) -> None:
        name = self._name(path)
        existing = await self.get(path) or {}
        merged = {**existing, **data}
        await asyncio.to_thread(self._client.set_secret, name, json.dumps(merged))

    async def get(self, path: str) -> dict[str, str] | None:
        name = self._name(path)

        def _get() -> dict[str, str] | None:
            from azure.core.exceptions import ResourceNotFoundError

            try:
                secret = self._client.get_secret(name)
            except ResourceNotFoundError:
                return None
            return json.loads(secret.value) if secret and secret.value else None

        return await asyncio.to_thread(_get)

    async def delete(self, path: str) -> None:
        name = self._name(path)

        def _delete() -> None:
            from azure.core.exceptions import ResourceNotFoundError

            try:
                poller = self._client.begin_delete_secret(name)
                poller.wait()
                # purge so a re-`put` of the same path doesn't collide with a
                # soft-deleted (recoverable) secret of the same name.
                purge = getattr(self._client, "purge_deleted_secret", None)
                if purge:
                    purge(name)
            except ResourceNotFoundError:
                pass

        await asyncio.to_thread(_delete)

    async def schedule_destroy(self, path: str, destroy_at: datetime) -> None:
        """Key Vault has no native scheduled-deletion window; a due destroy is
        performed for real now, a future one is a documented no-op until due
        (a scheduler should re-invoke schedule_destroy or delete at/after
        destroy_at — the same shape Vault's out-of-band sweeper takes)."""
        if destroy_at.astimezone(UTC) <= datetime.now(UTC):
            await self.delete(path)


class GCPSecretManagerStore:
    """Real GCP Secret Manager adapter (``google-cloud-secret-manager``)
    implementing SecretsStore.

    No local Secret Manager emulator exists; unit-tested against an injected
    fake client (standard practice for this SDK), not live-verified — see the
    honesty note on ``AzureKeyVaultStore`` above, same rationale applies here.
    """

    def __init__(
        self,
        *,
        project_id: str | None = None,
        client: object | None = None,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            from google.cloud import secretmanager

            if not project_id:
                raise ValueError("GCPSecretManagerStore requires project_id")
            self._client = secretmanager.SecretManagerServiceClient()
        self.project_id = project_id

    def _secret_id(self, path: str) -> str:
        return _sanitize_name("wr", path)

    def _secret_path(self, path: str) -> str:
        return f"projects/{self.project_id}/secrets/{self._secret_id(path)}"

    async def put(self, path: str, data: dict[str, str]) -> None:
        existing = await self.get(path) or {}
        merged = {**existing, **data}
        payload = json.dumps(merged).encode()
        secret_id = self._secret_id(path)
        parent = f"projects/{self.project_id}"
        secret_name = self._secret_path(path)

        def _put() -> None:
            from google.api_core.exceptions import AlreadyExists

            try:
                self._client.create_secret(
                    request={
                        "parent": parent,
                        "secret_id": secret_id,
                        "secret": {"replication": {"automatic": {}}},
                    }
                )
            except AlreadyExists:
                pass
            self._client.add_secret_version(
                request={"parent": secret_name, "payload": {"data": payload}}
            )

        await asyncio.to_thread(_put)

    async def get(self, path: str) -> dict[str, str] | None:
        version_name = f"{self._secret_path(path)}/versions/latest"

        def _get() -> dict[str, str] | None:
            from google.api_core.exceptions import NotFound

            try:
                resp = self._client.access_secret_version(request={"name": version_name})
            except NotFound:
                return None
            raw = resp.payload.data
            return json.loads(raw) if raw else None

        return await asyncio.to_thread(_get)

    async def delete(self, path: str) -> None:
        secret_name = self._secret_path(path)

        def _delete() -> None:
            from google.api_core.exceptions import NotFound

            try:
                self._client.delete_secret(request={"name": secret_name})
            except NotFound:
                pass

        await asyncio.to_thread(_delete)

    async def schedule_destroy(self, path: str, destroy_at: datetime) -> None:
        """Secret Manager has no native scheduled-deletion window (same
        honesty note as AzureKeyVaultStore.schedule_destroy)."""
        if destroy_at.astimezone(UTC) <= datetime.now(UTC):
            await self.delete(path)
