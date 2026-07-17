"""Composition root: wires ports to adapters.

Two wirings, selected by ``settings.adapter_mode``:

* ``"real"`` (runtime default via ``Settings.from_env``) wires the shared
  ``windrose_common`` adapters against local, protocol-compatible infra —
  MinIO (object store), the Iceberg REST catalog (table writer), Vault
  (secrets), Redpanda/Kafka (event publisher) and OPA (policy). No stub is
  reachable from this path (CONVENTIONS.md END STATE).
* ``"memory"`` wires the in-memory/local test doubles used by the unit tier
  (never reached by ``app.main`` in production).

Scheduler (in-process cron) is real logic, not a stub. In the real wiring every
declared connector type resolves to a real driver (local-protocol or
credential-gated SDK); the registry DEFAULT rejects anything left unwired with
an explicit UNSUPPORTED_CONNECTOR error — no deterministic double is reachable
from the real path. The fakes remain the defaults only in "memory" mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.api.auth import JWKSKeyProvider
from app.config import Settings
from app.domain.drivers import (
    DispatchingSourcePreviewer,
    FetcherRegistry,
    wire_local_drivers,
)
from app.domain.objectstore import LocalFSObjectStore, ObjectStore, S3ObjectStore
from app.domain.policy import OPAPolicyEngine, PolicyEngine, StaticPolicyEngine
from app.domain.probers import (
    FakeConnectionProber,
    FakeSourcePreviewer,
    ProberRegistry,
    SourcePreviewer,
    UnsupportedConnectorProber,
    UnsupportedSourcePreviewer,
)
from app.domain.querysource import (
    FakeQuerySource,
    QuerySourceRegistry,
    UnsupportedQuerySource,
)
from app.domain.scheduler import InProcessScheduler, Scheduler
from app.domain.secrets import (
    AWSSecretsManagerStore,
    AzureKeyVaultStore,
    GCPSecretManagerStore,
    InMemorySecretsStore,
    SecretsStore,
    VaultSecretsStore,
)
from app.domain.tablewriter import IcebergTableWriter, ParquetFileTableWriter, TableWriter
from app.events.outbox import EventPublisher, InMemoryEventPublisher, KafkaEventPublisher
from app.store.db import Database, build_engine


@dataclass(slots=True)
class Container:
    settings: Settings
    db: Database
    secrets: SecretsStore
    object_store: ObjectStore
    table_writer: TableWriter
    scheduler: Scheduler
    probers: ProberRegistry
    previewer: SourcePreviewer
    query_sources: QuerySourceRegistry
    policy: PolicyEngine
    publisher: EventPublisher
    # remote-file source fetchers (SFTP/HTTP → object store); empty in memory tier
    fetchers: FetcherRegistry = field(default_factory=FetcherRegistry)
    # cached JWKS provider (real JWKS refresh) when jwks_url is configured
    jwks: object | None = None
    # in-process scheduler bookkeeping (Temporal carries tenant context natively)
    schedule_tenants: dict[str, str] = field(default_factory=dict)
    scheduler_bound: bool = False


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or Settings.from_env()
    if settings.adapter_mode == "real":
        return _build_real(settings)
    return _build_memory(settings)


def _build_memory(settings: Settings) -> Container:
    data_dir = Path(settings.data_dir)
    return Container(
        settings=settings,
        db=Database(build_engine(settings.database_url)),
        secrets=InMemorySecretsStore(),
        object_store=LocalFSObjectStore(data_dir / "objects"),
        table_writer=ParquetFileTableWriter(data_dir / "bronze"),
        scheduler=InProcessScheduler(),
        probers=ProberRegistry(default=FakeConnectionProber()),
        previewer=FakeSourcePreviewer(),
        query_sources=QuerySourceRegistry(default=FakeQuerySource()),
        policy=StaticPolicyEngine(),
        publisher=InMemoryEventPublisher(),
    )


def _build_secrets_store(settings: Settings) -> SecretsStore:
    """SECRETS_BACKEND=vault|aws|azure|gcp (BYO Infra Hardening Phase 2,
    docs/design/byo-infra-hardening.md). Default "vault" preserves the prior
    Vault-only behavior unchanged when the env var is unset."""
    backend = (settings.secrets_backend or "vault").lower()
    if backend == "vault":
        return VaultSecretsStore(addr=settings.vault_addr, token=settings.vault_token)
    if backend == "aws":
        return AWSSecretsManagerStore(
            region_name=settings.aws_region,
            endpoint_url=settings.aws_secrets_endpoint_url,
            access_key=settings.aws_access_key_id,
            secret_key=settings.aws_secret_access_key,
        )
    if backend == "azure":
        return AzureKeyVaultStore(vault_url=settings.azure_key_vault_url)
    if backend == "gcp":
        return GCPSecretManagerStore(project_id=settings.gcp_project_id)
    raise ValueError(f"unknown SECRETS_BACKEND: {settings.secrets_backend!r}")


def _build_real(settings: Settings) -> Container:
    # Registry DEFAULTS in the real container FAIL HONESTLY: any connector type
    # without an explicitly wired real driver (today: only `presto`) raises
    # UNSUPPORTED_CONNECTOR at create/test/preview time and fails query jobs
    # permanently — a fake prober must never let a driverless connection
    # "test OK" and then ingest zero rows silently. Real local-protocol and
    # credential-gated SDK drivers are wired on top by wire_local_drivers.
    probers = ProberRegistry(default=UnsupportedConnectorProber())
    query_sources = QuerySourceRegistry(default=UnsupportedQuerySource())
    fetchers = FetcherRegistry()
    previewer = DispatchingSourcePreviewer(default=UnsupportedSourcePreviewer())
    wire_local_drivers(settings, probers, query_sources, fetchers, previewer)

    return Container(
        settings=settings,
        db=Database(build_engine(settings.database_url)),
        secrets=_build_secrets_store(settings),
        object_store=S3ObjectStore(
            settings.uploads_bucket,
            endpoint_url=settings.s3_endpoint_url,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
        ),
        table_writer=IcebergTableWriter(
            settings.iceberg_catalog_uri,
            warehouse=settings.iceberg_warehouse,
            s3_endpoint=settings.s3_endpoint_url,
            s3_access_key=settings.s3_access_key,
            s3_secret_key=settings.s3_secret_key,
            s3_region=settings.s3_region,
        ),
        scheduler=InProcessScheduler(),
        probers=probers,
        previewer=previewer,
        query_sources=query_sources,
        fetchers=fetchers,
        policy=OPAPolicyEngine(settings.opa_url, redis_url=settings.redis_url),
        publisher=KafkaEventPublisher(settings.kafka_bootstrap_servers),
        jwks=(
            JWKSKeyProvider(settings.jwks_url, settings.jwks_ttl_seconds)
            if settings.jwks_url
            else None
        ),
    )
