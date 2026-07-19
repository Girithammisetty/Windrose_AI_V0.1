"""Dependency wiring (CONVENTIONS.md END STATE).

RUNTIME DEFAULT (``settings.use_real_adapters=True``): ``app.main:app`` / ``make
run`` / Docker wire the REAL adapters against local infra — Postgres+pgvector RLS
store, ai-gateway LLM, tool-plane MCP tools, memory-service, case-service,
realtime-hub, Redpanda (Kafka), Redis (kill registry), OPA, and a real RS256
signing key served at the JWKS endpoint. No in-memory double is reachable from
the running binary. The unit tier sets ``use_real_adapters=False`` and injects
doubles from ``adapters/fakes.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.constants import GRANT_ISSUER
from app.proposals.service import ProposalService
from app.runtime.engine import RunEngine
from app.signing import GrantIssuer, SigningKey, TokenMinter


@dataclass
class Container:
    settings: Settings
    signing_key: SigningKey
    grant_issuer: GrantIssuer
    token_minter: TokenMinter
    store: Any
    bus: Any
    realtime: Any
    llm: Any
    memory: Any
    case_reader: Any
    evidence_reader: Any
    ingestion_reader: Any
    experiment_reader: Any
    dataset_reader: Any
    pipeline_reader: Any
    pipeline_writer: Any
    semantic_reader: Any
    catalog_reader: Any
    tool_client: Any
    authz: Any
    kill_registry: Any
    token_verifier: Any
    proposal_service: ProposalService
    run_engine: RunEngine
    transcripts: Any = None
    session_proj: Any = None
    trainer: Any = None
    eval_gate: Any = None
    extras: dict = field(default_factory=dict)


def build_container(
    settings: Settings | None = None,
    *,
    mode: str | None = None,
    session_factory=None,
    store=None,
    llm=None,
    tool_client=None,
    memory=None,
    case_reader=None,
    evidence_reader=None,
    ingestion_reader=None,
    experiment_reader=None,
    dataset_reader=None,
    pipeline_reader=None,
    pipeline_writer=None,
    semantic_reader=None,
    catalog_reader=None,
    bus=None,
    realtime=None,
    authz=None,
    kill_registry=None,
    session_proj=None,
    eval_gate=None,
) -> Container:
    settings = settings or Settings()
    real = settings.use_real_adapters
    if mode is None:
        mode = settings.store_mode or ("sql" if real else "memory")

    engines: list = []

    # Signing key (real RS256; ephemeral keypair in dev/tests). ALWAYS real.
    signing_key = SigningKey(settings.grant_private_key_pem, settings.grant_kid)
    grant_issuer = GrantIssuer(signing_key, issuer=GRANT_ISSUER)
    token_minter = TokenMinter(signing_key, issuer=settings.obo_issuer,
                               audience=settings.obo_audience)

    # Store
    if store is None:
        if mode == "memory":
            from app.store.memory import InMemoryStore
            store = InMemoryStore()
        elif mode == "sql":
            from sqlalchemy.ext.asyncio import async_sessionmaker

            from app.store.sql import SqlStore, make_engine
            if session_factory is None:
                engine = make_engine(settings.database_url)
                engines.append(engine)
                session_factory = async_sessionmaker(engine, expire_on_commit=False)
            # Privileged (BYPASSRLS) session factory for cross-tenant control-plane
            # reads — today only the kill-switch admin list (ART-FR-073) needs it:
            # an operator must see every tenant's active kills, but the app role's
            # RLS session only ever proves a single tenant's GUC. Falls back to the
            # app-role factory (self._sf) when admin_database_url is unset/equal,
            # e.g. in unit tests using one superuser-backed sqlite/pg double.
            admin_session_factory = session_factory
            if settings.admin_database_url and settings.admin_database_url != settings.database_url:
                admin_engine = make_engine(settings.admin_database_url)
                engines.append(admin_engine)
                admin_session_factory = async_sessionmaker(admin_engine, expire_on_commit=False)
            store = SqlStore(session_factory, admin_session_factory)
        else:
            raise ValueError(f"unknown mode {mode!r}")

    # Event bus
    if bus is None:
        if real:
            from app.events.bus import KafkaEventBus
            bus = KafkaEventBus(settings.kafka_bootstrap_servers)
        else:
            from app.events.bus import InMemoryEventBus
            bus = InMemoryEventBus()

    # realtime-hub — publish to the INTERNAL producer listener with a service
    # JWT carrying scope realtime.publish (hub authenticatePublisher; signed
    # with the same RS256 key our rbac/A2A/OBO tokens use, verified by the hub
    # via the shared JWKS).
    if realtime is None:
        if real:
            from app.adapters.realtime import RealtimeHubClient

            def _publish_token(tenant_id: str) -> str:
                return token_minter.mint_service(
                    tenant_id=tenant_id, scopes=["realtime.publish"])

            realtime = RealtimeHubClient(settings.realtime_hub_internal_url,
                                         token_provider=_publish_token)
        else:
            from app.adapters.fakes import NoopRealtime
            realtime = NoopRealtime()

    # realtime-hub chat-authz session-ownership projection (rt:session:* keys).
    if session_proj is None:
        if real:
            from app.adapters.sessionproj import RedisSessionProjection
            session_proj = RedisSessionProjection(settings.redis_url)
        else:
            from app.adapters.sessionproj import InMemorySessionProjection
            session_proj = InMemorySessionProjection()

    # LLM (ai-gateway). REAL default; the X-Windrose-JWT is a self-signed service
    # token (identity-service in prod); the virtual key is minted per run.
    if llm is None:
        if real:
            from app.adapters.llm import AiGatewayLlmClient

            def _jwt_provider(tenant_id: str) -> str:
                return token_minter.mint_agent_autonomous(
                    tenant_id=tenant_id, agent_key="agent-runtime", agent_version=1,
                    scopes=["*"])

            # Tenant-scoped key minting is the real, always-correct default —
            # agent-runtime is ONE shared process for every tenant, so a single
            # fixed key (AR_AI_GATEWAY_VIRTUAL_KEY) only ever matches one tenant
            # and 401s any OTHER tenant's call that interleaves on this process.
            # Only fall back to the static key if it's explicitly configured
            # (a deliberate single-tenant dev override).
            vkey_provider = None
            if not settings.ai_gateway_virtual_key:
                from app.adapters.vkeys import TenantVirtualKeyProvider

                # Key minting is a privileged admin action (ai.key.write) —
                # agent-runtime authenticates as ITSELF (a trusted platform
                # component, typ=service), not as an autonomous business agent
                # (typ=agent_autonomous, the lower-trust principal used for the
                # actual chat/LLM call below) — agents legitimately should not
                # be able to self-mint arbitrary ai-gateway keys.
                def _key_mint_jwt_provider(tenant_id: str) -> str:
                    return token_minter.mint_service(
                        tenant_id=tenant_id, scopes=["ai.key.write"])

                vkey_provider = TenantVirtualKeyProvider(
                    settings.ai_gateway_url, jwt_provider=_key_mint_jwt_provider,
                    allowed_request_classes=[settings.ai_gateway_request_class]).get

            llm = AiGatewayLlmClient(
                settings.ai_gateway_url, chat_path=settings.ai_gateway_chat_path,
                model=settings.ai_gateway_model, virtual_key=settings.ai_gateway_virtual_key,
                vkey_provider=vkey_provider,
                jwt_provider=_jwt_provider, request_class=settings.ai_gateway_request_class,
                temperature=settings.llm_temperature, max_tokens=settings.llm_max_tokens)
        else:
            from app.adapters.fakes import FakeLlm
            llm = FakeLlm()

    # tool-plane
    if tool_client is None:
        if real:
            from app.adapters.tools import ToolPlaneClient
            tool_client = ToolPlaneClient(settings.tool_plane_url,
                                          mcp_path=settings.tool_plane_mcp_path)
        else:
            from app.adapters.fakes import FakeToolClient
            tool_client = FakeToolClient()

    # memory-service
    if memory is None:
        if real:
            from app.adapters.memory import MemoryServiceClient
            memory = MemoryServiceClient(settings.memory_service_url)
        else:
            from app.adapters.fakes import FakeMemory
            memory = FakeMemory()

    # case-service reader
    if case_reader is None:
        if real:
            from app.adapters.case import CaseServiceClient
            case_reader = CaseServiceClient(settings.case_service_url)
        else:
            from app.adapters.fakes import FakeCaseReader
            case_reader = FakeCaseReader()

    # case-evidence reader: reads + text-extracts a case's attachments so the
    # triage/copilot graphs reason over the actual documents (the follow-up to
    # task #77's attach/list/download). Wraps the real case client (which now
    # exposes list_evidence/download_evidence); fake = empty in unit mode.
    if evidence_reader is None:
        if real and hasattr(case_reader, "list_evidence"):
            from app.adapters.evidence import EvidenceReader
            evidence_reader = EvidenceReader(case_reader)
        else:
            from app.adapters.fakes import FakeEvidenceReader
            evidence_reader = FakeEvidenceReader()

    # eval-gate verifier (P1): confirms an agent version's attached eval-gate
    # result genuinely PASSED in eval-service before publish. Fake in unit mode
    # returns a configurable verdict (default: any non-empty gate id passes).
    if eval_gate is None:
        if real:
            from app.adapters.eval_gate import EvalGateVerifier
            eval_gate = EvalGateVerifier(settings.eval_service_url)
        else:
            from app.adapters.fakes import FakeEvalGate
            eval_gate = FakeEvalGate()

    # ingestion-service reader (onboarding grounding: connector catalog + schema
    # preview)
    if ingestion_reader is None:
        if real:
            from app.adapters.ingestion import IngestionServiceClient
            ingestion_reader = IngestionServiceClient(settings.ingestion_service_url)
        else:
            from app.adapters.fakes import FakeIngestionReader
            ingestion_reader = FakeIngestionReader()

    # experiment-service reader (inference grounding: registered models + versions
    # -> production version + its input schema)
    if experiment_reader is None:
        if real:
            from app.adapters.experiment import ExperimentServiceClient
            experiment_reader = ExperimentServiceClient(settings.experiment_service_url)
        else:
            from app.adapters.fakes import FakeExperimentReader
            experiment_reader = FakeExperimentReader()

    # dataset-service reader (inference grounding: input-dataset schema/row_count
    # for the dataset<->model feature-compatibility check)
    if dataset_reader is None:
        if real:
            from app.adapters.dataset import DatasetServiceClient
            dataset_reader = DatasetServiceClient(settings.dataset_service_url)
        else:
            from app.adapters.fakes import FakeDatasetReader
            dataset_reader = FakeDatasetReader()

    # pipeline-orchestrator reader (model-training grounding: algorithm-template
    # catalog + the chosen algorithm's parameter schema the plan fills)
    if pipeline_reader is None:
        if real:
            from app.adapters.pipeline import PipelineOrchestratorClient
            pipeline_reader = PipelineOrchestratorClient(settings.pipeline_orchestrator_url)
        else:
            from app.adapters.fakes import FakePipelineReader
            pipeline_reader = FakePipelineReader()

    # pipeline-orchestrator WRITER (BRD 52 ml-engineer: sandboxed, OBO-authorized
    # training launches — reversible artifacts only; promotion stays a proposal)
    if pipeline_writer is None:
        if real:
            from app.adapters.pipeline import PipelineWriter
            pipeline_writer = PipelineWriter(settings.pipeline_orchestrator_url)
        else:
            from app.adapters.fakes import FakePipelineWriter
            pipeline_writer = FakePipelineWriter()

    # semantic-service reader (dashboard-designer grounding: governed measures +
    # dimensions of the workspace's published semantic models)
    if semantic_reader is None:
        if real:
            from app.adapters.semantic import SemanticLayerClient
            semantic_reader = SemanticLayerClient(settings.semantic_service_url)
        else:
            from app.adapters.fakes import FakeSemanticReader
            semantic_reader = FakeSemanticReader()

    # chart-service reader (dashboard-designer grounding: the governed chart-type
    # catalog the designer picks chart types from)
    if catalog_reader is None:
        if real:
            from app.adapters.chartcatalog import ChartCatalogClient
            catalog_reader = ChartCatalogClient(settings.chart_service_url)
        else:
            from app.adapters.fakes import FakeChartCatalog
            catalog_reader = FakeChartCatalog()

    # OPA authz
    if authz is None:
        if real:
            from app.adapters.authz import OpaAuthz
            authz = OpaAuthz(settings.opa_url, redis_url=settings.redis_url,
                             package=settings.opa_package)
        else:
            from app.adapters.authz import AllowAllAuthz
            authz = AllowAllAuthz()

    # kill registry
    if kill_registry is None:
        if real:
            from app.adapters.killswitch import RedisKillRegistry
            kill_registry = RedisKillRegistry(settings.redis_url)
        else:
            from app.adapters.killswitch import InMemoryKillRegistry
            kill_registry = InMemoryKillRegistry()

    # incoming-token verifier (JWKS or static PEM)
    token_verifier = _build_verifier(settings)

    # SLM distillation milestone 1: transcript sink (consent-gated capture).
    from app.domain.transcripts import TranscriptSink
    transcripts = TranscriptSink(store, enabled=settings.slm_transcript_capture)

    proposal_service = ProposalService(
        store=store, authz=authz, grant_issuer=grant_issuer, token_minter=token_minter,
        tool_client=tool_client, bus=bus, realtime=realtime, settings=settings,
        transcripts=transcripts)
    run_engine = RunEngine(
        store=store, proposals=proposal_service, bus=bus, realtime=realtime, llm=llm,
        memory=memory, case_reader=case_reader, evidence_reader=evidence_reader,
        ingestion_reader=ingestion_reader,
        experiment_reader=experiment_reader, dataset_reader=dataset_reader,
        pipeline_reader=pipeline_reader, pipeline_writer=pipeline_writer,
        semantic_reader=semantic_reader,
        catalog_reader=catalog_reader, settings=settings, transcripts=transcripts,
        kill_registry=kill_registry,
        kill_poll_interval_s=settings.kill_poll_interval_s)

    from app.adapters.trainer import build_trainer

    return Container(
        settings=settings, signing_key=signing_key, grant_issuer=grant_issuer,
        token_minter=token_minter, store=store, bus=bus, realtime=realtime, llm=llm,
        memory=memory, case_reader=case_reader, evidence_reader=evidence_reader,
        ingestion_reader=ingestion_reader,
        experiment_reader=experiment_reader, dataset_reader=dataset_reader,
        pipeline_reader=pipeline_reader, pipeline_writer=pipeline_writer,
        semantic_reader=semantic_reader,
        catalog_reader=catalog_reader, tool_client=tool_client, authz=authz,
        kill_registry=kill_registry, token_verifier=token_verifier,
        proposal_service=proposal_service, run_engine=run_engine,
        transcripts=transcripts, session_proj=session_proj,
        trainer=build_trainer(settings.slm_trainer_backend),
        eval_gate=eval_gate,
        extras={"mode": mode, "engines": engines,
                # exposed for the outbox relay (app.main lifespan)
                "session_factory": session_factory})


def _build_verifier(settings: Settings):
    from windrose_common.authjwt import JwksCache, JwtVerifier
    if settings.jwt_public_key_pem:
        return JwtVerifier(issuer=settings.jwt_issuer, audience=settings.jwt_audience,
                           public_key_pem=settings.jwt_public_key_pem)
    jwks = JwksCache(settings.jwks_url, ttl_seconds=settings.jwks_ttl_seconds)
    return JwtVerifier(issuer=settings.jwt_issuer, audience=settings.jwt_audience, jwks=jwks)
