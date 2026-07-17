"""Dependency wiring. Two modes: ``memory`` (unit/dev) and ``sql`` (integration/prod).

Real runtime (``PPL_USE_REAL_ADAPTERS=true``) wires the shared windrose_common
adapters (Kafka, Redis, OPA, MinIO) + the REAL local training executor + real MLflow
gateway; the local executor is the default execution backend on the Mac. Unit tests
inject in-memory doubles for the executor/MLflow via ``build_container`` kwargs.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app.adapters.manifest_store import LocalFSManifestStore, S3ManifestStore
from app.adapters.mlflow_gateway import MlflowGateway
from app.api.auth import LocalScopeAuthz, OpaAuthzClient, TokenVerifier
from app.config import Settings
from app.domain.catalog import seed_algorithm_templates, seed_components
from app.domain.scheduler import PipelineScheduleService
from app.domain.services import (
    AdminService,
    AlgorithmInstantiationService,
    CatalogService,
    RunService,
    ServiceDeps,
    TemplateService,
)
from app.events.consumer import PipelineEventConsumer
from app.executor.local import LocalTrainingExecutor
from app.mcp.facade import McpFacade
from app.store.memory import MemoryState, memory_uow_factory
from app.utils import Clock

logger = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    clock: Clock
    deps: ServiceDeps
    catalog_service: CatalogService
    template_service: TemplateService
    run_service: RunService
    schedule_service: PipelineScheduleService
    instantiation_service: AlgorithmInstantiationService
    admin_service: AdminService
    consumer: PipelineEventConsumer
    mcp: McpFacade
    token_verifier: TokenVerifier
    authz: Any
    dedup: Any
    memory_state: MemoryState | None = None
    extras: dict = field(default_factory=dict)

    def schedule_drive(self, tenant_id: str, run_id: str) -> None:
        """Fire-and-forget local execution of a submitted run (202 returns first)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self.run_service.drive_run(tenant_id, run_id))
        self.extras.setdefault("drive_tasks", set()).add(task)
        task.add_done_callback(lambda t: self.extras["drive_tasks"].discard(t))


def build_container(
    settings: Settings | None = None,
    *,
    mode: str = "memory",
    session_factory=None,
    clock: Clock | None = None,
    executor=None,
    mlflow=None,
    feature_source=None,
) -> Container:
    settings = settings or Settings()
    clock = clock or Clock()

    memory_state: MemoryState | None = None
    if mode == "memory":
        memory_state = MemoryState()
        uow_factory = memory_uow_factory(memory_state)
        from app.store.memory import MemoryScheduleScanner, _InMemoryDedup

        dedup = _InMemoryDedup()
        schedule_scanner = MemoryScheduleScanner(memory_state)
    elif mode == "sql":
        if session_factory is None:
            raise ValueError("sql mode requires a session_factory")
        from app.store.sql import SqlDedupStore, SqlScheduleScanner, sql_uow_factory

        uow_factory = sql_uow_factory(session_factory)
        dedup = SqlDedupStore(session_factory)
        schedule_scanner = SqlScheduleScanner(session_factory)
    else:
        raise ValueError(f"unknown mode {mode!r}")

    if settings.use_real_adapters:
        from windrose_common.redisx import RedisDedupStore, build_redis

        dedup = RedisDedupStore(build_redis(settings.redis_url))

    # Component + algorithm catalog (in-process cache; persisted to DB in real mode).
    components = {c.name: c for c in seed_components()}
    algorithms = {a.name: a for a in seed_algorithm_templates()}

    # Manifest store + executor + MLflow gateway (REAL by default).
    if settings.use_real_adapters:
        from windrose_common.objectstore import S3BlobObjectStore, S3Config

        s3_cfg = S3Config.for_minio(
            settings.artifacts_bucket, endpoint_url=settings.s3_endpoint_url,
            access_key=settings.s3_access_key, secret_key=settings.s3_secret_key,
            region=settings.s3_region)
        blob = S3BlobObjectStore(s3_cfg)
        manifest_store = S3ManifestStore(blob)
        if feature_source is None:
            from app.adapters.feature_source import ObjectStoreFeatureSource

            feature_source = ObjectStoreFeatureSource(blob)
    else:
        manifest_store = LocalFSManifestStore(settings.object_store_dir)

    executor = executor or LocalTrainingExecutor(settings.mlflow_tracking_uri)
    mlflow = mlflow or MlflowGateway(settings.mlflow_tracking_uri,
                                     settings.mlflow_experiment)

    # Dataset row reader: the real HTTP reader hits dataset-service's internal rows API
    # so a read-from-warehouse node materializes an uploaded dataset at run time; the
    # in-memory reader is unit-tier only (never reachable from the shipped wiring).
    if settings.use_real_adapters:
        from app.adapters.dataset_reader import HttpDatasetReader

        dataset_reader = HttpDatasetReader(
            settings.dataset_service_url, settings.dataset_reader_spiffe)
    else:
        from app.adapters.dataset_reader import InMemoryDatasetReader

        dataset_reader = InMemoryDatasetReader({})

    # Execution backend selection via the swappable workflow-backend registry
    # (Phase 3): "local" (default, real training on the Mac → None/inline) or the
    # INFRA-GATED "argo" adapter (real Argo server REST; raises
    # DependencyUnavailable when no k8s cluster/Argo server is reachable). Adding
    # a backend is a one-line register() in app/executor/registry.py.
    from app.executor.registry import resolve_workflow_backend

    workflow_backend = resolve_workflow_backend(settings)

    deps = ServiceDeps(
        settings=settings, clock=clock, uow_factory=uow_factory, components=components,
        algorithms=algorithms, manifest_store=manifest_store, executor=executor,
        mlflow=mlflow, feature_source=feature_source, dataset_reader=dataset_reader,
        events_topic=settings.events_topic, workflow_backend=workflow_backend)

    catalog_service = CatalogService(deps)
    template_service = TemplateService(deps)
    run_service = RunService(deps, template_service)
    schedule_service = PipelineScheduleService(deps, run_service, schedule_scanner)
    instantiation_service = AlgorithmInstantiationService(deps, template_service)
    admin_service = AdminService(deps)
    consumer = PipelineEventConsumer(
        deps, dedup, feature_source=feature_source,
        default_quota={
            "max_concurrent_runs": settings.default_max_concurrent_runs,
            "max_concurrent_pods": settings.default_max_concurrent_pods,
            "max_run_duration_minutes": settings.default_max_run_duration_minutes,
            "min_seconds_between_runs": settings.default_min_seconds_between_runs})
    mcp = McpFacade(catalog_service, template_service, run_service, instantiation_service)

    authz = (OpaAuthzClient(settings.opa_url, redis_url=settings.redis_url)
             if settings.use_real_adapters else LocalScopeAuthz())

    return Container(
        settings=settings, clock=clock, deps=deps, catalog_service=catalog_service,
        template_service=template_service, run_service=run_service,
        schedule_service=schedule_service,
        instantiation_service=instantiation_service, admin_service=admin_service,
        consumer=consumer, mcp=mcp, token_verifier=TokenVerifier(settings), authz=authz,
        dedup=dedup, memory_state=memory_state,
        extras={"session_factory": session_factory, "components": list(components.values()),
                "algorithms": list(algorithms.values())})
