/** Per-request factory: builds one real HTTP client per downstream service,
 * all sharing the caller's forwarded JWT + trace headers. */
import type { Config } from "../config.js";
import { ServiceClient, type ClientRequestContext, type FetchImpl } from "./base.js";
import { IdentityClient } from "./identity.js";
import { DatasetClient } from "./dataset.js";
import { CaseClient } from "./case.js";
import { ChartClient } from "./chart.js";
import { UsageClient } from "./usage.js";
import { ExperimentClient } from "./experiment.js";
import { InferenceClient } from "./inference.js";
import { AgentClient } from "./agent.js";
import { RbacClient } from "./rbac.js";
import { IngestionClient } from "./ingestion.js";
import { QueryClient } from "./query.js";
import { PipelinesClient } from "./pipelines.js";
import { SemanticClient } from "./semantic.js";
import { AuditClient } from "./audit.js";
import { NotificationClient } from "./notification.js";
import { ToolPlaneClient } from "./toolplane.js";
import { MemoryClient } from "./memory.js";
import { EvalClient } from "./eval.js";
import { AiGatewayClient } from "./aigateway.js";

export interface Clients {
  identity: IdentityClient;
  dataset: DatasetClient;
  case: CaseClient;
  chart: ChartClient;
  usage: UsageClient;
  experiment: ExperimentClient;
  inference: InferenceClient;
  agent: AgentClient;
  rbac: RbacClient;
  ingestion: IngestionClient;
  query: QueryClient;
  pipelines: PipelinesClient;
  semantic: SemanticClient;
  audit: AuditClient;
  notification: NotificationClient;
  toolPlane: ToolPlaneClient;
  memory: MemoryClient;
  /** eval-service (Tier 2a: eval suites/runs/gates/canaries/trends). */
  eval: EvalClient;
  /** ai-gateway admin plane (Tier 2a: providers/ladders/budgets/keys/guardrails). */
  aiGateway: AiGatewayClient;
}

function svc(service: string, baseUrl: string, ctx: ClientRequestContext, cfg: Config, fetchImpl?: FetchImpl) {
  return new ServiceClient({ service, baseUrl, ctx, fetchImpl, timeoutMs: cfg.downstreamTimeoutMs });
}

export function buildClients(cfg: Config, ctx: ClientRequestContext, fetchImpl?: FetchImpl): Clients {
  return {
    identity: new IdentityClient(svc("identity-service", cfg.services.identity, ctx, cfg, fetchImpl)),
    dataset: new DatasetClient(svc("dataset-service", cfg.services.dataset, ctx, cfg, fetchImpl)),
    case: new CaseClient(svc("case-service", cfg.services.case, ctx, cfg, fetchImpl)),
    chart: new ChartClient(svc("chart-service", cfg.services.chart, ctx, cfg, fetchImpl)),
    usage: new UsageClient(svc("usage-service", cfg.services.usage, ctx, cfg, fetchImpl)),
    experiment: new ExperimentClient(svc("experiment-service", cfg.services.experiment, ctx, cfg, fetchImpl)),
    inference: new InferenceClient(svc("inference-service", cfg.services.inference, ctx, cfg, fetchImpl)),
    agent: new AgentClient(svc("agent-runtime", cfg.services.agentRuntime, ctx, cfg, fetchImpl)),
    rbac: new RbacClient(svc("rbac-service", cfg.services.rbac, ctx, cfg, fetchImpl)),
    ingestion: new IngestionClient(svc("ingestion-service", cfg.services.ingestion, ctx, cfg, fetchImpl)),
    query: new QueryClient(svc("query-service", cfg.services.query, ctx, cfg, fetchImpl)),
    pipelines: new PipelinesClient(svc("pipeline-orchestrator", cfg.services.pipeline, ctx, cfg, fetchImpl)),
    semantic: new SemanticClient(svc("semantic-service", cfg.services.semantic, ctx, cfg, fetchImpl)),
    audit: new AuditClient(svc("audit-service", cfg.services.audit, ctx, cfg, fetchImpl)),
    notification: new NotificationClient(svc("notification-service", cfg.services.notification, ctx, cfg, fetchImpl)),
    toolPlane: new ToolPlaneClient(svc("tool-plane", cfg.services.toolPlane, ctx, cfg, fetchImpl)),
    memory: new MemoryClient(svc("memory-service", cfg.services.memory, ctx, cfg, fetchImpl)),
    // ---- Tier 2a additions (eval-service + ai-gateway admin) ----------------
    eval: new EvalClient(svc("eval-service", cfg.services.eval, ctx, cfg, fetchImpl)),
    aiGateway: new AiGatewayClient(svc("ai-gateway", cfg.services.aiGateway, ctx, cfg, fetchImpl)),
  };
}
