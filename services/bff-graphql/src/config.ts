/**
 * Runtime configuration for bff-graphql.
 *
 * The BFF holds NO service credentials. The only things it is configured with
 * are: the base URLs of the downstream domain services it aggregates, the
 * identity-service JWKS URL used to verify inbound user JWTs at the edge
 * (fail-fast only — never for authz), the realtime-hub URL it hands back inside
 * StreamHandle descriptors, and the static query-limit knobs from BRD §BFF-FR-04x.
 */

export type Mode = "development" | "production" | "test";

export interface ServiceUrls {
  identity: string;
  dataset: string;
  case: string;
  chart: string;
  usage: string;
  experiment: string;
  inference: string;
  agentRuntime: string;
  rbac: string;
  ingestion: string;
  query: string;
  pipeline: string;
  semantic: string;
  audit: string;
  notification: string;
  toolPlane: string;
  memory: string;
  /** eval-service (eval suites/runs/gates/canaries/trends). Default matches the
   * local harness port (deploy/e2e/config.env PORT_EVAL=8324). */
  eval: string;
  /** ai-gateway admin plane (providers/ladders/budgets/keys/guardrails).
   * Default matches the local harness port (deploy/e2e/config.env PORT_AIGW=8312). */
  aiGateway: string;
  /** pack-service (BRD 23: capability-pack catalog + governed install lifecycle).
   * Default matches the local harness port (deploy/e2e/config.env PORT_PACK=8309). */
  pack: string;
}

export interface Limits {
  /** Max nesting depth per operation (BFF-FR-041: 10). */
  maxDepth: number;
  /** Max aliases per operation (BFF-FR-041: 20). */
  maxAliases: number;
  /** Max root fields per operation (BFF-FR-041: 5). */
  maxRootFields: number;
  /** Max cost points; list fields cost first x child-cost (BFF-FR-041: 5000). */
  maxCost: number;
  /** Max `first` on any connection, mirrors REST limit cap (MASTER-FR-022: 200). */
  maxPageSize: number;
  /** Default page size when `first` omitted (MASTER-FR-022: 50). */
  defaultPageSize: number;
}

export interface Config {
  mode: Mode;
  port: number;
  services: ServiceUrls;
  /** identity-service JWKS document URL (RS256 public keys). */
  jwksUrl: string;
  /** Expected token issuer / audience (validated at the edge, fail-fast only). */
  jwtIssuer?: string;
  jwtAudience?: string;
  /** When true the JWT signature is verified at the edge; still forwarded verbatim. */
  verifyJwt: boolean;
  /** realtime-hub base URL surfaced inside StreamHandle fields (BFF-FR-060). */
  realtimeHubUrl: string;
  /** Production accepts only persisted operations (BFF-FR-040). */
  persistedQueriesOnly: boolean;
  /** Introspection is disabled in production (BFF-FR-041). */
  introspection: boolean;
  /** Per-downstream request timeout in ms (BFF-FR-032 / BR-4). */
  downstreamTimeoutMs: number;
  /** Origins allowed to call /graphql cross-origin (BRD 58 SEC-3). Always
   * includes ui-web's own dev origin as a floor; prod deployments add their
   * real ui-web origin(s) via CORS_ALLOWED_ORIGINS. Never '*' -- the BFF holds
   * no cookies itself, but an open allowlist would let any page drive a
   * signed-in user's browser into calling the API with their bearer token. */
  corsAllowedOrigins: string[];
  limits: Limits;
}

function env(name: string, fallback?: string): string | undefined {
  const v = process.env[name];
  return v === undefined || v === "" ? fallback : v;
}

function bool(name: string, fallback: boolean): boolean {
  const v = process.env[name];
  if (v === undefined || v === "") return fallback;
  return v === "1" || v.toLowerCase() === "true";
}

export function loadConfig(overrides: Partial<Config> = {}): Config {
  const mode = (env("NODE_ENV", "development") as Mode) ?? "development";
  const isProd = mode === "production";

  const services: ServiceUrls = {
    identity: env("IDENTITY_URL", "http://localhost:9001")!,
    dataset: env("DATASET_URL", "http://localhost:9004")!,
    case: env("CASE_URL", "http://localhost:9008")!,
    chart: env("CHART_URL", "http://localhost:9007")!,
    usage: env("USAGE_URL", "http://localhost:9017")!,
    experiment: env("EXPERIMENT_URL", "http://localhost:9010")!,
    // inference-service (batch scoring jobs). Default matches the 90xx dev scheme;
    // overridden by INFERENCE_URL (deploy/e2e/config.env PORT_INFERENCE) in the harness.
    inference: env("INFERENCE_URL", "http://localhost:9016")!,
    agentRuntime: env("AGENT_RUNTIME_URL", "http://localhost:9014")!,
    rbac: env("RBAC_URL", "http://localhost:8302")!,
    ingestion: env("INGESTION_URL", "http://localhost:8083")!,
    // query-service (saved queries + ad-hoc SQL execution). Default matches the
    // local harness port (deploy/e2e/config.env PORT_QUERY=8085).
    query: env("QUERY_URL", "http://localhost:8085")!,
    pipeline: env("PIPELINE_URL", "http://localhost:8313")!,
    semantic: env("SEMANTIC_URL", "http://localhost:8086")!,
    // audit-service (WORM compliance trail). Default matches the local harness
    // port (deploy/e2e/config.env PORT_AUDIT); overridden by AUDIT_URL in prod.
    audit: env("AUDIT_URL", "http://localhost:8322")!,
    // notification-service (in-app/email/webhook fan-out + scheduled dashboard
    // report subscriptions, NOTIF-FR-060). Default matches the local harness
    // port (deploy/e2e/config.env PORT_NOTIFICATION).
    notification: env("NOTIFICATION_URL", "http://localhost:8323")!,
    // tool-plane tool-registry (admin plane: kill switches, TPL-FR-052). Default
    // matches the local harness port (deploy/e2e/config.env PORT_TOOLREG=8310);
    // overridden by TOOL_REGISTRY_URL in prod. NOT the mcp-gateway data-plane URL.
    toolPlane: env("TOOL_REGISTRY_URL", "http://localhost:9011")!,
    // memory-service (agent long-term memory: browse/erasure/stats). Default
    // matches the local harness port (deploy/e2e/config.env PORT_MEMORY=8307);
    // overridden by MEMORY_URL in prod.
    memory: env("MEMORY_URL", "http://localhost:9013")!,
    // eval-service (eval flywheel: suites/runs/gates/canaries/trends). Default
    // matches the local harness port (deploy/e2e/config.env PORT_EVAL=8324).
    eval: env("EVAL_URL", "http://localhost:8324")!,
    pack: env("PACK_URL", "http://localhost:8309")!,
    // ai-gateway (LLM gateway admin plane). Default matches the local harness
    // port (deploy/e2e/config.env PORT_AIGW=8312).
    aiGateway: env("AI_GATEWAY_URL", "http://localhost:8312")!,
  };

  const base: Config = {
    mode,
    port: Number(env("PORT", "4000")),
    services,
    jwksUrl: env("JWKS_URL", `${services.identity}/.well-known/jwks.json`)!,
    jwtIssuer: env("JWT_ISSUER"),
    jwtAudience: env("JWT_AUDIENCE"),
    verifyJwt: bool("VERIFY_JWT", true),
    realtimeHubUrl: env("REALTIME_HUB_URL", "http://localhost:9020")!,
    persistedQueriesOnly: bool("PERSISTED_QUERIES_ONLY", isProd),
    introspection: bool("INTROSPECTION", !isProd),
    downstreamTimeoutMs: Number(env("DOWNSTREAM_TIMEOUT_MS", "10000")),
    corsAllowedOrigins: (env("CORS_ALLOWED_ORIGINS", "http://localhost:3000") ?? "")
      .split(",")
      .map((o) => o.trim())
      .filter(Boolean),
    limits: {
      maxDepth: Number(env("MAX_DEPTH", "10")),
      maxAliases: Number(env("MAX_ALIASES", "20")),
      maxRootFields: Number(env("MAX_ROOT_FIELDS", "5")),
      maxCost: Number(env("MAX_COST", "5000")),
      maxPageSize: 200,
      defaultPageSize: 50,
    },
  };

  return { ...base, ...overrides };
}
