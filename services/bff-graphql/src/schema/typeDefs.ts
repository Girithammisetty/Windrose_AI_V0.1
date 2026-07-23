/**
 * Datacern BFF GraphQL SDL.
 *
 * UI-shaped aggregation over the domain services' REST APIs. Every type notes
 * the downstream service that backs it (BR-12: the schema is self-documenting).
 * No business logic and no authz live here — resolvers forward the user's JWT
 * and reshape the REST responses (BFF-FR-003, BFF-FR-011).
 */
import gql from "graphql-tag";

export const typeDefs = gql`
  """UUIDv7 time-ordered identifier exposed as an opaque string (MASTER-FR-021)."""
  scalar DateTime
  scalar Date
  """Arbitrary JSON payload passed through from a downstream service verbatim."""
  scalar JSON

  """Anything addressable by a domain UUID and a URN (MASTER-FR-013 / BFF-FR-022)."""
  interface Node {
    id: ID!
    urn: String!
  }

  """Cursor page metadata mirroring the REST envelope page{next_cursor,has_more}."""
  type PageInfo {
    nextCursor: String
    hasMore: Boolean!
  }

  """
  A pointer at realtime-hub (BFF-FR-060). The BFF never proxies SSE/WebSocket;
  the client connects to hubUrl with its own JWT and subscribes to topics.
  """
  type StreamHandle {
    hubUrl: String!
    topics: [String!]!
  }

  """A long-running downstream operation; progress is consumed from realtime-hub."""
  type Operation {
    id: ID!
    status: OperationStatus!
  }

  enum OperationStatus { PENDING RUNNING SUCCEEDED FAILED }
  enum RunStatus { QUEUED RUNNING SUCCEEDED FAILED CANCELLED }
  enum CaseStatus { DRAFT UNASSIGNED IN_PROGRESS RESOLVED CLOSED }
  enum Severity { LOW MEDIUM HIGH CRITICAL }
  enum ProposalStatus { PENDING APPROVED REJECTED EDITED_APPROVED RESPONDED EXPIRED }
  enum DecisionKind { APPROVE REJECT EDIT_ARGS RESPOND }

  # ============================ platform (identity-service, usage-service) ====
  """The authenticated caller, derived from the forwarded JWT claims (identity-service)."""
  type Viewer {
    userId: ID!
    tenantId: ID!
    type: String!
    scopes: [String!]!
    "First-class cross-tenant platform operator (distinct from the per-tenant Admin role)."
    isPlatformAdmin: Boolean!
    """
    The caller's role display names, read from rbac-service's permissions_flat
    projection (rbac GET /me/capabilities, JWT forwarded). Display-only data for
    the UI capability gate — NOT an authz decision (the services enforce).
    """
    roles: [String!]!
    """
    The caller's allowed action names (e.g. "case.case.read"), read from the
    same rbac projection. "*" means "all actions" (tenant admin). The UI hides
    anything whose capability is absent (fail-safe); the services still enforce.
    """
    capabilities: [String!]!
    """
    True when the rbac capability lookup FAILED and roles/capabilities above are
    the fail-closed empty fallback rather than the caller's real grants. The UI
    keeps the fail-closed nav but shows a "permissions unavailable" notice
    instead of presenting the outage as "you have no access".
    """
    capsDegraded: Boolean!
    """Display name of the caller's tenant (identity GET /tenants/self;
    member-visible, null if the lookup fails)."""
    tenantName: String
    tenantDisplayName: String
    """The workspace the token is scoped to + its display name (rbac
    /me/capabilities workspace_name; null when unresolvable)."""
    workspaceId: ID
    workspaceName: String
    """Per-tenant UI label overrides (identity GET /tenants/self/labels; BRD 23
    inc3). The app overlays these onto its base i18n catalog so a capability
    pack can white-label the vertical (e.g. "Cases" -> "AP Exceptions"). Empty
    when none are set or the lookup fails (the base catalog is used)."""
    displayLabels: [LabelOverride!]!
    """The caller's tenant's white-label brand (BRD 59 WS3): color tokens the
    app shell + embed surfaces apply as CSS custom properties, and whether a
    logo has been uploaded (fetched separately via /api/tenant-branding/logo,
    not through GraphQL). Never null — an unconfigured tenant gets the
    all-empty shape so the app shell always has something to render."""
    branding: TenantBranding!
  }

  """A tenant's white-label identity (BRD 59 WS3). primaryColor/accentColor are
  bare HSL triplets ("221 83% 53%") applied directly as CSS custom properties."""
  type TenantBranding {
    configured: Boolean!
    hasLogo: Boolean!
    primaryColor: String!
    accentColor: String!
    updatedAt: DateTime
  }

  input SetTenantBrandingInput {
    primaryColor: String!
    accentColor: String!
  }

  """One per-tenant UI label override: an i18n key and the string to show for
  it (BRD 23 inc3)."""
  type LabelOverride {
    key: String!
    value: String!
  }

  """A directory user (identity-service GET /users/{id})."""
  type User implements Node {
    id: ID!
    urn: String!
    email: String!
    fullName: String
    """Lifecycle: invited | active | deactivated (identity-service)."""
    status: String
    """Last successful sign-in (identity-service last_login_at; null if never)."""
    lastLoginAt: DateTime
    createdAt: DateTime
  }

  type UserConnection { nodes: [User!]! pageInfo: PageInfo! }

  input InviteUserInput {
    email: String!
    fullName: String
    """Optional group names to seed (accepted by identity but not yet wired to Keycloak)."""
    groups: [String!]
  }

  # ============================ admin: rbac (rbac-service) =====================
  """
  A workspace / content boundary (rbac-service GET /workspaces/{id}). Archived
  state is expressed by \`archived\` (rbac has no status field — archivedAt is the
  source of truth).
  """
  type Workspace implements Node {
    id: ID!
    urn: String!
    name: String!
    description: String
    public: Boolean
    archived: Boolean!
    archivedAt: DateTime
    createdBy: String
    createdAt: DateTime
    updatedAt: DateTime
  }

  type WorkspaceConnection { nodes: [Workspace!]! pageInfo: PageInfo! }

  input CreateWorkspaceInput {
    name: String!
    description: String
    public: Boolean
  }

  """A permission or content group (rbac-service GET /groups/{id})."""
  type Group implements Node {
    id: ID!
    urn: String!
    name: String!
    description: String
    """permission | content (rbac serializes this as group_type)."""
    groupType: String
    system: Boolean
    autoGenerated: Boolean
    createdAt: DateTime
    updatedAt: DateTime
  }

  type GroupConnection { nodes: [Group!]! pageInfo: PageInfo! }

  """A group membership edge (rbac-service GET /groups/{id}/members)."""
  type GroupMember {
    userId: ID!
    expiresAt: DateTime
    createdAt: DateTime
  }

  """Create a Team (rbac-service POST /groups, group_type fixed to permission)."""
  input CreateTeamInput {
    name: String!
    description: String
  }

  """Update a Team's name/description (rbac-service PATCH /groups/{id})."""
  input UpdateTeamInput {
    name: String
    description: String
  }

  """A tenant or system role (rbac-service GET /roles). Bindable to a permission
  group (Team) via assignTeamRole; a group's currently bound roles are readable
  via the groupRoles query."""
  type Role {
    id: ID!
    name: String!
    system: Boolean
    version: Int
    actions: [String!]!
    createdAt: DateTime
    updatedAt: DateTime
  }

  type RoleConnection { nodes: [Role!]! pageInfo: PageInfo! }

  """One step in an authz decision trace (rbac ChainStep) — a rule the engine
  walked while deciding a subject+action(+resource) tuple. \`type\` is one of
  membership | role | workspace_assignment | grant | flag | scope_excluded."""
  type AuthzChainStep {
    type: String!
    group: String
    groupType: String
    role: String
    action: String
    workspaceScoped: Boolean
    viaGroup: String
    workspace: String
    level: String
    subject: String
    admin: Boolean
    detail: String
  }

  """The real decision trace for a subject+action(+resource) tuple (rbac-
  service POST /authz/explain) — a debug tool, not a general query. Tenant is
  always the CALLER's own verified token tenant (never accepted as input)."""
  type AuthzExplanation {
    allowed: Boolean!
    reason: String!
    chain: [AuthzChainStep!]!
  }

  input ExplainAuthzInput {
    userId: String!
    typ: String
    scopes: [String!]
    action: String!
    resourceUrn: String
    workspaceId: String
  }

  # ============== Tier 4b: identity/rbac admin (types + inputs) ===============
  """Edit a workspace's name/description/public flag (rbac-service PATCH
  /workspaces/{id}). Absent fields stay unchanged."""
  input UpdateWorkspaceInput {
    name: String
    description: String
    public: Boolean
  }

  """rbac's two-kind group model: PERMISSION groups carry roles; CONTENT groups
  carry workspace/data access. Lowercased to the wire value (group_type)."""
  enum GroupType { PERMISSION CONTENT }

  """Create any group (rbac-service POST /groups). The general path — content
  groups in particular. Teams (permission groups) keep their dedicated
  createTeam mutation."""
  input CreateGroupInput {
    name: String!
    description: String
    groupType: GroupType!
  }

  """Editable fields of a group (rbac-service PATCH /groups/{id}) — name and
  description only; groupType is fixed at creation. Only provided fields change."""
  input UpdateGroupInput {
    id: ID!
    name: String
    description: String
  }

  """One bulk membership operation (rbac store.BulkMemberOp)."""
  enum GroupMemberOp { ADD REMOVE }

  input GroupMemberOpInput {
    op: GroupMemberOp!
    userId: ID!
  }

  """Per-entry outcome of a bulk membership call (rbac store.BulkMemberResult).
  \`code\` carries the downstream failure code when ok is false (e.g. NOT_FOUND)."""
  type GroupMemberOpResult {
    userId: ID!
    op: String!
    ok: Boolean!
    code: String
  }

  """The real partial-failure report of POST /groups/{id}/members:bulk —
  one entry per requested op; never a blind success."""
  type BulkGroupMembershipResult {
    results: [GroupMemberOpResult!]!
    succeeded: Int!
    failed: Int!
  }

  """Create a custom role (rbac-service POST /roles)."""
  input CreateRoleInput {
    name: String!
    actions: [String!]!
  }

  """
  Edit a custom role (rbac-service PATCH /roles/{id}). Both fields are optional
  — omit one to leave it unchanged; supply both to rename and recompose the
  action set in a single atomic update. At least one must be provided.
  """
  input UpdateRoleInput {
    name: String
    actions: [String!]
  }

  """
  A created/rotated service account WITH its api_key (identity-service POST
  /service-accounts, POST /service-accounts/{id}/rotate). The apiKey
  (wr_sa_<id>.<secret>) is returned EXACTLY ONCE here and is never retrievable
  again — the UI must surface it immediately and never persist it.
  """
  type CreatedServiceAccount {
    serviceAccount: ServiceAccount!
    apiKey: String!
  }

  input CreateServiceAccountInput {
    name: String!
    scopes: [String!]
    expiresAt: DateTime
  }

  """
  One row of effective access to a resource (rbac-service GET
  /grants?resource_urn=): direct grants, the creator's implicit grant, and
  group grants expanded to member users. \`provenance\` is direct |
  implicit_creator | via_group; \`via\` names the group for via_group rows;
  \`grantId\` is the underlying grant (delete target — only meaningful for
  provenance direct).
  """
  type EffectiveAccessEntry {
    subjectType: String!
    subjectId: ID!
    level: String!
    provenance: String!
    via: String
    grantId: ID!
    workspaceId: ID!
  }

  """A content grant row (rbac-service POST /grants response). \`implicit\` marks
  the creator's automatic owner grant."""
  type ContentGrant {
    id: ID!
    workspaceId: ID!
    resourceUrn: String!
    subjectType: String!
    subjectId: ID!
    level: String!
    implicit: Boolean!
    createdAt: DateTime
  }

  """Create a content grant (rbac-service POST /grants — the body nests the
  subject). \`subjectType\` is user | group; \`level\` is viewer | editor | owner."""
  input CreateContentGrantInput {
    workspaceId: ID!
    resourceUrn: String!
    subjectType: String!
    subjectId: ID!
    level: String!
  }

  # ============================ admin: identity (identity-service) =============
  """
  A machine principal (identity-service GET /service-accounts). Secret material is
  never returned on reads (json:"-" in the domain model); only metadata is exposed.
  """
  type ServiceAccount implements Node {
    id: ID!
    urn: String!
    name: String!
    scopes: [String!]!
    expiresAt: DateTime
    lastUsedAt: DateTime
    revokedAt: DateTime
    createdAt: DateTime
    updatedAt: DateTime
  }

  type ServiceAccountConnection { nodes: [ServiceAccount!]! pageInfo: PageInfo! }

  """Tenant compute quotas (identity-service domain.Quotas)."""
  type TenantQuotas {
    cpu: Int
    memory: String
    processingCpu: Int
    processingMemory: String
  }

  """
  A tenant + its settings (identity-service GET /tenants/{id}). Settings live on
  the tenant object itself; there is no separate settings resource.
  """
  type Tenant implements Node {
    id: ID!
    urn: String!
    name: String!
    displayName: String
    ownerEmail: String
    """Isolation tier: pool | bridge | silo."""
    tier: String
    cloud: String
    """draft | provisioning | active | suspended | deleting | deleted."""
    status: String
    subdomain: String
    platformVersion: String
    autoUpgrade: Boolean
    modules: [String!]!
    quotas: TenantQuotas
    createdAt: DateTime
    updatedAt: DateTime
    """Embedded-UI configuration (identity-service GET /tenants/{id}/embed-config).
    Never carries the secret itself — only whether one has been generated."""
    embedConfig: EmbedConfig
  }

  """A tenant's embedded-UI (iframe) configuration. The secret itself is never
  readable after generation — only its presence (configured) is exposed."""
  type EmbedConfig {
    configured: Boolean!
    allowedOrigins: [String!]!
    updatedAt: DateTime
  }

  """PUT /tenants/{id}/embed-config response — embedSecret is shown exactly
  once, at generation/rotation time; store it immediately, it cannot be
  retrieved again."""
  type SetEmbedConfigResult {
    embedSecret: String!
    allowedOrigins: [String!]!
  }

  """A tenant's own OIDC identity provider (BYO-P4). When set + enabled, an ID
  token whose issuer matches routes to THIS tenant at login. configured is
  false when the tenant has never set up SSO."""
  type TenantIdpConfig {
    configured: Boolean!
    issuer: String
    clientId: String
    discoveryUrl: String
    enabled: Boolean!
    updatedAt: DateTime
  }
  input SetTenantIdpInput {
    issuer: String!
    clientId: String
    discoveryUrl: String
    enabled: Boolean
  }

  # ============================ admin: audit (audit-service) ===================
  """
  A WORM audit-trail event (audit-service GET /audit/search). The actor and
  optional via-agent are flattened from the nested REST objects. \`payload\` is
  null when withheld (bodyWithheld true).
  """
  type AuditEvent {
    eventId: ID!
    urn: String!
    eventType: String!
    tenantId: ID
    actorType: String
    actorId: String
    viaAgentId: String
    viaAgentVersion: String
    action: String
    resourceUrn: String
    occurredAt: DateTime!
    ingestedAt: DateTime
    traceId: String
    payloadDigest: String
    bodyWithheld: Boolean
    payload: JSON
    chainSeq: Int
    chainHash: String
  }

  type AuditEventConnection { nodes: [AuditEvent!]! pageInfo: PageInfo! }

  enum SiemExportFormat { CEF LEEF JSON }

  """One proposed/decided state of a tenant's SIEM export destination (BRD 59
  WS2, four-eyes gated). Every propose/approve/reject creates or transitions
  one row rather than mutating a config in place, so approvedBy/rejectedBy
  preserve who took each decision."""
  type SiemConfig {
    id: ID!
    endpoint: String!
    format: SiemExportFormat!
    active: Boolean!
    status: String!
    requestedBy: String!
    approvedBy: String
    rejectedBy: String
    rejectReason: String
    createdAt: DateTime!
    updatedAt: DateTime!
  }

  """The tenant's SIEM export state: the live destination (if any), a
  proposal awaiting a second approver (if any), and full decision history."""
  type SiemConfigState {
    active: SiemConfig
    pending: SiemConfig
    history: [SiemConfig!]!
  }

  input ProposeSiemConfigInput {
    endpoint: String!
    format: SiemExportFormat!
    authRef: String
  }

  """The real result of a chain-integrity verification for one tenant-day
  (audit-service POST /audit/verify). \`sealed=false\` never occurs from this
  route — an unsealed day 409s instead of returning a fake pass/fail."""
  type ChainVerifyResult {
    valid: Boolean!
    eventsChecked: Int!
    chainHead: String!
    manifestMatch: Boolean!
    firstMismatchSeq: Int
    sealed: Boolean!
  }

  """An async compliance-pack build job (audit-service POST /compliance/soc2-
  pack | /compliance/ai-decision-log, 202 + poll GET /operations/{id}).
  \`status\`: running | succeeded | failed. \`resultUrl\` is a presigned
  download link, only present once succeeded."""
  type ComplianceJob {
    operationId: ID!
    status: String!
    resultUrl: String
    error: String
  }

  """BRD 60 WS5 — the four-eyes decision summary at the heart of an evidence
  pack. \`fourEyes\` is proven from the events below: a DISTINCT human approver
  (\`approver\` != \`onBehalfOf\`)."""
  type EvidenceDecision {
    agentId: String!
    agentVersion: String!
    onBehalfOf: String!
    approver: String!
    outcome: String!
    fourEyes: Boolean!
    proposedAt: String!
    decidedAt: String!
    toolId: String!
    toolVersion: String!
    argsDigest: String!
    affectedUrns: [String!]!
  }

  """One WORM event with its immutable hash-chain position."""
  type EvidenceEvent {
    eventId: ID!
    eventType: String!
    resourceUrn: String!
    actorType: String!
    actorId: String!
    viaAgentId: String
    oboUserId: String
    occurredAt: String!
    payloadDigest: String!
    chainDate: String!
    chainSeq: Int!
    chainHash: String!
  }

  """Tamper-evidence for one chain-day: the hash chain re-verified against its
  sealed WORM (Object-Lock) manifest. An unsealed day reports \`sealed:false\`
  with a note — never a faked verification."""
  type EvidenceDayProof {
    chainDate: String!
    sealed: Boolean!
    valid: Boolean!
    manifestMatch: Boolean!
    eventsChecked: Int!
    manifestUri: String
    manifestSha256: String
    note: String
  }

  """The auditor evidence pack for one governed decision (audit-service
  POST /compliance/evidence-pack)."""
  type EvidencePack {
    kind: String!
    tenantId: String!
    proposalId: ID!
    proposalUrn: String!
    generatedAt: String!
    decision: EvidenceDecision!
    events: [EvidenceEvent!]!
    chainProof: [EvidenceDayProof!]!
    integrity: String!
  }

  # ============================ data (dataset-service) ========================
  """A dataset in the catalog (dataset-service GET /datasets/{id})."""
  type Dataset implements Node {
    id: ID!
    urn: String!
    name: String!
    workspaceId: String
    description: String
    status: String
    tags: [String!]!
    rowCount: Int
    createdAt: DateTime
    """dataset-service has no status="archived" value — presence of deleted_at IS
    the archive marker (archiveDataset / restoreDataset)."""
    archived: Boolean!
    archivedAt: DateTime
    """Profile summary (dataset-service GET /datasets/{id}/profile), loader-batched."""
    profile: Profile
  }

  """Dataset profiling summary (dataset-service)."""
  type Profile {
    rowCount: Int
    columnCount: Int
    fullJsonUrl: String
    htmlReportUrl: String
  }

  type DatasetConnection { nodes: [Dataset!]! pageInfo: PageInfo! }

  input DatasetFilter { status: String tags: String }

  """Fields editable on an existing dataset (updateDataset). Both optional — omit
  a field to leave it unchanged."""
  input UpdateDatasetInput { name: String description: String }

  """A node in a dataset's lineage graph (dataset-service GET /lineage)."""
  type LineageNode {
    urn: String!
    """Enriched class for owned URNs (dataset|version|...) or "foreign"."""
    kind: String
    name: String
    status: String
  }

  """A directed lineage edge (from_urn --activity--> to_urn)."""
  type LineageEdge {
    fromUrn: String!
    toUrn: String!
    activity: String
    occurredAt: DateTime
  }

  """A dataset's upstream/downstream URN lineage graph (dataset-service GET /lineage)."""
  type DatasetLineage {
    nodes: [LineageNode!]!
    edges: [LineageEdge!]!
    """True when unexplored edges remain past the requested depth."""
    truncated: Boolean
  }

  """
  Who reads this dataset (dataset-service GET /datasets/{id}/consumers): a
  depth-3 downstream lineage rollup counted by consuming service and activity.
  """
  type DatasetConsumers {
    downstreamEdges: Int!
    """{service: count} for every downstream node, e.g. {"query": 3}."""
    byService: JSON!
    """{activity: count} over downstream edges, e.g. {"executed": 5}."""
    byActivity: JSON!
    truncated: Boolean
  }

  """A similarity-search hit (dataset-service POST /datasets:similar), ranked."""
  type SimilarDataset {
    id: ID
    urn: String
    name: String
    score: Float
  }

  """One immutable dataset version (dataset-service GET /datasets/{id}/versions)."""
  type DatasetVersion {
    id: ID!
    urn: String
    versionNo: Int!
    """Iceberg snapshot backing this version."""
    icebergSnapshotId: String
    """Authoritative column map ({col: {type, nullable, tags}}); may be empty for
    pre-schema-capture versions (see datasetSchema's profile fallback)."""
    schema: JSON
    schemaDiff: JSON
    breakingChange: Boolean
    rowCount: Float
    bytes: Float
    producedByUrn: String
    profileStatus: String
    expired: Boolean
    createdAt: DateTime
  }

  type DatasetVersionConnection { nodes: [DatasetVersion!]! pageInfo: PageInfo! }

  """POST /datasets/{id}/versions/{n}/profile 202 ack — the re-profile job runs
  async; watch profileStatus on the version / the profile panel for completion."""
  type ReprofileResult {
    operationId: ID
    profileId: ID
    status: String
  }

  # ============================ ingestion (ingestion-service) =================
  """
  A single field in a connector type's config form (ingestion-service
  GET /connector-types). Derived from the pydantic config model + SECRET_FIELDS;
  the UI renders the right widget and Zod validation per field.
  """
  type ConnectorField {
    name: String!
    """Widget type: string | integer | number | boolean | enum | object | array."""
    type: String!
    required: Boolean!
    """Write-only credential field (rendered as a password input, Vault-backed)."""
    secret: Boolean!
    default: JSON
    enum: [String!]
    help: String
  }

  """A supported data-source connector type + its dynamic-form schema (ingestion-service)."""
  type ConnectorType {
    connectorType: String!
    displayName: String!
    """Grouping for the picker: database | warehouse | object-store | file | saas."""
    category: String!
    fields: [ConnectorField!]!
    secretFields: [String!]!
    """Raw JSON Schema (MCP get_connection_schema parity)."""
    configSchema: JSON!
  }

  """
  A saved data-source connection (ingestion-service GET /connections/{id}).
  Secrets are write-only: reads never return credential values (secretFields lists
  which secrets are set; secretSet is true when any is stored in Vault).
  """
  type DataConnection implements Node {
    id: ID!
    urn: String!
    name: String!
    connectorType: String!
    config: JSON!
    secretFields: [String!]!
    secretSet: Boolean!
    trafficDirection: String
    tags: [String!]!
    workspaceId: String
    """Result of the last test-connection probe: "ok" | "failed" | null."""
    lastTestStatus: String
    lastTestedAt: DateTime
    createdAt: DateTime
    updatedAt: DateTime
  }

  type DataConnectionList { nodes: [DataConnection!]! pageInfo: PageInfo! }

  """
  A governed, proposal-mode decision write-back to a tenant's own system of
  record (ingestion-service, INS-FR-061) over an \`outgoing\` DataConnection.
  Every job is four-eyes: approvedBy must differ from requestedBy, enforced
  server-side (a self-approve attempt 422s regardless of what the UI allows).
  status: pending_approval | delivering | delivered | failed | rejected.
  """
  type Writeback implements Node {
    id: ID!
    urn: String!
    connectionId: ID!
    workspaceId: String
    decisionKind: String!
    decisionRef: String!
    target: JSON!
    payload: JSON!
    status: String!
    requestedBy: String!
    approvedBy: String
    attempts: Int!
    lastError: String
    targetRef: String
    deliveredAt: DateTime
    createdAt: DateTime
    updatedAt: DateTime
  }

  enum ConnectionTestStatus { OK FAILED }

  """A connection-test probe result (ingestion-service test-connection)."""
  type ConnectionTestResult {
    status: ConnectionTestStatus!
    latencyMs: Int
    """Categorized failure: AUTH_FAILED | SOURCE_UNREACHABLE | TIMEOUT | ... (null on ok)."""
    errorCategory: String
    errorDetail: String
  }

  input CreateConnectionInput {
    name: String!
    type: String!
    config: JSON!
    secrets: JSON
    trafficDirection: String
    tags: [String!]
    workspaceId: String
    """Skip the pre-persist probe (default false: a failed probe aborts create)."""
    skipTest: Boolean
  }

  """target carries executor routing: db_upsert wants {schema, table, key_column},
  http_post wants {path?, method?}. payload is the decision snapshot itself
  (db_upsert: the row incl. the key column; http_post: the JSON body)."""
  input CreateWritebackInput {
    connectionId: ID!
    decisionKind: String!
    decisionRef: String!
    target: JSON
    payload: JSON
    workspaceId: String
  }

  """
  An ingestion run (ingestion-service GET /ingestions/{id}). Lands source data as
  a real dataset version; status advances through created -> running -> succeeded
  live over the ingestion.* realtime-hub topics.
  """
  type Ingestion implements Node {
    id: ID!
    urn: String!
    """file_upload | query | scheduled_run | webhook_batch."""
    mode: String!
    """created | running | succeeded | failed | cancelled."""
    status: String!
    trigger: String
    connectionId: ID
    datasetUrn: String
    fileFormat: String
    statement: String
    rowsAppended: Float
    bytesReceived: Float
    bytesTotal: Float
    attempts: Int
    createdAt: DateTime
    updatedAt: DateTime
  }

  type IngestionConnection { nodes: [Ingestion!]! pageInfo: PageInfo! }

  input CreateIngestionInput {
    """file_upload | query | scheduled_run | webhook_batch."""
    mode: String!
    """Source connection to pull from (required for query/scheduled_run modes)."""
    connectionId: ID
    """The SQL to run against the source (query mode)."""
    statement: String
    fileFormat: String
    """Land into an existing dataset (mutually exclusive with newDatasetName)."""
    datasetUrn: String
    """Create + land into a brand-new dataset (mutually exclusive with datasetUrn)."""
    newDatasetName: String
    newDatasetDescription: String
    skipProfiling: Boolean
    allowEmpty: Boolean
  }

  """A part already confirmed by a resumable upload session."""
  type UploadPart {
    n: Int!
    etag: String!
    size: Int!
  }

  """
  A resumable chunked-upload session (ingestion-service GET/POST /uploads). The
  actual chunk bodies are raw binary and are NEVER sent through this GraphQL
  schema — the browser PUTs each part directly to a ui-web API route
  (/api/uploads/{uploadId}/parts/{n}) that proxies to ingestion-service with the
  caller's session forwarded; only session lifecycle (create/status/complete)
  is JSON and goes through createUpload/upload/completeUpload.
  """
  type Upload {
    uploadId: ID!
    ingestionId: ID!
    """created | completed | aborted | expired (ingestion-service upload status)."""
    status: String
    """Chunk size in bytes the server expects for every part PUT (server-decided;
    may differ from the requested partSize)."""
    partSize: Int
    bytesTotal: Float
    sha256: String
    expiresAt: DateTime
    parts: [UploadPart!]!
  }

  """Create an upload session (ingestion-service POST /uploads). \`ingestionId\`
  must be an existing Ingestion created via createIngestion(mode: "file_upload")."""
  input CreateUploadInput {
    ingestionId: ID!
    partSize: Int
    bytesTotal: Float
  }

  """A confirmed part, echoed back from the ui-web chunk-PUT proxy response, for
  completeUpload's manifest."""
  input UploadPartInput {
    n: Int!
    etag: String!
    size: Int!
  }

  input CompleteUploadInput {
    parts: [UploadPartInput!]!
    sha256: String
  }

  """Edit a saved connection (ingestion-service PATCH /connections/{id}).
  Secrets are WRITE-ONLY and merge: supplied keys overwrite the Vault values,
  omitted keys are preserved — reads only ever return masks. A config/secret
  change live-probes the source unless skipTest."""
  input UpdateConnectionInput {
    name: String
    config: JSON
    secrets: JSON
    trafficDirection: String
    tags: [String!]
    skipTest: Boolean
  }

  """Sample-rows request against a SAVED connection (ingestion-service POST
  /connections/{id}/preview, ING-FR-005). Exactly one of table/path/query."""
  input ConnectionPreviewInput {
    table: String
    path: String
    query: String
    """1..100 (service caps at 100). Rows are never persisted."""
    limit: Int
  }

  """A source preview: ≤100 real rows fetched live from the connected source."""
  type ConnectionPreview {
    columns: [String!]!
    """Row objects keyed by column name."""
    rows: [JSON!]!
  }

  """
  A recurring ingestion schedule (ingestion-service /schedules, ING-FR-060..062).
  Fires its ingestionTemplate as a query-mode ingestion on a cron or fixed
  interval; watermark carries incremental-load state.
  """
  type IngestionSchedule implements Node {
    id: ID!
    urn: String!
    connectionId: ID!
    ingestionTemplate: JSON
    cron: String
    intervalSeconds: Int
    timezone: String
    watermark: JSON
    """skip | buffer_one — what happens when the previous run is still active."""
    overlapPolicy: String
    enabled: Boolean!
    workspaceId: String
    lastFiredAt: DateTime
    nextFireAt: DateTime
    createdAt: DateTime
    updatedAt: DateTime
  }

  type IngestionScheduleConnection { nodes: [IngestionSchedule!]! pageInfo: PageInfo! }

  """Incremental-load watermark spec for a new schedule."""
  input ScheduleWatermarkInput {
    column: String!
    operator: String
    """int | decimal | timestamp | date | string."""
    valueType: String
    initialValue: String!
  }

  """Create a recurring schedule (ingestion-service POST /schedules). Provide
  EXACTLY ONE of cron / intervalSeconds (the service 422s otherwise;
  intervalSeconds minimum 60)."""
  input CreateIngestionScheduleInput {
    connectionId: ID!
    """The ingestion body fired on each tick (statement, dataset_urn or
    new_dataset, skip_profiling, ...)."""
    ingestionTemplate: JSON!
    cron: String
    intervalSeconds: Int
    timezone: String
    watermark: ScheduleWatermarkInput
    overlapPolicy: String
    enabled: Boolean
  }

  input UpdateIngestionScheduleInput {
    cron: String
    intervalSeconds: Int
    timezone: String
    ingestionTemplate: JSON
    overlapPolicy: String
    enabled: Boolean
  }

  """POST /schedules/{id}/run_now outcome: either the fire was skipped by the
  overlap policy, or a real ingestion was created (and possibly already ran)."""
  type ScheduleRunNowResult {
    skipped: Boolean!
    ingestionId: ID
    """True when the overlap policy buffered this fire behind the active run."""
    buffered: Boolean
    """Terminal status when the deployment executes fires inline."""
    status: String
  }

  # ============================ queries (query-service) =======================
  """
  A saved, versioned SQL query (query-service GET /queries/{id}). \`sqlText\`,
  \`variables\` and \`versionNo\` hydrate only on the single-resource path.
  """
  type SavedQuery implements Node {
    id: ID!
    urn: String!
    name: String!
    description: String
    tags: [String!]!
    moduleNames: [String!]!
    sqlText: String
    variables: JSON
    versionNo: Int
    createdBy: String
    createdAt: DateTime
    updatedAt: DateTime
  }

  type SavedQueryConnection { nodes: [SavedQuery!]! pageInfo: PageInfo! }

  """A result column header (query-service GET /executions/{id}/results)."""
  type QueryColumn {
    name: String!
    type: String
  }

  """
  A completed query execution WITH its first page of rows (query-service
  POST /sql/run in sync mode, then GET /executions/{id}/results). Each row is a
  JSON array of scalar cells aligned to \`columns\`.
  """
  type QueryResult {
    executionId: ID!
    """succeeded | failed | ... (sync runs return a terminal status)."""
    status: String!
    engine: String
    cacheHit: Boolean
    durationMs: Int
    resultRows: Int
    scanBytes: Float
    columns: [QueryColumn!]!
    rows: [JSON!]!
    """True when more rows exist past this page."""
    hasMore: Boolean
    warnings: JSON
    error: JSON
  }

  input RunSqlInput {
    sql: String!
    """Row cap for the returned page (query-service caps at 10 000)."""
    limit: Int
    engineHint: String
  }

  """One immutable version of a saved query (query-service GET
  /queries/{id}/versions). Every update creates a new version; numbers never fork."""
  type SavedQueryVersion {
    id: ID!
    versionNo: Int!
    sqlText: String
    """The typed variable declarations of this version (QRY-FR-002)."""
    variables: JSON
    """Save-time-resolved {{dataset('name')}} references."""
    datasetRefs: JSON
    createdBy: String
    createdAt: DateTime
  }

  type SavedQueryVersionConnection { nodes: [SavedQueryVersion!]! pageInfo: PageInfo! }

  """
  A query execution history row (query-service GET /executions). Unlike
  QueryResult this is the persisted record — it carries no rows; fetch results
  separately (or re-run).
  """
  type QueryExecution implements Node {
    id: ID!
    urn: String!
    """queued | running | succeeded | failed | cancelled."""
    status: String!
    engine: String
    cacheHit: Boolean
    savedQueryId: ID
    queryVersionNo: Int
    """Present on the single-execution path only (GET /executions/{id})."""
    sqlText: String
    createdBy: String
    createdAt: DateTime
    startedAt: DateTime
    finishedAt: DateTime
    durationMs: Int
    resultRows: Int
    scanBytes: Float
    queuePosition: Int
    error: JSON
  }

  type QueryExecutionConnection { nodes: [QueryExecution!]! pageInfo: PageInfo! }

  """One row of the tenant query-stats rollup (query-service GET /stats/queries,
  QRY-FR-081): top queries by scan bytes with failure counts over a window."""
  type QueryStatRow {
    sqlFingerprint: String!
    executions: Int!
    totalScanBytes: Float!
    failures: Int!
    topUser: String
  }

  type QueryStats {
    since: DateTime
    topQueries: [QueryStatRow!]!
  }

  """A typed variable declaration (query-service QRY-FR-002). SQL references it
  as the named placeholder :name; every placeholder must be declared at save time."""
  input VariableDeclInput {
    name: String!
    """string | integer | decimal | boolean | date | timestamp | string_list | integer_list."""
    type: String!
    """Default true."""
    required: Boolean
    default: JSON
    allowedValues: [JSON!]
    min: Float
    max: Float
  }

  """
  Create/update body for a saved query (query-service POST/PATCH /queries).
  Create requires name, moduleNames (≥1) and sqlText; PATCH omissions leave
  fields unchanged. The service 422s with per-variable problems when a
  :placeholder lacks a declaration (surfaced under error extensions.details).
  """
  input SavedQueryInput {
    name: String
    description: String
    sqlText: String
    variables: [VariableDeclInput!]
    tags: [String!]
    moduleNames: [String!]
  }

  # ============================ insights (chart-service) ======================
  """A dashboard of charts (chart-service GET /dashboards/{id})."""
  type Dashboard implements Node {
    id: ID!
    urn: String!
    title: String!
    module: String
    """archiveDashboard / restoreDashboard. chart-service does not serialize
    archivedAt over REST (only the boolean flag), so there is no archivedAt field."""
    archived: Boolean!
    """Charts, hydrated with data via one batched chart-service call (AC-1).
    Pass \`filters\` to cross-filter the dashboard: a selection on one chart
    (origin) filters its same-model siblings and never itself (CHART-FR-041)."""
    charts(filters: [ChartFilterInput!]): [Chart!]!
  }

  """A cross-filter predicate, emitted by a chart selection or a manual dashboard
  filter. In a dashboard batch it is applied to sibling charts that share the
  origin chart's semantic model; a chart is never filtered by its own selection."""
  input ChartFilterInput {
    """The dimension/column to filter (a dimension of the target chart's model)."""
    field: String!
    """chart-service op whitelist: eq neq in gt gte lt lte between like."""
    op: String!
    """Bind value (never interpolated). Array for \`in\`/\`between\`."""
    value: JSON!
    """The source chart's id when emitted by a chart selection; omit for a
    manual dashboard-wide filter (which then applies to every chart)."""
    origin: ID
  }

  """A chart definition + its resolved data (chart-service)."""
  type Chart implements Node {
    id: ID!
    urn: String!
    name: String
    chartType: String
    spec: JSON
    """Editor config (chart-service chartView \`config\`), for authoring round-trip."""
    config: JSON
    """Editor display options (chart-service chartView \`display_meta\`)."""
    displayMeta: JSON
    """Typed source references (chart-service chartView \`sources\`)."""
    sources: JSON
    """Shaped, server-aggregated data (chart-service /dashboards/{id}/data batch)."""
    data: ChartData
    """AI-provenance badge data when the chart was agent-generated."""
    provenance: JSON
  }

  """Resolved chart data with per-chart error isolation (chart-service)."""
  type ChartData {
    rows: JSON
    columns: JSON
    """{nodes, edges} object shape for network-family charts (chart-service
    Shape) — null for every other family, which use rows/columns instead."""
    graph: JSON
    """Resolved artifact blob for the metric/parameter (dataset/run) family
    (chart-service ShapedResult.artifact) — e.g. {kind:"dataset_summary",
    metrics:[{label,value}]}. Null for every family that uses rows/columns."""
    artifact: JSON
    meta: JSON
  }

  """
  One entry in the chart-type catalog (chart-service GET /chart-types). Powers
  the no-code editor's type picker + per-type config form (JSON Schema-driven).
  """
  type ChartType {
    name: String!
    """Config family: axis | y_only | heatmap | network | grid | metric."""
    family: String!
    """Source class: query | dataset | run (null when unspecified)."""
    dataClass: String
    """Config field names required for this type (CHART-FR-012)."""
    requiredFields: [String!]!
    """Per-type JSON Schema for the config editor."""
    configSchema: JSON
  }

  """
  Shaped data for an UNSAVED chart spec (chart-service POST /charts/preview).
  Mirrors ShapedResult's columns/rows so the live editor preview renders without
  persisting the chart first.
  """
  type ChartShapedData {
    chartId: ID
    chartType: String
    columns: JSON
    rows: JSON
    """{nodes, edges} object shape for network-family charts (chart-service
    Shape) — null for every other family, which use rows/columns instead."""
    graph: JSON
    """Resolved artifact blob for the metric/parameter (dataset/run) family
    (chart-service ShapedResult.artifact) — e.g. {kind:"dataset_summary",
    metrics:[{label,value}]}. Null for every family that uses rows/columns."""
    artifact: JSON
    rowCount: Int
    truncated: Boolean
  }

  input CreateDashboardInput {
    name: String!
    module: String
    description: String
    layout: JSON
    meta: JSON
    tags: [String!]
  }

  input UpdateDashboardInput {
    name: String
    description: String
    layout: JSON
    meta: JSON
    tags: [String!]
  }

  input ChartSourceInput {
    position: Int!
    sourceType: String!
    sourceUrn: String!
  }

  input CreateChartInput {
    dashboardId: ID!
    name: String!
    chartType: String!
    description: String
    config: JSON!
    displayMeta: JSON
    sources: [ChartSourceInput!]
  }

  input UpdateChartInput {
    name: String
    chartType: String
    config: JSON
    displayMeta: JSON
    sources: [ChartSourceInput!]
  }

  type DashboardConnection { nodes: [Dashboard!]! pageInfo: PageInfo! }

  """
  A scheduled dashboard-report email subscription (notification-service,
  NOTIF-FR-060 — "Case Reports / Team Reports"). Each enabled
  subscription backs one real Temporal Schedule that periodically emails a live
  digest of the target dashboard's chart data to \`recipients\`.
  """
  type ReportSubscription implements Node {
    id: ID!
    urn: String!
    dashboardId: ID!
    workspaceId: ID!
    name: String!
    recipients: [String!]!
    """daily | weekly."""
    cadence: String!
    """Send hour, 0-23, local to \`timezone\`."""
    sendHour: Int!
    """0(Sun)-6(Sat); set for a weekly cadence."""
    sendWeekday: Int
    timezone: String!
    """html | text."""
    format: String!
    enabled: Boolean!
    lastSentAt: String
    """'' | sent | failed — outcome of the most recent send."""
    lastStatus: String
    lastError: String
    createdBy: String!
    createdAt: String!
    updatedAt: String!
  }

  input CreateReportSubscriptionInput {
    dashboardId: ID!
    name: String!
    recipients: [String!]!
    cadence: String!
    sendHour: Int
    sendWeekday: Int
    timezone: String
    format: String
    enabled: Boolean
  }

  input UpdateReportSubscriptionInput {
    name: String
    recipients: [String!]
    cadence: String
    sendHour: Int
    sendWeekday: Int
    timezone: String
    format: String
    enabled: Boolean
  }

  type ReportSubscriptionConnection { nodes: [ReportSubscription!]! pageInfo: PageInfo! }

  # ============================ semantic (semantic-service) ===================
  """A measure in a semantic model's published definition (semantic-service)."""
  type SemanticMeasure {
    name: String!
    """Aggregation function (sum | avg | count | ...), from the definition \`agg\`."""
    agg: String
    """Owning entity name."""
    entity: String
  }

  """A dimension in a semantic model's published definition (semantic-service)."""
  type SemanticDimension {
    name: String!
    """Owning entity name."""
    entity: String
    """Dimension type (categorical | time | numeric | ...), from the definition \`type\`."""
    dimType: String
  }

  """
  A semantic model + its published dimensions/measures (semantic-service). Powers
  the chart editor's REAL dimension/measure pickers. List items carry empty
  dimensions/measures (headers only); semanticModel(name) hydrates the full
  published definition once a model is picked.
  """
  type SemanticModel implements Node {
    id: ID!
    urn: String!
    name: String!
    dimensions: [SemanticDimension!]!
    measures: [SemanticMeasure!]!
  }

  # ============================ semantic authoring (semantic-service) =========
  """A real column of a dataset version (dataset-service), for the semantic-model
  editor's column picker — dimensions/measures bind to these, never free text."""
  type DatasetColumn {
    name: String!
    type: String
    nullable: Boolean
    tags: [String!]!
    """True when sourced from dataset-service's profile (inferred column names/
    types from a completed profiling run) rather than the dataset version's
    authoritative schema map. The version schema is empty for datasets ingested
    before schema capture was wired up on this deployment; profiling reliably
    has real column data once profiling completes, so the picker falls back to
    it rather than showing no columns for a dataset that plainly has them."""
    inferred: Boolean!
  }

  """One column filter for datasetRows. \`op\` ∈ eq | neq | contains | gt | gte
  | lt | lte. On numeric columns the comparison ops compare numerically; on
  text columns they fall back to a case-insensitive substring match."""
  input RowFilterInput {
    col: String!
    op: String!
    value: String!
  }

  """A page of dataset rows. \`columns\` is the column order; each row in
  \`rows\` is an array of display-string cells aligned to it (null preserved).
  \`total\` is the unfiltered row count; \`filtered\` is the count after the
  active filters (equal to \`total\` when no filter is set)."""
  type DatasetRowPage {
    columns: [String!]!
    rows: [[String]!]!
    total: Int!
    filtered: Int!
    offset: Int!
    limit: Int!
    """True when the dataset exceeds the browse working-set cap: total/filtered
    are then computed over the first N rows only (lower bounds). The UI shows a
    'first N rows' notice so the counts are not read as exact."""
    truncated: Boolean!
  }

  """Result of a quick-chart aggregation over a dataset. \`columns\` is
  [dimension, valueLabel]; each row is [category, aggregatedValue] as display
  strings. \`sql\` is the exact governed query that ran (surfaced so the UI can
  offer a one-click 'save as a chart/saved query')."""
  type DatasetAggregateResult {
    columns: [String!]!
    rows: [[String]!]!
    sql: String!
  }

  enum SemanticVersionStatus { DRAFT IN_REVIEW PUBLISHED REJECTED SUPERSEDED }

  """A semantic model's header (semantic-service GET /models), richer than
  \`SemanticModel\` above (which is shaped for the chart editor's field picker).
  Powers the authoring list/detail pages."""
  type SemanticModelSummary implements Node {
    id: ID!
    urn: String!
    workspaceId: ID
    name: String!
    description: String
    publishedVersionNo: Int
    """version_no of the draft this create/version call just opened, when known
    (present on createSemanticModel / createSemanticModelVersion responses)."""
    draftVersionNo: Int
    healthStatus: String
    createdBy: String
    createdAt: DateTime
    updatedAt: DateTime
  }

  type SemanticModelSummaryConnection { nodes: [SemanticModelSummary!]! pageInfo: PageInfo! }

  type SemanticEntity {
    name: String!
    datasetUrn: String!
    table: String!
    primaryKey: [String!]!
    datasetVersionPolicy: JSON!
    description: String
  }

  type SemanticDimensionDef {
    name: String!
    entity: String!
    column: String
    expr: String
    dimType: String!
    timeGrains: [String!]!
    synonyms: [String!]!
    description: String
    deprecated: Boolean!
    successor: String
  }

  type SemanticMeasureDef {
    name: String!
    entity: String
    agg: String
    expr: String
    exprMetric: String
    filters: String
    format: String
    synonyms: [String!]!
    description: String
    deprecated: Boolean!
    successor: String
  }

  type SemanticJoinPathOn { fromColumn: String! toColumn: String! }

  type SemanticJoinPath {
    name: String!
    fromEntity: String!
    toEntity: String!
    joinType: String!
    on: [SemanticJoinPathOn!]!
    cardinality: String!
  }

  """Reshaped (snake->camel) view of a version's raw JSON \`definition\`. The
  authoring mutations still take/return the definition as JSON (see
  \`updateSemanticModelDraft\`) since the editor round-trips the raw document;
  this typed view is for rendering."""
  type SemanticModelDefinition {
    entities: [SemanticEntity!]!
    dimensions: [SemanticDimensionDef!]!
    measures: [SemanticMeasureDef!]!
    joinPaths: [SemanticJoinPath!]!
  }

  """One version of a semantic model, carrying the governance state machine
  (semantic-service app/domain/state.py): draft -> in_review -> published, or
  in_review -> rejected -> draft (revise), or published -> superseded."""
  type SemanticModelVersion implements Node {
    id: ID!
    urn: String!
    modelId: ID!
    versionNo: Int!
    status: SemanticVersionStatus!
    """Present on single-version fetches; omitted (null) on list rows to avoid
    shipping the full document per row."""
    definition: SemanticModelDefinition
    """Raw JSON definition, for the editor to round-trip verbatim."""
    definitionJson: JSON
    diff: JSON
    submittedBy: String
    approvedBy: String
    decisionNote: String
    publishedAt: DateTime
    createdAt: DateTime
  }

  type SemanticModelVersionConnection { nodes: [SemanticModelVersion!]! pageInfo: PageInfo! }

  input CreateSemanticModelInput {
    name: String!
    description: String
    """Optional seed definition for the opened draft v1; omit to start empty."""
    definition: JSON
  }

  input UpdateSemanticModelInput {
    name: String
    description: String
  }

  input SemanticDimensionRefInput { name: String! grain: String }
  input SemanticFilterInput { dimension: String! op: String! values: [JSON!] }

  input CompileSemanticModelInput {
    """Model id (uuid) or name (requires workspaceId)."""
    model: ID!
    workspaceId: ID
    metrics: [String!]!
    dimensions: [SemanticDimensionRefInput!]
    filters: [SemanticFilterInput!]
    limit: Int
    dialect: String
    """Compile against an OPEN (draft/in_review/rejected) version instead of the
    published one — the editor's live preview (semantic-service BR-2)."""
    draftVersionNo: Int
    """Best-effort query-service dry-run cost/row estimate alongside the compiled
    SQL. The compiled SQL/schema always come back on success regardless of this
    flag; see \`validationAvailable\` on the result for whether the dry-run
    verdict itself was obtainable."""
    validate: Boolean
  }

  type SemanticOutputColumn { name: String! type: String role: String }

  type SemanticCompileResult {
    sql: String!
    engineDialect: String
    outputSchema: [SemanticOutputColumn!]!
    warnings: [String!]!
    provenance: JSON
    """False when \`validate: true\` was requested but the dry-run verdict could
    not be obtained (a real downstream failure — see \`validationMessage\`); the
    compiled sql/outputSchema above are unaffected and still real."""
    validationAvailable: Boolean!
    validationValid: Boolean
    validationMessage: String
  }

  enum VerifiedQueryStatus { DRAFT PENDING_REVIEW APPROVED REJECTED ARCHIVED }

  """
  A verified NL↔SQL pair (semantic-service /verified-queries, SEM-FR-040) with
  its own governance lifecycle mirroring model versions: draft ->
  pending_review -> approved | rejected (-> draft on edit), any -> archived.
  Approve/reject are four-eyes server-side: the author can never decide their
  own pair.
  """
  type VerifiedQuery implements Node {
    id: ID!
    urn: String!
    workspaceId: ID
    modelId: ID
    nlText: String!
    sqlText: String!
    variables: JSON
    status: VerifiedQueryStatus!
    tags: [String!]!
    """Authorship origin, e.g. {origin: "harvested", agent_run_urn} for
    agent-harvested candidates."""
    provenance: JSON
    """Set by revalidation when a schema change breaks the pair (SEM-FR-043)."""
    healthNote: String
    submittedBy: String
    approvedBy: String
    decidedAt: DateTime
    createdAt: DateTime
    updatedAt: DateTime
  }

  type VerifiedQueryConnection { nodes: [VerifiedQuery!]! pageInfo: PageInfo! }

  """
  One hit from semantic search over APPROVED verified NL↔SQL pairs
  (semantic-service /verified-queries:search, SEM-FR-041). Hard tenant+workspace
  scoped; \`score\` is the ANN similarity (higher = closer).
  """
  type VerifiedQuerySearchHit {
    id: ID!
    nlText: String!
    sqlText: String!
    variables: JSON
    tags: [String!]!
    modelId: ID
    score: Float!
  }

  input CreateVerifiedQueryInput {
    nlText: String!
    sqlText: String!
    """Typed variable declarations ([{name, type, required?}])."""
    variables: JSON
    """Owning semantic model id or name (optional)."""
    model: String
    tags: [String!]
  }

  """Edit a draft/rejected pair (409 otherwise; editing a rejected pair returns
  it to draft)."""
  input UpdateVerifiedQueryInput {
    nlText: String
    sqlText: String
    variables: JSON
    tags: [String!]
  }

  """An async semantic-service operation (bootstrap-from-dataset), pollable via
  semanticOperation(id)."""
  type SemanticOperation {
    operationId: ID!
    kind: String
    status: String
    report: JSON
    createdAt: DateTime
    finishedAt: DateTime
  }

  # ============================ cases (case-service) ==========================
  """
  A claims-triage case (case-service GET /cases/{id}). sourceDataset and
  assignee are federated joins into dataset-service / identity-service resolved
  via dataloaders (AC-2).
  """
  type Case implements Node {
    id: ID!
    urn: String!
    caseNumber: Int
    title: String
    status: CaseStatus
    severity: Severity
    dueDate: DateTime
    createdAt: DateTime
    """Assignee (identity-service, userById loader)."""
    assignee: User
    """Source dataset (dataset-service, datasetById loader)."""
    sourceDataset: Dataset
    """Copilot proposals targeting this case (agent-runtime, resource-urn loader)."""
    proposals: [Proposal!]!
    """Pack/dataset-provided evidence summary (key -> value; 'note' carries the
    investigator briefing). Present on BOTH list rows and detail."""
    displayProjection: JSON
    # ---- Tier 4b: caseView detail fields (null on search-projection list rows).
    description: String
    """The disposition recorded by resolveCase (null until resolved)."""
    dispositionId: ID
    resolutionNote: String
    """When the case entered resolved — reopenCase is only legal within 30 days of this."""
    resolvedAt: DateTime
    closedAt: DateTime
    """Optimistic-concurrency version, bumped by every write."""
    caseVersion: Int
    """How many times the case has been (re)assigned — SLA policy caps this."""
    reassignCount: Int
    """Uploaded evidence attachments (case-service GET /cases/{id}/evidence).
    Resolved on the detail path only; needs case.evidence.read. Download the
    bytes via the ui-web /api/case-evidence proxy (binary, not GraphQL)."""
    evidence: [CaseEvidence!]!
  }

  """One uploaded evidence file on a case (task #77). Metadata only — the bytes
  live in object storage; fetch them via /api/case-evidence/{caseId}/{id}."""
  type CaseEvidence {
    id: ID!
    caseId: ID!
    filename: String!
    contentType: String!
    sizeBytes: Float!
    uploadedBy: String
    createdAt: DateTime
  }

  type CaseConnection { nodes: [Case!]! pageInfo: PageInfo! }

  input CaseFilter { status: String severity: String assignee: String }
  input CasePatchInput { description: String dueDate: DateTime severity: Severity customFields: JSON }

  # ============================ Tier 4b: case ops ==============================
  """
  A case comment (case-service POST /cases/{id}/comments). CONTRACT GAP: the
  service exposes NO "list comments" route — a body is only ever available on
  the create response; the timeline carries comment ids only. \`caseId\`/
  \`authorId\`/\`createdAt\` are null on the updateCaseComment response because
  PATCH /comments/{cid} echoes only {id, body}.
  """
  type CaseComment {
    id: ID!
    caseId: ID
    authorId: String
    """Comment author (identity-service, userById loader)."""
    author: User
    body: String
    editedAt: DateTime
    createdAt: DateTime
  }

  """
  One case timeline entry (case-service GET /cases/{id}/timeline, CASE-FR-025)
  — the merged event+comment feed. \`comment.added\` events carry {comment_id}
  in newValue (the body itself is NOT retrievable after creation; see
  CaseComment). Needs case.case.read.
  """
  type CaseActivity {
    id: ID!
    caseId: ID
    """e.g. case.status_changed | case.assigned | comment.added | case.escalated."""
    eventType: String
    """user | agent | system."""
    actorType: String
    actorId: String
    """Acting user (identity-service, userById loader) — hydrated ONLY when
    actorType is "user"; agent/system actors resolve null."""
    actor: User
    """{agent_id, version} when the write came through an agent."""
    viaAgent: JSON
    proposalUrn: String
    oldValue: JSON
    newValue: JSON
    occurredAt: DateTime
  }

  type CaseActivityConnection { nodes: [CaseActivity!]! pageInfo: PageInfo! }

  """
  An async case bulk/export operation (case-service GET /operations/{id}).
  \`downloadUrl\` is the service-relative export path (only set once status is
  "succeeded"; expires ~15 min after completion); \`error\` carries the worker's
  real failure message when status is "failed". Needs case.case.read.
  """
  type CaseOperation {
    id: ID!
    kind: String
    """running | succeeded | failed."""
    status: String
    succeeded: Int
    failed: Int
    total: Int
    rowCount: Int
    downloadUrl: String
    expiresAt: DateTime
    error: String
  }

  """A workspace disposition catalog entry (case-service /dispositions,
  CASE-FR-020) — the closed vocabulary resolveCase draws from."""
  type Disposition {
    id: ID!
    urn: String!
    workspaceId: ID
    code: String
    label: String
    """true_positive | false_positive | benign | inconclusive | other."""
    category: String
    """When true, resolveCase requires a resolutionNote (422 otherwise)."""
    requiresNote: Boolean!
    active: Boolean!
    createdAt: DateTime
    updatedAt: DateTime
  }

  "One pushdown row-filter condition on a case trigger (op: eq|neq|contains|gt|gte|lt|lte)."
  type CaseTriggerCondition { col: String! op: String! value: String! }
  input CaseTriggerConditionInput { col: String! op: String! value: String! }

  """An event-rule case trigger (realtime-decisioning INC-1): when an ingestion
  completes into the matching dataset, rows passing the conditions are
  materialized as cases (idempotent via dataset_urn+row_pk dedup). Triggers
  create work; they never decide — four-eyes governance is untouched."""
  type CaseTrigger {
    id: ID!
    workspaceId: ID
    name: String!
    enabled: Boolean!
    datasetUrn: String
    datasetName: String
    conditions: [CaseTriggerCondition!]!
    "Row column whose value becomes the case row_pk (dedup identity); null = first column."
    rowPkField: String
    severity: String!
    dueHours: Int!
    projectionFields: [String!]!
    maxCasesPerEvent: Int!
    createdById: String
    createdAt: DateTime
    updatedAt: DateTime
  }
  input CreateCaseTriggerInput {
    name: String!
    enabled: Boolean
    datasetUrn: String
    datasetName: String
    conditions: [CaseTriggerConditionInput!]
    rowPkField: String
    severity: String
    dueHours: Int
    projectionFields: [String!]
    maxCasesPerEvent: Int
  }
  input UpdateCaseTriggerInput {
    id: ID!
    name: String
    enabled: Boolean
    datasetUrn: String
    datasetName: String
    conditions: [CaseTriggerConditionInput!]
    rowPkField: String
    severity: String
    dueHours: Int
    projectionFields: [String!]
    maxCasesPerEvent: Int
  }

  """A custom case-field config (case-service /case-fields, CASE-FR-022).
  \`purpose\` is normalized to the string form (create | update | both) — the
  service serializes it as int16 (0/1/2) on reads."""
  type CaseField {
    id: ID!
    urn: String!
    workspaceId: ID
    """Scopes the field to cases born from one saved query (null = workspace-wide)."""
    queryUrn: String
    name: String
    """string | text | integer | float | boolean | date | enum."""
    dataType: String
    """create | update | both."""
    purpose: String
    fieldMeta: JSON
    createdAt: DateTime
    updatedAt: DateTime
  }

  "One embedded field def on a typed case schema (case-service inc10)."
  type CaseSchemaField { name: String! dataType: String label: String required: Boolean }
  """A governed typed case SCHEMA: a named case TYPE (duplicate_review,
  banking_change_verification, …) binding a distinct set of embedded field defs.
  Capability packs install these; distinct from the flat CaseField catalog."""
  type CaseSchema {
    id: ID!
    workspaceId: ID
    schemaKey: ID!
    name: String!
    description: String
    fields: [CaseSchemaField!]!
    createdAt: DateTime
    updatedAt: DateTime
  }
  input CaseSchemaFieldInput { name: String! dataType: String label: String required: Boolean }
  input CreateCaseSchemaInput {
    schemaKey: ID!
    name: String!
    description: String
    fields: [CaseSchemaFieldInput!]
  }

  """The PUT /sla-policy echo (case-service, CASE-FR-012). WRITE-ONLY contract:
  the service exposes no GET for the current policy, so this is only ever the
  response to putCaseSlaPolicy — never a fabricated read."""
  type CaseSlaPolicy {
    workspaceId: ID
    warnBeforeSeconds: Int
    """auto_unassign | escalate | notify_only."""
    onBreach: String
    maxReassignCount: Int
  }

  input CreateDispositionInput {
    code: String!
    label: String!
    """true_positive | false_positive | benign | inconclusive | other."""
    category: String!
    requiresNote: Boolean
    active: Boolean
  }

  input UpdateDispositionInput {
    label: String
    category: String
    """Always sent to the service (its PATCH overwrites requires_note
    unconditionally), so pass the current value when leaving it unchanged."""
    requiresNote: Boolean
    active: Boolean
  }

  input CreateCaseFieldInput {
    queryUrn: String
    name: String!
    """string | text | integer | float | boolean | date | enum."""
    dataType: String!
    """create | update | both (the service defaults anything else to both)."""
    purpose: String
    fieldMeta: JSON
  }

  """Editable fields of a custom case-field (case-service PATCH /case-fields/{id}).
  name/dataType/queryUrn are immutable — only purpose + fieldMeta may change."""
  input UpdateCaseFieldInput {
    id: ID!
    """create | update | both (the service defaults anything else to both)."""
    purpose: String
    fieldMeta: JSON
  }

  input CaseSlaPolicyInput {
    warnBeforeSeconds: Int
    """auto_unassign | escalate | notify_only."""
    onBreach: String
    """Target user for on_breach=escalate."""
    escalateTo: ID
    maxReassignCount: Int
  }

  """One case's failure within a bulk operation (case-service partial-failure
  semantics, CASE-FR-030/031)."""
  type BulkCaseFailure { caseId: ID! code: String! message: String! }

  """Result of a bulk case operation: which ids succeeded vs failed, with the
  real per-case error for each failure — never a blind "queued" success."""
  type BulkCaseResult { succeededIds: [ID!]! failed: [BulkCaseFailure!]! }

  """One row of a case worklist: the source row's stable primary key plus a
  display projection (column → value) snapshotted onto the case for the queue."""
  input CaseRowInput {
    rowPk: String!
    displayProjection: [KeyValueInput!]!
  }

  input KeyValueInput { key: String! value: String! }

  """Create-cases request. \`datasetUrn\` is required (cases are row-anchored);
  \`queryUrn\`/\`dashboardUrn\` record provenance; \`dueDate\` is required and must
  be in the future; \`severity\` ∈ low|medium|high|critical (default medium)."""
  input CreateCasesInput {
    datasetUrn: String!
    datasetVersion: String
    queryUrn: String
    dashboardUrn: String
    dueDate: DateTime!
    severity: String
    assignedToId: ID
    description: String
    rows: [CaseRowInput!]!
  }

  type CreatedCase { id: ID! caseNumber: Int status: String dedupKey: String recurrenceOf: ID }
  type DeduplicatedRow { id: ID! caseNumber: Int rowPk: String }

  """Result of createCases: the newly-created cases and the rows that matched an
  existing case (recorded as a recurrence instead of a duplicate)."""
  type CreateCasesResult {
    created: [CreatedCase!]!
    deduplicated: [DeduplicatedRow!]!
  }

  """A dashboard chart's drill target: the real dataset behind the chart and the
  physical column a group-by dimension maps to. Feeds the dataset-rows browse so
  a manager can turn a chart selection (e.g. payer=Cigna) into cases anchored to
  real (dataset_urn, row_pk)."""
  type ChartDrillTarget {
    datasetId: ID!
    datasetUrn: String!
    column: String!
  }

  # ============================ agentic (agent-runtime) =======================
  """An agent-suggested write awaiting a human decision (agent-runtime GET /proposals/{id})."""
  type Proposal implements Node {
    id: ID!
    urn: String!
    agentKey: String
    tool: String
    argsDiff: JSON
    """Tool-plane risk tier (read|write-proposal|write-direct|admin), or "unknown"
    when the downstream payload carries no classification. Pure passthrough of the
    agent-runtime signal so the UI can fail closed on bulk-approve — the BFF makes
    no authz/business decision here."""
    riskTier: String
    rationale: String
    affectedUrns: [String!]!
    """
    Structured predicted-effect object from the originating agent (e.g.
    {summary, blast_radius, reversibility}). agent-runtime always serializes
    this as a JSON object (app/domain/entities.py Proposal.predicted_effect:
    dict), never a plain string, so this must be the JSON scalar. Was
    previously typed String, which made GraphQLScalarType.serialize throw on
    every proposal with a non-null predictedEffect, nulling the whole
    Case.proposals field (and therefore Case itself) via non-null
    propagation.
    """
    predictedEffect: JSON
    status: ProposalStatus
    decision: JSON
    createdAt: DateTime
  }

  """An agent run (agent-runtime GET /runs/{id})."""
  type AgentRun implements Node {
    id: ID!
    urn: String!
    agentKey: String
    status: RunStatus
    costUsd: Float
    tokenUsage: TokenUsage
    """Tool-call tree for the trace visualiser (agent-runtime GET /runs/{id}/trace)."""
    trace: JSON
    """Live token stream descriptor -> realtime-hub (BFF-FR-060)."""
    tokenStream: StreamHandle!
  }

  type TokenUsage { inputTokens: Int outputTokens: Int }

  type ProposalConnection { nodes: [Proposal!]! pageInfo: PageInfo! }

  """Correction->retrain loop stats: what the tenant's decisions have taught
  the system so far (agent-runtime transcript corpus + curated SFT datasets)."""
  type LearningLoopStats {
    """Agent-run transcripts captured into the governed corpus (M1)."""
    transcriptsCaptured: Int!
    """Transcripts carrying a human decision/correction — the training signal."""
    correctionsCaptured: Int!
    """Curated, versioned SFT datasets (M2)."""
    datasetCount: Int!
    """Latest curated dataset, if any."""
    latestDatasetAgentKey: String
    latestDatasetVersion: Int
    """Gold input->corrected-output examples in the latest dataset."""
    latestDatasetExamples: Int
    latestDatasetAt: DateTime
    """True when a count hit the service's 200-row page cap (display as 200+)."""
    capped: Boolean!
  }
  input DecisionInput { kind: DecisionKind! reason: String editedArgs: JSON responseText: String }

  # ============================ kill switches (agent-runtime + tool-plane) =====
  """Which control plane a kill switch targets — agent-runtime (agent execution)
  or tool-plane (tool invocation). The two backends are distinct services with
  distinct id/scope shapes; this type normalizes both for one admin surface."""
  enum KillSwitchTarget { AGENT TOOL }

  """An emergency-stop kill switch (ART-FR-073 / TPL-FR-052). \`tenantId\` null
  means a platform-wide (global) kill, visible to every tenant."""
  type KillSwitch {
    id: ID!
    target: KillSwitchTarget!
    scope: String!
    agentKey: String
    toolId: String
    version: String
    tenantId: String
    active: Boolean!
    reason: String!
    setBy: String!
    createdAt: DateTime
  }

  """The thin {id, active} response DELETE /kill-switches returns — deliberately
  NOT the full KillSwitch (the route doesn't echo scope/reason/setBy on lift, and
  the BFF never fabricates fields a downstream response didn't provide)."""
  type KillSwitchLiftResult {
    id: ID!
    active: Boolean!
  }

  # ============================ memory (memory-service) ========================
  """One stored agent memory record (memory-service _memory_view). \`status\`:
  active | quarantined | expired | deleted. \`scope\`: session | user |
  workspace | tenant. \`content\` is UNTRUSTED model input (BR-12) — render as
  plain text, never execute/interpret."""
  type MemoryRecord implements Node {
    id: ID!
    urn: String!
    scope: String!
    scopeRef: String!
    content: String!
    confidence: Float
    status: String!
    tags: [String!]!
    provenance: JSON
    retrievalCount: Int
    classifierScore: Float
    ttlExpiresAt: DateTime
    """Only populated on the single-record detail fetch, not the browse list."""
    mergedFrom: [String!]
    revalidateAt: DateTime
  }
  type MemoryRecordConnection { nodes: [MemoryRecord!]! pageInfo: PageInfo! }

  """A right-to-be-forgotten erasure request (memory-service ErasureRequest).
  \`status\`: received | running | verifying | completed | failed."""
  type ErasureRequest {
    operationId: ID!
    status: String!
    report: JSON
    completedAt: DateTime
  }

  # ============================ ml (experiment-service) =======================
  """An ML experiment (experiment-service GET /experiments/{id})."""
  type Experiment implements Node {
    id: ID!
    urn: String!
    name: String!
    description: String
    """archiveExperiment / restoreExperiment. Same deleted_at-derived convention
    as Dataset.archived — no separate status value."""
    archived: Boolean!
    runs: RunConnection!
  }

  """A single training run (experiment-service GET /runs/{id})."""
  type Run implements Node {
    id: ID!
    urn: String!
    name: String
    status: RunStatus
    metrics: JSON
    params: JSON
    model: RegisteredModel
    """Tier 4b: the owning experiment (register-as-model needs both ids)."""
    experimentId: ID
  }

  """A registered model version (experiment-service GET /models/{id})."""
  type RegisteredModel implements Node {
    id: ID!
    urn: String!
    name: String
    stage: String
  }

  """
  A registered model in the registry (experiment-service GET /models). \`versions\`
  is hydrated only on the single-model path (GET /models/{id}); the LIST path
  serves headers only, so it resolves to [] there (no per-row N+1).
  """
  type Model implements Node {
    id: ID!
    urn: String!
    name: String
    """Model family label: classification | regression | anomaly_detection | ..."""
    modelType: String
    ownerId: String
    description: String
    createdAt: DateTime
    """The model's versions with their promotion stage (detail path only)."""
    versions: [ModelVersion!]!
  }

  """
  One version of a registered model + its promotion stage (experiment-service
  _version_payload). \`stage\` is the label production | staging | archived | none.
  """
  type ModelVersion {
    modelId: ID!
    version: Int!
    urn: String!
    stage: String
    sourceRunId: ID
    flavor: String
    mlflowModelRef: String
    stageUpdatedAt: DateTime
  }

  """The outcome of a promotion REQUEST (experiment-service POST .../promote, 202).
  A pending promotion awaits a SECOND person's decision (four-eyes)."""
  type PromotionRequest {
    promotionId: ID!
    status: String!
    operationId: String
  }

  """One promotion request against a model version (experiment-service GET
  /models/{id}/versions/{v}/promotions row) — the four-eyes approval queue's
  source. \`status\` is pending | approved | rejected | expired | cancelled."""
  type Promotion implements Node {
    id: ID!
    urn: String!
    modelVersionId: String
    targetStage: String
    fromStage: String
    status: String
    rationale: String
    requestedBy: String
    viaAgent: JSON
    decision: JSON
    createdAt: DateTime
  }
  type PromotionConnection { nodes: [Promotion!]! pageInfo: PageInfo! }

  """The model + dataset references captured on an inference job (inference-service)."""
  type InferenceModelRef {
    urn: String
    name: String
    version: Int
    """The promotion stage the version held when the job was queued."""
    stageAtSubmit: String
  }
  type InferenceDatasetRef { urn: String version: Int }

  """
  A batch inference job (inference-service GET /inferences/{id}). \`status\` is the
  JobStatus name (validating | queued | submitted | running | finalizing |
  succeeded | failed | cancelling | cancelled | rejected).
  """
  type InferenceJob implements Node {
    id: ID!
    urn: String!
    name: String
    description: String
    status: String!
    model: InferenceModelRef
    inputDataset: InferenceDatasetRef
    outputDataset: InferenceDatasetRef
    rowCount: Int
    error: String
    pipelineRunUrn: String
    scheduleId: ID
    """Tier 4b: the job this one was retried FROM (set on the NEW job by retryInferenceJob)."""
    retriedFromJobId: ID
    createdAt: DateTime
    submittedAt: DateTime
    startedAt: DateTime
    finishedAt: DateTime
  }

  type ModelConnection { nodes: [Model!]! pageInfo: PageInfo! }
  type InferenceJobConnection { nodes: [InferenceJob!]! pageInfo: PageInfo! }

  input CreateExperimentInput {
    name: String!
    """Model family: classification | regression | anomaly_detection | forecasting | unsupervised | clustering."""
    modelType: String!
    description: String
    """The three pipeline URNs are required by experiment-service and must be
    mutually distinct (EXP-FR-001)."""
    modelPipelineUrn: String!
    featureEngineeringPipelineUrn: String!
    trainingPipelineUrn: String!
  }

  input CreateInferenceJobInput {
    """A promoted model VERSION urn (wr:...:experiment:model_version/<id>@<v>)."""
    modelVersionUrn: String!
    inputDatasetUrn: String!
    name: String
    description: String
    """Allow scoring a version that is not in production (default false)."""
    allowUnpromoted: Boolean
  }

  type ExperimentConnection { nodes: [Experiment!]! pageInfo: PageInfo! }
  type RunConnection { nodes: [Run!]! pageInfo: PageInfo! }

  # ====================== Tier 4b: ml ops (types + inputs) ====================
  """
  registerRunAsModel outcome (experiment-service POST
  /experiments/{eid}/runs/{rid}/register, 201). \`modelCreated\` is true when
  this call created the model header itself (first version under the name).
  """
  type RegisterModelResult {
    modelId: ID!
    version: Int!
    """Always "none" on a fresh registration — promotion is a separate four-eyes flow."""
    stage: String
    modelCreated: Boolean!
  }

  input RegisterRunInput {
    modelName: String!
    description: String
    """MLflow model flavor; the service defaults to "mlflow.sklearn"."""
    flavor: String
    ownerId: String
  }

  """PATCH /experiments/{id} — omitted fields stay unchanged (exclude_unset)."""
  input UpdateExperimentInput {
    name: String
    description: String
    note: String
  }

  """The free-text note on a run (experiment-service /runs/{id}/note)."""
  type RunNote {
    runId: ID!
    description: String
  }

  """One artifact row (experiment-service GET /runs/{id}/artifacts)."""
  type RunArtifact {
    path: String!
    sizeBytes: Int
    contentType: String
  }

  """
  The server-side run comparison matrix (experiment-service POST /runs/compare).
  \`metrics\` rows are [{key, values: {runId: value|null}, best_run_id,
  direction}] and \`params\` rows are [{key, values, differs}] — passed through
  verbatim (compare.py build_comparison). Any run id not visible in the caller's
  workspace 404s the whole request (BR-9). Needs experiment.run.read.
  """
  type RunComparison {
    runIds: [ID!]!
    metrics: JSON
    params: JSON
  }

  """PATCH .../card overlay — the 4 human-authored model-card fields; omitted
  fields stay unchanged."""
  input ModelCardOverlayInput {
    intendedUse: String
    limitations: String
    evaluationSummary: String
    ethicalConsiderations: String
  }

  """One column verdict from the inference compatibility check
  (inference-service schema_compat): ok | missing | type_mismatch | nullable_mismatch."""
  type CompatColumn {
    name: String!
    requiredType: String
    actualType: String
    verdict: String!
  }

  """
  The model×dataset compatibility report (inference-service POST
  /inferences/validate). \`stageError\` carries the stage-policy error code when
  the stage check alone fails (the report is then compatible=false). Read-only;
  needs inference.job.read.
  """
  type InferenceCompatibilityReport {
    compatible: Boolean!
    modelStage: String
    columns: [CompatColumn!]!
    warnings: JSON
    rowCount: Int
    stageError: String
  }

  input ValidateInferenceInput {
    modelVersionUrn: String!
    inputDatasetUrn: String!
    allowUnpromoted: Boolean
    allowEmpty: Boolean
  }

  """
  A recurring scoring schedule (inference-service /schedules, INF-FR-050..055).
  Model spec is EITHER a pinned \`modelVersionUrn\` OR \`modelUrn\` +
  \`stageSelector\` (resolved fresh at every fire); timing is EITHER \`cron\`
  (with \`timezone\`) OR \`intervalSeconds\`. \`pausedReason\` distinguishes a
  user pause from the consecutive-failure circuit breaker
  (AUTO_PAUSED_CONSECUTIVE_FAILURES). \`nextFireAt\` is next_fire_preview.at
  (null while paused).
  """
  type InferenceSchedule {
    id: ID!
    urn: String!
    name: String
    enabled: Boolean!
    pausedReason: String
    modelVersionUrn: String
    modelUrn: String
    """production | staging | none | archived (fire-time stage resolution)."""
    stageSelector: String
    """{dataset_urn: "..."} — the input resolved fresh at each fire."""
    inputSelector: JSON
    """{dataset_name, mode} output spec (mode defaults to append server-side)."""
    output: JSON
    cron: String
    intervalSeconds: Int
    timezone: String
    """skip | queue | cancel_running."""
    overlapPolicy: String
    consecutiveFailures: Int
    temporalScheduleId: String
    notifyOnFailure: Boolean
    nextFireAt: DateTime
  }

  type InferenceScheduleConnection { nodes: [InferenceSchedule!]! pageInfo: PageInfo! }

  """
  POST /schedules body. Server validation (mirrored in the UI): exactly ONE of
  modelVersionUrn / modelUrn (modelUrn additionally requires stageSelector), and
  exactly ONE of cron / intervalSeconds.
  """
  input CreateInferenceScheduleInput {
    name: String!
    """{dataset_urn: "..."} — the only selector shape the service resolves today."""
    inputSelector: JSON!
    """{dataset_name, mode: create|append|replace} (mode defaults to append)."""
    output: JSON!
    modelVersionUrn: String
    modelUrn: String
    stageSelector: String
    cron: String
    intervalSeconds: Int
    timezone: String
    overlapPolicy: String
    enabled: Boolean
    notifyOnFailure: Boolean
  }

  """PATCH /schedules/{id} — ONLY these fields are patchable (name/model/stage
  cannot change after creation; enabled flips via pause/resume)."""
  input UpdateInferenceScheduleInput {
    cron: String
    intervalSeconds: Int
    timezone: String
    overlapPolicy: String
    inputSelector: JSON
    output: JSON
    notifyOnFailure: Boolean
  }

  """POST /inferences/bulk body — one model over up to 20 datasets."""
  input BulkCreateInferenceInput {
    modelVersionUrn: String!
    inputDatasetUrns: [String!]!
    parameters: JSON
    outputDatasetName: String
    """create | append | replace."""
    outputMode: String
  }

  # ============================ usage (usage-service) =========================
  """Cost + budget panel, composed from usage-service in one query (US-10)."""
  type CostPanel {
    rows: [UsageRow!]!
    budgetStates: [BudgetState!]!
  }
  type UsageRow { dimensions: JSON meterKey: String quantity: Float costUsd: Float }
  type BudgetState {
    scope: String
    consumed: Float
    limit: Float
    lastThreshold: Int
    exhaustedAt: DateTime
  }

  """A budget definition (usage-service GET /budgets/{id}). Distinct from
  BudgetState (this is the configured limit; BudgetState is live spend)."""
  type Budget implements Node {
    id: ID!
    urn: String!
    """Most-specific dimension as \"workspace/<id>\" | \"user/<id>\" | \"agent/<id>\" | \"tenant/<id>\"."""
    scope: String
    meterKey: String
    window: String
    limitUsd: Float
    """Fixed v1 threshold set: [80, 95, 100]."""
    thresholds: [Int!]!
    """alert_only | hard_stop — behavior once consumption reaches 100%."""
    actionAt100: String
    status: String
    createdAt: DateTime
    updatedAt: DateTime
  }

  type BudgetConnection { nodes: [Budget!]! pageInfo: PageInfo! }

  """Create a budget (usage-service POST /budgets). Exactly one scope id should
  be set; an unscoped budget applies at the tenant level."""
  input CreateBudgetInput {
    workspaceId: String
    userId: String
    agentId: String
    meterKey: String!
    window: String!
    limitUsd: Float!
    """alert_only (default) | hard_stop."""
    actionAt100: String
  }

  """Partial budget update (usage-service PATCH /budgets/{id})."""
  input UpdateBudgetInput {
    limitUsd: Float
    actionAt100: String
  }

  """A priced rate card (usage-service GET /rate-cards). Rate-card create/activate
  are platform-only actions — a tenant admin sees the list but create/activate
  controls are hidden by capability (a tenant token gets a real 403 if forced)."""
  type RateCard implements Node {
    id: ID!
    urn: String!
    version: Int
    """YYYY-MM-DD effective date."""
    effectiveFrom: Date
    """draft | active | superseded."""
    status: String
    """meter_key -> price_per_unit_usd."""
    items: JSON
    createdAt: DateTime
  }

  type RateCardConnection { nodes: [RateCard!]! pageInfo: PageInfo! }

  """A detected spend deviation (usage-service GET /anomalies, USG-FR-050/051).
  \`status\`: open | dismissed. \`day\` is YYYY-MM-DD; \`z\` is the z-score that
  triggered detection."""
  type Anomaly implements Node {
    id: ID!
    urn: String!
    meterKey: String!
    day: Date!
    observed: Float!
    mean: Float!
    stddev: Float!
    z: Float!
    status: String!
    dismissedBy: String
    suppressedReason: String
    createdAt: DateTime!
  }

  """Create a draft rate card (usage-service POST /rate-cards). Platform-only."""
  input CreateRateCardInput {
    version: Int!
    effectiveFrom: Date!
    items: JSON!
  }

  # ============================ pipelines (pipeline-orchestrator) =============
  """An output port of a pipeline step (component.definition.outputs)."""
  type PipelineStepPort {
    name: String!
    type: String!
  }

  """
  A single configurable parameter of a pipeline step or algorithm. Derived from
  the component/algorithm parameter schema; the UI renders the right widget +
  validation per parameter. \`enumValues\` is named to avoid the GraphQL reserved
  word; \`min\`/\`max\` come from the backend \`minimum\`/\`maximum\`.
  """
  type PipelineStepParam {
    name: String!
    type: String!
    "Semantic format (column | columns | dataset_ref | expression | enum | key_value); drives the UI widget + data-binding."
    format: String
    "Element semantic for array params (e.g. columns -> item 'column')."
    itemFormat: String
    required: Boolean!
    default: JSON
    enumValues: [String!]
    min: Float
    max: Float
    help: String
  }

  """
  A step type in the component catalog (pipeline-orchestrator GET /components).
  Powers the no-code builder's node palette + per-node config forms.
  """
  type PipelineStepType {
    name: String!
    displayName: String!
    """Grouping for the palette: io | data_prep | algorithm | utility | comment."""
    category: String!
    description: String
    minInputs: Int!
    maxInputs: Int!
    maxOutputs: Int!
    outputs: [PipelineStepPort!]!
    parameters: [PipelineStepParam!]!
  }

  """An algorithm-step template (pipeline-orchestrator GET /algorithm-templates)."""
  type AlgorithmTemplate {
    name: String!
    displayName: String!
    """Model family (classification | regression | anomaly_detection | ...)."""
    family: String
    """Available modes (training | tuning | tuning_cross_validation)."""
    modes: [String!]!
    parameters: [PipelineStepParam!]!
  }

  """
  A saved pipeline template (pipeline-orchestrator GET /pipelines/{id}). The DAG
  \`definition\` and \`createdBy\` are not yet surfaced by the backend payload and
  read as null.
  """
  type PipelineTemplate implements Node {
    id: ID!
    urn: String!
    name: String!
    pipelineType: String!
    activeVersionId: ID
    definition: JSON
    validationStatus: String
    """System-owned templates cannot be archived (409)."""
    isSystem: Boolean
    """Soft-deleted via deletePipelineTemplate; restorePipelineTemplate undoes it."""
    archived: Boolean
    createdBy: String
    createdAt: String
    updatedAt: String
  }

  type PipelineTemplateList { nodes: [PipelineTemplate!]! pageInfo: PageInfo! }

  """A single problem found while validating a pipeline definition."""
  type PipelineValidationIssue {
    code: String!
    message: String!
    """The offending DAG node alias, when the issue is node-scoped (else null)."""
    node: String
  }

  """A pipeline-definition validation report (pipeline-orchestrator POST /pipelines/validate)."""
  type PipelineValidationResult {
    valid: Boolean!
    issues: [PipelineValidationIssue!]!
  }

  """A pipeline run (pipeline-orchestrator GET /runs/{id})."""
  type PipelineRun implements Node {
    id: ID!
    urn: String!
    templateId: ID!
    status: String!
    """Failure detail when status=failed (run_payload.error)."""
    error: JSON
    """Set on runs created by retryPipelineRun: the failed run this one re-drives."""
    retriedFromRunId: ID
    createdAt: String
    startedAt: String
    finishedAt: String
  }

  type PipelineRunList { nodes: [PipelineRun!]! pageInfo: PageInfo! }

  input CreatePipelineInput {
    name: String!
    pipelineType: String!
    definition: JSON!
  }

  """
  Update a pipeline template (pipeline-orchestrator PUT /pipelines/{id}). The
  pipeline type is immutable (the backend keeps it from the template), so only the
  name + DAG definition are updatable; a new immutable version is minted.
  """
  input UpdatePipelineInput {
    name: String!
    definition: JSON!
  }

  input RunPipelineInput { parameters: JSON }

  """One immutable pipeline-template version (pipeline-orchestrator GET
  /pipelines/{id}/versions)."""
  type PipelineTemplateVersion {
    id: ID!
    templateId: ID!
    versionNo: Int!
    """valid | draft — only valid versions can back a run."""
    validationStatus: String
    validationReport: JSON
    manifestDigest: String
    argoTemplateName: String
    createdAt: DateTime
  }

  type PipelineTemplateVersionConnection { nodes: [PipelineTemplateVersion!]! pageInfo: PageInfo! }

  """POST /pipelines/{id}/compile result: the compiled Argo manifest for the
  active version."""
  type CompiledPipelineManifest {
    templateId: ID
    versionId: ID
    manifestDigest: String
    argoTemplateName: String
    manifest: JSON
  }

  """GET /runs/{id}/manifest: the run's compiled manifest + resolved parameters."""
  type PipelineRunManifest {
    runId: ID
    manifest: JSON
    resolvedParameters: JSON
  }

  """
  A recurring pipeline schedule (pipeline-orchestrator /pipeline-schedules,
  PIPE-FR-050). Fires \`templateId\`'s active version on \`cron\` in \`timezone\`
  with \`runParameters\`. \`lastRunId\` points at the most recent run it created.
  """
  type PipelineSchedule implements Node {
    id: ID!
    urn: String!
    scheduleId: ID!
    templateId: ID!
    name: String
    cron: String!
    timezone: String
    runParameters: JSON
    enabled: Boolean!
    nextFireAt: DateTime
    lastFireAt: DateTime
    lastRunId: ID
    createdAt: DateTime
  }

  """Create a recurring pipeline schedule (pipeline-orchestrator POST
  /pipeline-schedules). \`cron\` is required; \`timezone\` defaults to UTC."""
  input CreatePipelineScheduleInput {
    templateId: ID!
    name: String
    cron: String!
    timezone: String
    runParameters: JSON
  }

  # ===========================================================================
  # Tier 2a: eval (eval-service) — eval flywheel: suites/runs/gates/canaries.
  # ===========================================================================
  """An eval suite pinning datasets + scorers + a gate rule for one agent
  (eval-service GET /suites/{id}). There is no list endpoint — suites are
  discovered from a run's \`suitePins\` (via \`EvalRun.suite\`) or looked up
  directly by id+version."""
  type EvalSuite implements Node {
    id: ID!
    urn: String!
    suiteId: String!
    agentKey: String!
    version: Int!
    """[{dataset_key, version}, ...]"""
    datasets: JSON!
    """[{scorer, version, weight, regression_threshold, config}, ...]"""
    scorers: JSON!
    """The gate expression (BR-1: must reference >=1 deterministic scorer)."""
    gateRule: String!
    baselineVersion: String
    judgeLadderPin: JSON
    minCases: Int!
    createdAt: DateTime
  }

  """One scorer's verdict on one case within a run (eval-service GET /runs/{id}/cases)."""
  type EvalCaseResult {
    id: ID!
    runId: ID!
    caseId: ID!
    scorerKey: String!
    scorerVersion: Int!
    score: Float!
    passed: Boolean!
    details: JSON
    traceRef: String
    latencyMs: Int
    costUsd: Float!
    weight: Float!
    createdAt: DateTime
  }

  """A single scoring run (eval-service GET /runs/{id}). \`gate\` and \`suite\` are
  resolved from the run's own suite/candidate pins (no extra input needed) —
  this is the "model scorecard" single-run view."""
  type EvalRun implements Node {
    id: ID!
    urn: String!
    trigger: String!
    agentKey: String!
    """{agent_version?, content_digest}"""
    candidate: JSON!
    baseline: JSON
    """{suite_id, suite_version, datasets, scorers, gate_rule, judge_ladder_pin, baseline_version}"""
    suitePins: JSON!
    memorySnapshotVer: String
    status: String!
    """{aggregates: {scorer_key: {mean, pass_rate, ...}}, ...}"""
    totals: JSON!
    costUsd: Float!
    costCapUsd: Float!
    startedBy: String
    createdAt: DateTime
    updatedAt: DateTime
    """Per-case, per-scorer results (eval-service GET /runs/{id}/cases)."""
    cases: [EvalCaseResult!]!
    """The suite this run was pinned to (from suitePins.suite_id/suite_version)."""
    suite: EvalSuite
    """The promotion-blocking gate for this run's candidate, if one was ever
    evaluated (matched by agentKey + candidate.content_digest + suite/dataset
    pins, mirroring eval-service's own CI idempotency lookup). null if this run
    was never gated (e.g. a manual run, or CI hasn't posted yet)."""
    gate: EvalGateResult
  }
  type EvalRunConnection { nodes: [EvalRun!]! pageInfo: PageInfo! }

  """An eval dataset VERSION (eval-service GET /datasets/{key}/versions/{v}) —
  distinct from dataset-service's \`Dataset\` (a data-catalog entity); this is
  the eval flywheel's own case-collection versioning."""
  type EvalDataset implements Node {
    id: ID!
    urn: String!
    datasetKey: String!
    agentKey: String!
    version: Int!
    status: String!
    description: String
    caseCount: Int!
    provenanceSummary: JSON
    frozenBy: String
    frozenAt: DateTime
    createdBy: String
    createdAt: DateTime
    updatedAt: DateTime
  }
  type EvalDatasetConnection { nodes: [EvalDataset!]! pageInfo: PageInfo! }

  """A case-curation-queue item (eval-service GET /cases). Sourced from verified
  queries, production traces, HITL rejections, or approval edit-diffs — the raw
  material datasets are built from before promotion to \`active\`."""
  type EvalCase implements Node {
    id: ID!
    urn: String!
    datasetKey: String!
    datasetVersion: Int!
    input: JSON!
    expected: JSON!
    source: String!
    sourceRef: String
    tags: [String!]!
    weight: Float!
    status: String!
    anonymizationAttestedBy: String
    createdAt: DateTime
    updatedAt: DateTime
  }
  type EvalCaseConnection { nodes: [EvalCase!]! pageInfo: PageInfo! }

  """A registered scorer (eval-service GET /scorers) — deterministic or llm_judge."""
  type EvalScorer implements Node {
    id: ID!
    urn: String!
    scorerKey: String!
    version: Int!
    kind: String!
    gateEligible: Boolean!
    configSchema: JSON
    applicableExpectedKinds: [String!]!
    imageRef: String
    judgePromptRef: String
    judgePromptVer: String
    """llm_judge only: judge-vs-human agreement; activation is blocked below 0.8."""
    judgeAgreement: Float
    status: String!
    createdAt: DateTime
  }
  type EvalScorerConnection { nodes: [EvalScorer!]! pageInfo: PageInfo! }

  """A promotion-blocking gate verdict (eval-service GET /gates/{gate_run_id}) —
  the real governance signal CI/promotion flows check before shipping a candidate."""
  type EvalGateResult implements Node {
    id: ID!
    urn: String!
    gateRunId: String!
    runId: ID!
    agentKey: String!
    contentDigest: String!
    suiteId: String!
    suiteVersion: Int!
    datasetVersion: Int!
    gatePassed: Boolean!
    """[{scorer, aggregate, baseline, threshold, passed}, ...]"""
    verdicts: JSON!
    failedCasesSample: JSON
    reportUrl: String
    createdAt: DateTime
  }

  """An online A/B canary comparison (eval-service GET /canaries/{id})."""
  type EvalCanary implements Node {
    id: ID!
    urn: String!
    comparisonId: String!
    agentKey: String!
    candidateVersion: String!
    baselineVersion: String!
    sampleSpec: JSON
    mode: String!
    status: String!
    """{thresholds, must_scorers, samples, recommendation?, metrics?, early_stop?}"""
    report: JSON!
    samples: Int!
    createdAt: DateTime
    updatedAt: DateTime
  }

  """One score-trend point (eval-service GET /trends) — the raw series behind
  the model-version scorecard comparison view."""
  type EvalTrendPoint {
    runId: ID!
    agentVersion: String
    scorer: String!
    mean: Float
    passRate: Float
    at: DateTime!
  }

  """One SLO rollup row for an agent+window (eval-service GET /slos)."""
  type EvalSloRow {
    agentKey: String!
    agentVersion: String
    """null = the cross-tenant platform rollup (operator-only)."""
    tenantId: String
    window: String!
    windowStart: DateTime!
    metrics: JSON!
    targets: JSON!
    sampleN: Int!
  }

  input CreateEvalSuiteInput {
    suiteId: String!
    agentKey: String!
    datasets: JSON!
    scorers: JSON!
    gateRule: String!
    baselineVersion: String
    judgeLadderPin: JSON
    minCases: Int
  }

  """Editable fields of an eval suite version (eval-service PATCH /suites/{suiteId},
  optional ?version selecting the version to edit — omit for latest). suiteId and
  agentKey are immutable; only the listed fields (when provided) are patched."""
  input UpdateEvalSuiteInput {
    suiteId: String!
    version: Int
    datasets: JSON
    scorers: JSON
    gateRule: String
    baselineVersion: String
    judgeLadderPin: JSON
    minCases: Int
  }

  input CreateEvalRunInput {
    trigger: String
    agentKey: String!
    candidate: JSON!
    suiteId: String!
    suiteVersion: Int
    candidateOutputs: JSON
    baseline: JSON
    memorySnapshotVer: String
    costCapUsd: Float
  }

  input CreateEvalDatasetInput {
    datasetKey: String!
    agentKey: String!
    description: String
    provenanceSummary: JSON
  }

  input CreateEvalCaseInput {
    datasetKey: String!
    agentKey: String
    input: JSON!
    expected: JSON!
    source: String
    sourceRef: String
    tags: [String!]
    weight: Float
    status: String
    anonymizationAttestedBy: String
  }

  input EvalCasePatchInput {
    input: JSON
    expected: JSON
    tags: [String!]
    weight: Float
    anonymizationAttestedBy: String
  }

  input CreateEvalScorerInput {
    scorerKey: String!
    version: Int!
    kind: String!
    gateEligible: Boolean
    configSchema: JSON
    applicableExpectedKinds: [String!]
    imageRef: String
    judgePromptRef: String
    judgePromptVer: String
    judgeAgreement: Float
    status: String
  }

  """Editable fields of a scorer version (eval-service PATCH /scorers/{scorerKey},
  optional ?version selecting the version to edit — omit for latest). scorerKey/kind
  are immutable; only the listed fields (when provided) are patched. BR-1: an
  llm_judge scorer can never become gate-eligible even if gateEligible: true."""
  input UpdateEvalScorerInput {
    scorerKey: String!
    version: Int
    gateEligible: Boolean
    configSchema: JSON
    applicableExpectedKinds: [String!]
    imageRef: String
    judgePromptRef: String
    judgePromptVer: String
    judgeAgreement: Float
    status: String
  }

  input CreateEvalCanaryInput {
    agentKey: String!
    candidateVersion: String!
    baselineVersion: String!
    mode: String
    sampleSpec: JSON
    thresholds: JSON
    mustScorers: [String!]
  }

  # ===========================================================================
  # Tier 2a: ai-gateway admin — provider catalog, routing ladders, ai-gateway's
  # OWN LLM-spend budgets (distinct from usage-service's Budget/BudgetState
  # above), virtual keys, guardrail policy.
  # ===========================================================================
  """An LLM provider/deployment (ai-gateway GET /admin/providers). \`circuitState\`
  and \`healthy\` are LIVE flags attached by the admin route (breaker + health
  checker), not stored fields."""
  type AiProviderDeployment implements Node {
    id: ID!
    urn: String!
    provider: String!
    modelFamily: String!
    deploymentName: String!
    region: String!
    cloud: String!
    endpointVaultRef: String!
    tpmLimit: Int!
    rpmLimit: Int!
    priority: Int!
    status: String!
    circuitState: String
    healthy: Boolean
    createdAt: DateTime
    updatedAt: DateTime
  }
  type AiProviderDeploymentConnection { nodes: [AiProviderDeployment!]! pageInfo: PageInfo! }

  """A model-routing ladder for one request class (ai-gateway GET /admin/ladders/{class})."""
  type AiModelLadder {
    id: ID!
    requestClass: String!
    scope: String!
    """Ordered rung array: [{model_alias, max_tokens, temperature_default, cost_tier}, ...]."""
    rungs: JSON!
    version: Int!
    maxRung: Int
  }

  """ai-gateway's OWN LLM-spend budget (ai-gateway GET /admin/budgets) — DISTINCT
  from usage-service's platform-cost \`Budget\` type above; this one governs the
  gateway's own admission/degrade behavior per AIG-FR-02x."""
  type AiBudget implements Node {
    id: ID!
    urn: String!
    scopeType: String!
    scopeRef: String!
    window: String!
    limitUsd: Float!
    degradePct: Int!
    status: String!
    createdAt: DateTime
    updatedAt: DateTime
  }
  type AiBudgetConnection { nodes: [AiBudget!]! pageInfo: PageInfo! }

  """Live spend against one ai-gateway budget (ai-gateway GET /admin/spend)."""
  type AiSpendRow {
    budgetId: ID!
    scopeType: String!
    scopeRef: String!
    window: String!
    windowStart: String!
    limitUsd: Float!
    spendUsd: Float!
    reservedUsd: Float!
    resetAt: DateTime!
  }

  # ADDED (provider-agnostic + cost-detail): per (provider, model, request-class)
  # cost rollup — REAL aggregation from the ai-gateway request_log.
  """One cost-detail rollup row (by provider, by model, or by request-class)."""
  type AiCostRollup {
    provider: String
    model: String
    modelAlias: String
    requestClass: String
    requests: Int!
    inputTokens: Int!
    outputTokens: Int!
    costUsd: Float!
  }
  type AiCostWindow { since: String! hours: Int! priceVersion: String! }
  type AiCostTotals {
    requests: Int!
    inputTokens: Int!
    outputTokens: Int!
    costUsd: Float!
  }
  """Cost-detail breakdown over a window (ai-gateway GET /admin/spend/breakdown):
  real per-provider / per-model / per-request-class spend from the ledgered
  request_log — no estimated numbers."""
  type AiCostBreakdown {
    window: AiCostWindow!
    totals: AiCostTotals!
    byProvider: [AiCostRollup!]!
    byModel: [AiCostRollup!]!
    byRequestClass: [AiCostRollup!]!
    detail: [AiCostRollup!]!
  }

  """A scoped virtual API key agents use to call the gateway (ai-gateway GET
  /admin/keys). \`secret\` is populated ONLY on the create/rotate mutation
  response (shown once, AIG-FR-030) — never on list/read."""
  type AiVirtualKey implements Node {
    id: ID!
    urn: String!
    principalType: String!
    principalId: String!
    allowedRequestClasses: [String!]
    maxRung: Int!
    expiresAt: DateTime
    status: String!
    createdAt: DateTime
    secret: String
  }
  type AiVirtualKeyConnection { nodes: [AiVirtualKey!]! pageInfo: PageInfo! }

  """The tenant's guardrail policy (ai-gateway GET /admin/guardrails) — PII
  redaction, prompt-injection classification, output-schema validation rules."""
  type AiGuardrailPolicy {
    policy: JSON!
    version: Int!
  }

  input CreateAiProviderInput {
    provider: String!
    modelFamily: String!
    deploymentName: String!
    region: String!
    cloud: String!
    endpointVaultRef: String!
    tpmLimit: Int
    rpmLimit: Int
    priority: Int
  }

  input PatchAiProviderInput {
    status: String
    priority: Int
    tpmLimit: Int
    rpmLimit: Int
    endpointVaultRef: String
    reason: String
  }

  input CreateAiBudgetInput {
    scopeType: String!
    scopeRef: String!
    window: String!
    limitUsd: Float!
    degradePct: Int
  }

  input PatchAiBudgetInput {
    limitUsd: Float
    degradePct: Int
    status: String
  }

  input CreateAiVirtualKeyInput {
    principalType: String!
    principalId: String!
    allowedRequestClasses: [String!]
    maxRung: Int
    ttlSeconds: Int
  }

  # ==========================================================================
  # Tier 2b: notification-service — in-app inbox, per-user preferences,
  # subscription rules, webhook endpoints (+ delivery history), templates,
  # admin delivery stats + email suppressions.
  # ==========================================================================
  """One in-app notification (notification-service Notification, NOTIF-FR-020)."""
  type Notification implements Node {
    id: ID!
    urn: String!
    eventType: String!
    severityClass: String
    title: String!
    body: String
    resourceUrn: String
    deepLink: String
    readAt: DateTime
    createdAt: DateTime
  }
  type NotificationConnection { nodes: [Notification!]! pageInfo: PageInfo! }

  """The caller's notification preferences (NOTIF-FR-012). channelOverrides maps
  event_type -> channels; digestConfig maps event_class -> window."""
  type NotificationPreferences {
    channelOverrides: JSON!
    mutes: JSON
    quietHours: JSON
    digestConfig: JSON!
    updatedAt: DateTime
  }
  input NotificationPreferencesInput {
    channelOverrides: JSON
    mutes: JSON
    quietHours: JSON
    digestConfig: JSON
  }

  """A subscription rule — what triggers a notification, for whom, on which
  channels (notification-service SubscriptionRule, NOTIF-FR-010)."""
  type NotificationRule {
    id: ID!
    scope: String!
    subjectType: String!
    subjectId: String!
    eventTypes: [String!]!
    resourceFilter: JSON
    channels: [String!]!
    digestEnabled: Boolean!
    digestWindow: String
    active: Boolean!
    createdBy: String
    createdAt: DateTime
    updatedAt: DateTime
  }
  type NotificationRuleConnection { nodes: [NotificationRule!]! pageInfo: PageInfo! }
  input NotificationRuleInput {
    scope: String
    subjectType: String
    subjectId: String
    eventTypes: [String!]
    resourceFilter: JSON
    channels: [String!]
    digestEnabled: Boolean
    digestWindow: String
    active: Boolean
  }

  """One HMAC secret version on a webhook endpoint. \`secret\` is only surfaced
  on the create/rotate mutation responses in the UI (shown-once UX); the
  downstream guard is notification.webhook.read either way."""
  type WebhookSecret {
    version: Int!
    secret: String!
    createdAt: DateTime
    expiresAt: DateTime
  }

  """An outbound webhook endpoint (notification-service WebhookEndpoint,
  NOTIF-FR-022). Creation performs a REAL challenge handshake against the URL."""
  type WebhookEndpoint {
    id: ID!
    url: String!
    eventTypes: [String!]!
    secrets: [WebhookSecret!]!
    active: Boolean!
    verifiedAt: DateTime
    circuitState: String
    consecutiveFailures: Int
    createdBy: String
    createdAt: DateTime
    updatedAt: DateTime
  }
  type WebhookEndpointConnection { nodes: [WebhookEndpoint!]! pageInfo: PageInfo! }
  input CreateWebhookInput { url: String! eventTypes: [String!]! active: Boolean }
  input UpdateWebhookInput { url: String eventTypes: [String!] active: Boolean }

  """One delivery attempt for a webhook endpoint (NOTIF-FR-024)."""
  type WebhookDelivery {
    id: ID!
    eventId: ID
    status: String!
    attempts: Int!
    lastError: String
    providerMsgId: String
    nextRetryAt: DateTime
    createdAt: DateTime
    updatedAt: DateTime
  }
  type WebhookDeliveryConnection { nodes: [WebhookDelivery!]! pageInfo: PageInfo! }

  """One notification template version (NOTIF-FR-040)."""
  type NotificationTemplate {
    id: ID!
    key: String!
    channel: String!
    locale: String!
    version: Int!
    subjectTpl: String
    bodyHtmlTpl: String
    bodyTextTpl: String
    status: String!
    publishedAt: DateTime
    createdBy: String
    createdAt: DateTime
  }
  input CreateNotificationTemplateInput {
    key: String!
    channel: String!
    locale: String
    subjectTpl: String
    bodyHtmlTpl: String
    bodyTextTpl: String
  }
  """A rendered template preview (real render against a sample event)."""
  type NotificationTemplatePreview { subject: String! html: String! text: String! }

  """Tenant delivery stats for a rolling window (NOTIF-FR-051)."""
  type NotificationDeliveryStats { window: String! byChannel: JSON! }

  """A suppressed email recipient (bounce/complaint/manual)."""
  type EmailSuppression {
    id: ID!
    emailHash: String!
    reason: String!
    createdAt: DateTime
    clearedAt: DateTime
  }

  # ==========================================================================
  # Tier 2b: tool-plane registry admin — catalog CRUD/lifecycle, per-tenant
  # enablement, BYO onboarding queue.
  # ==========================================================================
  """A catalog tool header (tool-plane Tool, TPL-FR-001)."""
  type Tool {
    toolId: ID!
    displayName: String
    ownerService: String!
    ownerTeam: String
    enabledByDefault: Boolean!
    sideEffects: String!
    tags: [String!]!
    createdAt: DateTime
    updatedAt: DateTime
  }
  type ToolConnection { nodes: [Tool!]! pageInfo: PageInfo! }
  input RegisterToolInput {
    toolId: ID!
    displayName: String
    ownerService: String!
    ownerTeam: String
    enabledByDefault: Boolean
    sideEffects: String
    tags: [String!]
  }

  """A tool version (tool-plane ToolVersion). Returned by the add/publish
  lifecycle mutations; the browse path is ToolHealth (status per version)."""
  type ToolVersion {
    toolId: ID!
    version: String!
    status: String!
    semanticDescription: String
    permissionTier: String
    costWeight: Int
    sideEffects: String
    inputSchema: JSON
    outputSchema: JSON
    declaredSla: JSON
    deprecationEndsAt: DateTime
    publishedAt: DateTime
  }
  input AddToolVersionInput {
    version: String!
    semanticDescription: String!
    inputSchema: JSON
    outputSchema: JSON
    permissionTier: String
    costWeight: Int
    declaredSla: JSON
    sideEffects: String
    examples: JSON
  }

  """Deprecate/retire responses echo only the lifecycle outcome — the BFF never
  fabricates the fields the route didn't return."""
  type ToolVersionLifecycleResult { status: String! deprecationEndsAt: DateTime }

  """Published schemas for a tool version (tool-plane GET /tools/{id}/schema)."""
  type ToolSchema { toolId: ID! version: String! inputSchema: JSON outputSchema: JSON }

  """Per-version status + declared SLA + rolling health counters (tool-plane
  GET /tools/{id}/health, TPL-FR-050). Doubles as the per-tool version list."""
  type ToolVersionHealth { version: String! status: String! declaredSla: JSON health: JSON }
  type ToolHealth { toolId: ID! versions: [ToolVersionHealth!]! }

  """The caller-tenant's enablement/overrides for one tool (TPL-FR-004)."""
  type TenantToolSettings {
    toolId: ID!
    enabled: Boolean!
    maxTierOverride: String
    argumentConstraints: JSON
    rateLimitOverride: JSON
    updatedAt: DateTime
  }
  input SetToolEnablementInput {
    enabled: Boolean!
    maxTierOverride: String
    argumentConstraints: JSON
    rateLimitPerMin: Int
  }

  """A BYO (bring-your-own external tool) onboarding submission (TPL-FR-040)."""
  type ByoSubmission {
    id: ID!
    manifest: JSON
    endpointUrl: String!
    authMethod: String!
    requestedTier: String!
    egressDescription: String
    status: String!
    decidedBy: String
    decisionMessage: String
    createdAt: DateTime
  }
  input SubmitByoToolInput {
    manifest: JSON
    endpointUrl: String!
    authMethod: String
    requestedTier: String
    egressDescription: String
  }
  """The thin decide response ({id, status, decided_by}) — deliberately not the
  full submission (the route doesn't echo it)."""
  type ByoDecision { id: ID! status: String! decidedBy: String! }

  # ==========================================================================
  # Tier 2b: agent-runtime catalog/registry — definitions, versions, publish,
  # per-tenant agent config, run history. NB: these routes authorize on raw
  # JWT scopes (operator / tenant.admin), not rbac capabilities — the UI gates
  # them on the Admin role.
  # ==========================================================================
  """An agent definition in the registry (agent-runtime AgentDefinition)."""
  type AgentDefinition {
    agentKey: ID!
    displayName: String!
    description: String
    ownerTeam: String
    defaultWriteMode: String
    status: String
    latestPublishedVersion: Int
  }

  """One version of an agent (agent-runtime AgentVersion, read view)."""
  type AgentVersionInfo {
    agentKey: ID!
    version: Int!
    status: String!
    graphRef: String
    graphDigest: String
    guardrailProfile: String
    evalGateResultId: String
    toolset: JSON
    modelConfig: JSON
  }

  """The thin publish response ({agent_key, version, status})."""
  type AgentVersionPublishResult { agentKey: ID! version: Int! status: String! }

  """The caller-tenant's config for one agent. \`configured\` false means no row
  exists yet and the values shown are the runtime defaults."""
  type TenantAgentConfig {
    agentKey: ID!
    configured: Boolean!
    enabled: Boolean!
    pinnedVersion: Int
    promptParams: JSON
    autoExecutePolicy: JSON
    selfApproval: Boolean!
  }
  input TenantAgentConfigInput {
    enabled: Boolean
    pinnedVersion: Int
    promptParams: JSON
    autoExecutePolicy: JSON
    selfApproval: Boolean
  }

  """BRD 53 inc2b: author a tenant CUSTOM agent as governed configuration plus
  its guardrail envelope (inc2). The server validates + clamps: allowedTools must
  be tenant-enabled, dataScope workspaces must be UUIDs, budget is capped to the
  platform ceiling, tier can never exceed write-proposal."""
  input CreateCustomAgentInput {
    displayName: String!
    persona: String!
    systemPrompt: String
    allowedTools: [String!]!
    proposeTool: String
    dataScopeWorkspaces: [ID!]
    budgetMaxTokensPerSession: Int
    blockPiiEgress: Boolean
    redactPii: Boolean
  }

  """The created custom agent, echoing the stored (validated + clamped) policy."""
  type CustomAgentResult {
    agentKey: ID!
    status: String!
    graphRef: String!
    allowedTools: [String!]!
    persona: String!
    ownerTenant: ID!
    guardrailPolicy: JSON
  }

  """One role -> its bound persona copilot (BRD 53 inc3 PA-FR-010)."""
  type PersonaBinding { role: String! agentKey: ID! }
  type AutobindResult { created: [PersonaBinding!]! skipped: [PersonaBinding!]! }

  """Operator-set platform ceilings that clamp every tenant custom agent (BR-8)."""
  type AgentCeilings {
    maxBudgetTokens: Int!
    maxTier: String!
    updatedAt: String
    updatedBy: String
  }

  """One row in the tenant run history (agent-runtime GET /runs, Tier 2b).
  Deliberately lighter than AgentRun — no per-row trace/tokenStream fan-out;
  open the run detail (Query.agentRun) for those."""
  type AgentRunListItem {
    id: ID!
    urn: String!
    sessionId: ID
    agentKey: String
    agentVersion: Int
    """Uppercased passthrough of agent-runtime's run status. Deliberately NOT
    the shared RunStatus enum: agent runs carry extra states the enum can't
    represent (live-verified: AWAITING_APPROVAL while a proposal is pending)."""
    status: String
    principalType: String
    usage: JSON
    error: JSON
    createdAt: DateTime
  }
  type AgentRunListItemConnection { nodes: [AgentRunListItem!]! pageInfo: PageInfo! }

  # ============================ roots =========================================
  type Query {
    """The authenticated viewer (from the JWT; no downstream call)."""
    me: Viewer!
    user(id: ID!): User
    """The tenant user directory (identity-service GET /users), cursor-paginated. Admin only."""
    users(first: Int = 50, after: String): UserConnection!
    """Member-safe directory of ACTIVE tenant users for assignment / mention pickers
    (identity-service GET /users/assignable). Returns only id/email/fullName — status
    and lastLoginAt are always null here. No admin scope required, unlike \`users\`:
    any member holding case.case.assign can list assignees."""
    assignableUsers(first: Int = 50, after: String): UserConnection!

    """Workspaces (rbac-service GET /workspaces). \`archived\`: null|"only"|"with". Admin only."""
    workspaces(first: Int = 50, after: String, archived: String): WorkspaceConnection!
    """The caller tenant's own OIDC IdP config (BYO-P4, identity-service GET
    /tenants/self/idp). configured is false when SSO has never been set up.
    Needs the tenant-admin identity scope."""
    tenantIdp: TenantIdpConfig!
    """The caller tenant's SIEM export destination state (BRD 59 WS2,
    audit-service GET /audit/siemconfig): the live destination (if any), a
    proposal awaiting a second approver (if any), and full decision history."""
    siemConfig: SiemConfigState!
    """A single workspace (rbac-service GET /workspaces/{id})."""
    workspace(id: ID!): Workspace

    """RBAC groups (rbac-service GET /groups). \`type\`: permission|content. Admin only."""
    groups(first: Int = 50, after: String, type: String): GroupConnection!
    """A single group (rbac-service GET /groups/{id})."""
    group(id: ID!): Group
    """A group's members (rbac-service GET /groups/{id}/members), cursor-paginated."""
    groupMembers(groupId: ID!, first: Int = 50, after: String): [GroupMember!]!
    """The roles currently bound to a permission group (rbac-service GET
    /groups/{id}/roles), cursor-paginated — the read side of assign/unassignTeamRole."""
    groupRoles(groupId: ID!, first: Int = 50, after: String): [Role!]!
    """The groups a user belongs to (rbac-service GET /users/{id}/groups),
    cursor-paginated — the reverse of group membership."""
    userGroups(userId: ID!, first: Int = 50, after: String): [Group!]!

    """Tenant + system roles (rbac-service GET /roles), cursor-paginated. Feeds the
    role picker used to bind a role to a Team. Admin only."""
    roles(first: Int = 50, after: String): RoleConnection!

    """The real authz decision trace for a subject+action(+resource) tuple
    (rbac-service POST /authz/explain) — a debug tool for "why was I denied".
    Needs audit.log.read."""
    explainAuthz(input: ExplainAuthzInput!): AuthzExplanation!

    # ---- Tier 4b: identity/rbac admin ----------------------------------------
    """Effective access for one resource (rbac-service GET
    /grants?resource_urn=<urn>): direct + implicit-creator + via-group rows with
    provenance. Needs rbac.grant.list."""
    contentGrants(resourceUrn: String!): [EffectiveAccessEntry!]!

    """Service accounts (identity-service GET /service-accounts). Admin only."""
    serviceAccounts(first: Int = 50, after: String): ServiceAccountConnection!

    """The caller's tenant UI label overrides as a list (identity-service GET
    /tenants/self/labels). Member-visible read; editing needs identity.user.admin.
    The same overrides Viewer.displayLabels exposes, in editor shape."""
    tenantLabels: [LabelOverride!]!

    """The caller's tenant + settings (identity-service GET /tenants/{id}). Admin only."""
    tenant(id: ID!): Tenant

    """All tenants (identity-service GET /tenants). Platform-admin only (identity's requireSuperAdmin enforces)."""
    tenants(limit: Int): [Tenant!]!

    """
    Search the WORM audit trail (audit-service GET /audit/search). Admin only.
    \`from\`/\`to\` are RFC3339; when omitted the resolver defaults to the last 7
    days (audit enforces a 92-day max window). Filters are exact-match.
    """
    auditEvents(
      from: DateTime
      to: DateTime
      eventType: String
      action: String
      actorId: String
      actorType: String
      resourceUrn: String
      first: Int = 50
      after: String
    ): AuditEventConnection!

    """Poll an async compliance-pack build job (audit-service GET
    /operations/{id}). Needs audit.compliance.read."""
    complianceOperation(id: ID!): ComplianceJob

    """BRD 60 WS5 — the tamper-evident auditor evidence pack for ONE governed
    decision (audit-service POST /compliance/evidence-pack): who proposed, who
    approved (a distinct human), the exact tool call, and cryptographic proof
    the record wasn't altered. Needs audit.compliance.read."""
    evidencePack(proposalId: ID!): EvidencePack!

    dataset(id: ID!): Dataset
    datasets(first: Int = 50, after: String, q: String, filter: DatasetFilter): DatasetConnection!

    """The connector-type catalog (ingestion-service). Powers the New Connection picker + forms."""
    connectorTypes: [ConnectorType!]!
    """A single saved connection (ingestion-service GET /connections/{id})."""
    connection(id: ID!): DataConnection
    """Saved connections, cursor-paginated (ingestion-service GET /connections)."""
    connections(first: Int = 50, after: String, q: String, connectorType: String, trafficDirection: String): DataConnectionList!

    """A single decision write-back job (ingestion-service GET /writebacks/{id}). Needs ingestion.writeback.read."""
    writeback(id: ID!): Writeback
    """Write-back jobs, newest first — a bounded ops/admin list (no cursor;
    ingestion-service's GET /writebacks doesn't paginate). Needs ingestion.writeback.read."""
    writebacks(status: String, workspaceId: String, first: Int = 50): [Writeback!]!

    """Ingestion runs, cursor-paginated (ingestion-service GET /ingestions). Needs ingestion.ingestion.read."""
    ingestions(first: Int = 50, after: String, status: String, mode: String): IngestionConnection!
    """A single ingestion run (ingestion-service GET /ingestions/{id})."""
    ingestion(id: ID!): Ingestion

    """An upload session's status/progress (ingestion-service GET /uploads/{id}).
    Needs ingestion.upload.read."""
    upload(id: ID!): Upload

    """Recurring ingestion schedules, cursor-paginated (ingestion-service GET
    /schedules). Needs ingestion.schedule.read."""
    ingestionSchedules(first: Int = 50, after: String): IngestionScheduleConnection!
    """A single schedule (ingestion-service GET /schedules/{id})."""
    ingestionSchedule(id: ID!): IngestionSchedule

    """Live sample rows from a SAVED connection before ingesting (ingestion-service
    POST /connections/{id}/preview — read-only, never persisted; ING-FR-005).
    Needs ingestion.connection.read."""
    connectionPreview(id: ID!, input: ConnectionPreviewInput!): ConnectionPreview!

    """A dataset's lineage graph (dataset-service GET /lineage). Needs dataset.lineage.read."""
    datasetLineage(urn: String!, direction: String = "both", depth: Int): DatasetLineage!

    """Downstream-consumer rollup for a dataset (dataset-service GET
    /datasets/{id}/consumers). Needs dataset.dataset.read."""
    datasetConsumers(id: ID!): DatasetConsumers!

    """A dataset's version history, cursor-paginated (dataset-service GET
    /datasets/{id}/versions), newest first. Needs dataset.dataset.read."""
    datasetVersions(datasetId: ID!, first: Int = 50, after: String): DatasetVersionConnection!

    """Datasets similar to the given one, ranked (dataset-service POST
    /datasets:similar over the dataset's current-version schema/columns; falls
    back to profile columns when the version schema map is empty). Needs
    dataset.dataset.read."""
    similarDatasets(datasetId: ID!): [SimilarDataset!]!

    """Saved queries, cursor-paginated (query-service GET /queries). Needs query.query.read."""
    savedQueries(first: Int = 50, after: String, workspaceId: ID): SavedQueryConnection!
    """A single saved query WITH its current-version SQL (query-service GET /queries/{id})."""
    savedQuery(id: ID!): SavedQuery
    """A saved query's immutable version history (query-service GET
    /queries/{id}/versions), newest first. Needs query.query.read."""
    savedQueryVersions(queryId: ID!, first: Int = 50, after: String): SavedQueryVersionConnection!
    """Execution history, cursor-paginated (query-service GET /executions).
    \`status\` filters queued|running|succeeded|failed|cancelled; \`savedQueryId\`
    narrows to one saved query's runs. Needs query.execution.read."""
    queryExecutions(first: Int = 50, after: String, status: String, savedQueryId: ID, since: DateTime): QueryExecutionConnection!
    """A single execution WITH its sql_text (query-service GET /executions/{id})."""
    queryExecution(id: ID!): QueryExecution
    """Top queries by scan bytes over a window (query-service GET /stats/queries,
    default last 7 days). Needs query.stats.read."""
    queryStats(since: DateTime, limit: Int): QueryStats!

    dashboard(id: ID!): Dashboard
    dashboards(workspaceId: ID!, first: Int = 50, after: String): DashboardConnection!
    """Archived-only dashboards (chart-service GET /dashboards?filter[archived]=true).
    Needs chart.dashboard.read."""
    archivedDashboards(workspaceId: ID!, first: Int = 50, after: String): DashboardConnection!

    """A single report subscription (notification-service GET /reports/{id})."""
    reportSubscription(id: ID!): ReportSubscription
    """
    Scheduled dashboard-report subscriptions, cursor-paginated, optionally
    narrowed to one dashboard (notification-service GET /reports). Needs
    notification.report.read.
    """
    reportSubscriptions(dashboardId: ID, first: Int = 50, after: String): ReportSubscriptionConnection!

    """The chart-type catalog (chart-service GET /chart-types). Powers the editor's type picker."""
    chartTypes: [ChartType!]!
    """A single chart (chart-service GET /charts/{id}); \`data\` hydrates standalone."""
    chart(id: ID!): Chart
    """Preview an UNSAVED chart spec for the live editor (chart-service POST /charts/preview)."""
    chartPreview(input: CreateChartInput!): ChartShapedData!

    """
    Semantic models for the editor's field pickers (semantic-service GET /models).
    Headers only — list items carry empty dimensions/measures; use semanticModel(name)
    to hydrate the published definition once a model is picked.
    """
    semanticModels(workspaceId: ID): [SemanticModel!]!
    """
    A single semantic model by name, WITH its published dimensions/measures
    (semantic-service GET /models + GET /models/{id}/definition). null if the name
    is unknown; dimensions/measures are empty when the model has no published version.
    """
    semanticModel(name: String!): SemanticModel

    """Real columns of a dataset version (dataset-service), for the semantic-model
    editor's column picker. version omitted = the dataset's current version.
    Needs dataset.dataset.read."""
    datasetSchema(datasetId: ID!, version: Int): [DatasetColumn!]!

    """Server-paged, sortable, per-column-filterable browse of a dataset's
    current-version rows (dataset-service GET /datasets/{id}/rows) — the data
    grid's backend. \`filters\` are ANDed; \`op\` ∈ eq|neq|contains|gt|gte|lt|lte
    (numeric ops compare numerically on numeric columns). Cells come back as
    display strings (null preserved). Needs dataset.dataset.read."""
    datasetRows(
      datasetId: ID!
      offset: Int = 0
      limit: Int = 50
      sort: String
      dir: String
      filters: [RowFilterInput!]
    ): DatasetRowPage!

    """Resolve a dashboard chart's backing dataset + the physical column a
    group-by dimension maps to, so a chart selection can drill into the real
    detail rows (dataset browse) and open cases from them. Follows the chart's
    primary source: only \`semantic_measure\` charts resolve (chart ->
    display_meta.semantic_model -> published model definition -> the dimension's
    entity dataset_urn + column); returns null for saved-query/other charts or an
    unknown dimension. Needs chart.chart.read + semantic.model.read (the caller's
    token is forwarded)."""
    chartDrillTarget(chartId: ID!, dimension: String!): ChartDrillTarget

    """Quick-chart aggregation over a raw dataset — WITHOUT a hand-authored
    semantic model. Generates a governed \`SELECT <dimension>, <agg>(<measure>)
    GROUP BY <dimension>\` over the dataset's \`{{dataset()}}\` macro and runs it
    in the warehouse (query-service, DuckDB) — never aggregated in the BFF.
    \`dimension\`/\`measure\` MUST be real columns of the dataset (validated
    server-side); \`agg\` ∈ count|sum|avg|min|max (measure omitted ⇒ count(*)).
    Returns the aggregated rows plus the generated SQL (for "save to dashboard").
    Needs dataset.dataset.read + query.execution.execute."""
    datasetAggregate(
      datasetId: ID!
      dimension: String!
      measure: String
      agg: String!
      limit: Int = 50
    ): DatasetAggregateResult!

    """Semantic-model authoring list (semantic-service GET /models), cursor-
    paginated, richer headers than \`semanticModels\` above. Needs semantic.model.list."""
    semanticModelList(workspaceId: ID, first: Int = 50, after: String): SemanticModelSummaryConnection!
    """A single semantic model's header by id. Needs semantic.model.read."""
    semanticModelDetail(id: ID!): SemanticModelSummary
    """A model's version history (semantic-service GET /models/{id}/versions),
    headers only (no definition per row). Needs semantic.model.read."""
    semanticModelVersions(modelId: ID!, first: Int = 50, after: String): SemanticModelVersionConnection!
    """A single version WITH its full definition (semantic-service GET
    /models/{id}/versions/{version_no}). Needs semantic.model.read."""
    semanticModelVersion(modelId: ID!, versionNo: Int!): SemanticModelVersion
    """Compile metrics+dimensions+filters against a model (semantic-service POST
    /compile) — the editor's real preview/dry-run action. Needs semantic.compile.execute."""
    compileSemanticModel(input: CompileSemanticModelInput!): SemanticCompileResult!

    """Verified NL↔SQL pairs, cursor-paginated (semantic-service GET
    /verified-queries). \`status\` filters draft|pending_review|approved|rejected|
    archived. Needs semantic.verified_query.list."""
    verifiedQueries(workspaceId: ID, status: String, first: Int = 50, after: String): VerifiedQueryConnection!
    """A single verified pair (semantic-service GET /verified-queries/{id}).
    Needs semantic.verified_query.read."""
    verifiedQuery(id: ID!): VerifiedQuery
    """Semantic search over APPROVED verified NL↔SQL pairs (semantic-service GET
    /verified-queries:search, SEM-FR-041), hard-scoped to tenant + workspace.
    Needs semantic.verified_query.read."""
    verifiedQuerySearch(query: String!, workspaceId: ID!, topK: Int): [VerifiedQuerySearchHit!]!
    """Poll an async semantic operation (semantic-service GET /operations/{id}),
    e.g. a bootstrap-from-dataset. Needs semantic.model.read."""
    semanticOperation(id: ID!): SemanticOperation

    case(id: ID!): Case
    caseSearch(q: String, filter: CaseFilter, first: Int = 50, after: String): CaseConnection!

    # ---- Tier 4b: case ops ---------------------------------------------------
    """A case's merged event+comment timeline, cursor-paginated newest-first
    (case-service GET /cases/{id}/timeline). Needs case.case.read."""
    caseTimeline(caseId: ID!, first: Int = 50, after: String): CaseActivityConnection!
    """Poll an async case bulk/export operation (case-service GET
    /operations/{id}). Needs case.case.read."""
    caseOperation(id: ID!): CaseOperation
    """The workspace disposition catalog (case-service GET /dispositions —
    workspace resolved from the JWT claim). Needs case.disposition.read."""
    dispositions: [Disposition!]!
    "Event-rule case triggers for the caller's workspace (case.trigger.read)."
    caseTriggers: [CaseTrigger!]!
    """Custom case-field configs, optionally scoped to one saved query
    (case-service GET /case-fields?query_urn=). Needs case.case.read."""
    caseFields(queryUrn: String): [CaseField!]!
    """The workspace's typed case schemas — named case TYPES binding a field set
    (case-service GET /case-schemas, workspace from the JWT claim). Needs
    case.schema.read."""
    caseSchemas: [CaseSchema!]!

    proposalsInbox(status: ProposalStatus = PENDING, agentKey: String, first: Int = 50, after: String): ProposalConnection!
    proposal(id: ID!): Proposal
    agentRun(id: ID!): AgentRun
    """Correction->retrain loop stats (agent-runtime transcript corpus +
    curated SFT datasets, BRD 12 M1/M2). Counts are honest page counts capped
    at 200 — capped is true when the underlying page was full."""
    learningLoop: LearningLoopStats!

    experiments(first: Int = 50, after: String): ExperimentConnection!
    experiment(id: ID!): Experiment
    """Archived-only experiments (experiment-service GET /experiments/list_archived
    — a dedicated route, unlike dataset-service). Needs experiment.experiment.read."""
    archivedExperiments(first: Int = 50, after: String, workspaceId: String): ExperimentConnection!
    run(id: ID!): Run

    """Registered models (experiment-service GET /models). \`stage\` filters to a
    single promotion stage (e.g. "production"). Needs experiment.model.read."""
    models(first: Int = 50, after: String, stage: String): ModelConnection!
    """A single registered model WITH its versions + stages (experiment-service GET /models/{id})."""
    model(id: ID!): Model
    """
    A model version's promotion history/approval-queue (experiment-service GET
    /models/{id}/versions/{version}/promotions), cursor-paginated. No server-side
    status filter — filter client-side (e.g. to "pending" for the approval
    queue). Needs experiment.model.read.
    """
    promotions(modelId: ID!, version: Int!, first: Int = 50, after: String): PromotionConnection!
    """Batch inference jobs (inference-service GET /inferences). Needs inference.job.read."""
    inferenceJobs(first: Int = 50, after: String, status: String): InferenceJobConnection!
    """A single inference job (inference-service GET /inferences/{id})."""
    inferenceJob(id: ID!): InferenceJob

    # ---- Tier 4b: ml ops (experiment-service run tooling + inference-service --
    # ---- schedules) -----------------------------------------------------------
    """
    The experiment's best run by one metric (experiment-service GET
    /experiments/{id}/runs/best). \`direction\` is max|min (the query param is
    literally \`direction\`); \`status\` optionally restricts to one run status.
    The service 404s when NO run carries the metric — surfaced as null here. The
    payload's {metric: float} map folds into the Run's existing \`metrics\`
    field (mapRun already reduces plain-number maps), so no parallel BestRun
    wrapper type is needed. Needs experiment.run.read.
    """
    bestRun(experimentId: ID!, metric: String!, direction: String, status: String): Run
    """
    Server-side run comparison (experiment-service POST /runs/compare — a read
    modeled as a query; POST only because of the id-list body). Needs
    experiment.run.read.
    """
    compareRuns(runIds: [ID!]!, metrics: [String!], params: [String!], includeAll: Boolean): RunComparison!
    """The run's note (experiment-service GET /runs/{id}/note; 404 → null).
    Needs experiment.run.read."""
    runNote(runId: ID!): RunNote
    """
    Raw logged metric points for a run, optionally filtered to \`keys\`
    (experiment-service GET /runs/{id}/metric-history). Rows pass through
    verbatim: [{key, step, value, logged_at}]. Needs experiment.run.read.
    """
    runMetricHistory(runId: ID!, keys: [String!]): JSON!
    """The run's artifact index (experiment-service GET /runs/{id}/artifacts).
    Needs experiment.run.read."""
    runArtifacts(runId: ID!): [RunArtifact!]!
    """
    A REAL signed download url for one artifact (experiment-service GET
    /runs/{id}/artifacts/url?path=). Short-lived; fetched on demand per click. A
    missing path is a real 404 error, not a fabricated link. Needs experiment.run.read.
    """
    runArtifactUrl(runId: ID!, path: String!): String!
    """
    The MERGED model card for one version (experiment-service GET
    /models/{id}/versions/{v}/card): auto fields (algorithm, params,
    final_metrics, schemas, promotion_history, training_data_unavailable, ...)
    + the human \`overlay\`, verbatim as JSON. 404 → null. Needs experiment.model.read.
    """
    modelCard(modelId: ID!, version: Int!): JSON

    """Recurring scoring schedules (inference-service GET /schedules). Needs
    inference.schedule.read."""
    inferenceSchedules(first: Int = 50, after: String): InferenceScheduleConnection!
    """One scoring schedule (inference-service GET /schedules/{id}). Needs
    inference.schedule.read."""
    inferenceSchedule(id: ID!): InferenceSchedule
    """A schedule's fire history — the real jobs it submitted (inference-service
    GET /schedules/{id}/fires). Needs inference.schedule.read."""
    inferenceScheduleFires(scheduleId: ID!, first: Int = 50, after: String): InferenceJobConnection!

    """Usage rows + budget states for a workspace, one round trip (usage-service)."""
    workspaceCostPanel(workspaceId: ID!, from: Date!, to: Date!): CostPanel!

    """Budget definitions (usage-service GET /budgets). Server-side page size is
    fixed at 50 regardless of \`first\`. Admin only."""
    budgets(first: Int = 50, after: String): BudgetConnection!
    """A single budget (usage-service GET /budgets/{id})."""
    budget(id: ID!): Budget
    """Rate cards (usage-service GET /rate-cards, not server-paginated). Admin only."""
    rateCards(first: Int = 50, after: String): RateCardConnection!

    """Detected spend anomalies (usage-service GET /anomalies) — not server-
    paginated (fixed 100-row cap downstream). \`status\` filters to open |
    dismissed; omitted returns all. Needs usage.anomaly.read."""
    anomalies(status: String): [Anomaly!]!

    """The step-type catalog (pipeline-orchestrator). Powers the builder's node palette."""
    pipelineStepTypes: [PipelineStepType!]!
    """The algorithm-step catalog (pipeline-orchestrator GET /algorithm-templates)."""
    algorithmTemplates: [AlgorithmTemplate!]!
    """Saved pipeline templates, cursor-paginated (pipeline-orchestrator GET
    /pipelines). \`includeArchived\` also returns soft-deleted templates (their
    \`archived\` flag is true) so they can be restored."""
    pipelineTemplates(first: Int = 50, after: String, q: String, pipelineType: String, includeArchived: Boolean): PipelineTemplateList!
    """A single saved pipeline template (pipeline-orchestrator GET /pipelines/{id})."""
    pipelineTemplate(id: ID!): PipelineTemplate
    """Pipeline runs, cursor-paginated (pipeline-orchestrator GET /runs)."""
    pipelineRuns(first: Int = 50, after: String, templateId: ID, status: String): PipelineRunList!
    """A single pipeline run (pipeline-orchestrator GET /runs/{id})."""
    pipelineRun(id: ID!): PipelineRun
    """A run's compiled manifest + resolved parameters (pipeline-orchestrator GET
    /runs/{id}/manifest). Needs pipeline.run.read."""
    pipelineRunManifest(id: ID!): PipelineRunManifest!
    """A template's immutable version history, cursor-paginated
    (pipeline-orchestrator GET /pipelines/{id}/versions). Needs pipeline.template.read."""
    pipelineTemplateVersions(templateId: ID!, first: Int = 50, after: String): PipelineTemplateVersionConnection!
    """Recurring pipeline schedules for the tenant (pipeline-orchestrator GET
    /pipeline-schedules). Needs pipeline.schedule.read."""
    pipelineSchedules: [PipelineSchedule!]!

    """Active agent kill switches (agent-runtime GET /registry/kill-switches).
    Operators see every tenant's; a tenant admin sees their own tenant's +
    platform-global kills. Emergency-stop admin surface, not a general read."""
    agentKillSwitches: [KillSwitch!]!
    """Active tool kill switches (tool-plane GET /kill-switches). Needs
    tool.kill.read."""
    toolKillSwitches: [KillSwitch!]!

    """
    Browse/search agent memory records (memory-service GET /memories) — "what
    does the agent know about this workspace/user" admin surface. \`scope\`
    is session | user | workspace | tenant; \`scopeRef\` is that scope's id
    (e.g. a workspace id). Needs memory.memory.read.
    """
    memories(
      scope: String
      scopeRef: String
      status: String
      tags: [String!]
      first: Int = 50
      after: String
    ): MemoryRecordConnection!
    """A single memory record, full detail (memory-service GET /memories/{id}).
    Needs memory.memory.read."""
    memory(id: ID!): MemoryRecord
    """Poll a right-to-be-forgotten erasure request's status/report
    (memory-service GET /erasure/{id}). Needs memory.erasure.read."""
    erasure(id: ID!): ErasureRequest
    """Tenant memory stats (memory-service GET /stats), opaque passthrough.
    Needs memory.stats.read."""
    memoryStats: JSON!

    # ---- Tier 2a: eval (eval-service) --------------------------------------
    """A suite by id (+optional version; latest when omitted). Needs eval.suite.write
    (eval-service requires the write scope even to read a suite)."""
    evalSuite(suiteId: String!, version: Int): EvalSuite
    """Scoring runs, cursor-paginated (eval-service GET /runs). Needs eval.run.read."""
    evalRuns(agentKey: String, trigger: String, first: Int = 50, after: String): EvalRunConnection!
    """A single run (eval-service GET /runs/{id}). Needs eval.run.read."""
    evalRun(id: ID!): EvalRun
    """Eval dataset versions, cursor-paginated (eval-service GET /datasets). Needs eval.dataset.read."""
    evalDatasets(agentKey: String, first: Int = 50, after: String): EvalDatasetConnection!
    """A single dataset version (eval-service GET /datasets/{key}/versions/{v}). Needs eval.dataset.read."""
    evalDataset(datasetKey: String!, version: Int!): EvalDataset
    """The case curation queue, cursor-paginated (eval-service GET /cases).
    \`status\` defaults to candidate (the queue's default filter). Needs eval.case.read."""
    evalCases(
      datasetKey: String
      datasetVersion: Int
      status: String
      source: String
      first: Int = 50
      after: String
    ): EvalCaseConnection!
    """A single case (eval-service GET /cases/{id}). Needs eval.case.read."""
    evalCase(id: ID!): EvalCase
    """The scorer registry, cursor-paginated (eval-service GET /scorers). Needs eval.scorer.admin."""
    evalScorers(first: Int = 200, after: String): EvalScorerConnection!
    """A gate verdict by its run-scoped id (eval-service GET /gates/{gate_run_id}).
    Needs eval.gate.read."""
    evalGate(gateRunId: String!): EvalGateResult
    """Gate verdicts for a candidate build, matched by agent+content-digest
    (eval-service GET /gates) — the CI/promotion dedup lookup. Needs eval.gate.read."""
    evalGatesByDigest(agentKey: String!, contentDigest: String!): [EvalGateResult!]!
    """A canary A/B comparison by id (eval-service GET /canaries/{id}). Needs eval.canary.manage."""
    evalCanary(comparisonId: String!): EvalCanary
    """Score-trend series for an agent (eval-service GET /trends) — the model-
    version scorecard's raw data. Needs eval.trends.read."""
    evalTrends(agentKey: String!, scorer: String, window: String): [EvalTrendPoint!]!
    """SLO rollups for an agent+window (eval-service GET /slos). Needs eval.slo.read."""
    evalSlos(agentKey: String!, window: String): [EvalSloRow!]!

    # ---- Tier 2a: ai-gateway admin ------------------------------------------
    """The LLM provider/deployment catalog (ai-gateway GET /admin/providers).
    Needs ai.provider.read + the platform-operator scope."""
    aiProviders(first: Int = 50, after: String): AiProviderDeploymentConnection!
    """A model-routing ladder for one request class: chat|sql-gen|judge|embed
    (ai-gateway GET /admin/ladders/{class}). Needs ai.ladder.read."""
    aiLadder(requestClass: String!): AiModelLadder
    """ai-gateway's own LLM-spend budgets (ai-gateway GET /admin/budgets) —
    distinct from usage-service's \`budgets\` above. Needs ai.budget.read."""
    aiBudgets(scopeType: String, first: Int = 50, after: String): AiBudgetConnection!
    """A single ai-gateway budget (ai-gateway GET /admin/budgets/{id}). Needs ai.budget.read."""
    aiBudget(id: ID!): AiBudget
    """Live spend for every budget at a scope (ai-gateway GET /admin/spend).
    Needs ai.spend.read."""
    aiSpend(scopeType: String!, scopeRef: String!, window: String): [AiSpendRow!]!
    """Cost-detail breakdown (by provider / model / request-class) over the last
    \`windowHours\` (ai-gateway GET /admin/spend/breakdown). Needs ai.spend.read."""
    aiCostBreakdown(windowHours: Int = 24): AiCostBreakdown!
    """Virtual API keys, cursor-paginated (ai-gateway GET /admin/keys); never
    carries \`secret\` (only the create/rotate mutation responses do). Needs ai.key.read."""
    aiKeys(first: Int = 50, after: String): AiVirtualKeyConnection!
    """The tenant's guardrail policy (ai-gateway GET /admin/guardrails). Needs ai.guardrail.read."""
    aiGuardrailPolicy: AiGuardrailPolicy!

    # ---- Tier 2b: notification-service ---------------------------------------
    """The caller's in-app notification inbox (notification-service GET
    /notifications), newest first. \`unread\` true filters to unread only.
    Needs notification.inbox.read."""
    notifications(unread: Boolean, first: Int = 50, after: String): NotificationConnection!
    """Unread inbox count for the bell badge (GET /notifications/unread-count).
    Needs notification.inbox.read."""
    notificationUnreadCount: Int!
    """The caller's notification preferences (GET /preferences). Needs
    notification.preference.read."""
    notificationPreferences: NotificationPreferences!
    """Tenant subscription rules (GET /rules), cursor-paginated. Needs
    notification.rule.read (admin surface)."""
    notificationRules(first: Int = 50, after: String): NotificationRuleConnection!
    """Tenant webhook endpoints (GET /webhooks), cursor-paginated. Needs
    notification.webhook.read (admin surface)."""
    notificationWebhooks(first: Int = 50, after: String): WebhookEndpointConnection!
    """Delivery history for one webhook endpoint (GET /webhooks/{id}/deliveries).
    Needs notification.webhook.read."""
    notificationWebhookDeliveries(webhookId: ID!, first: Int = 50, after: String): WebhookDeliveryConnection!
    """Template versions for one template key (GET /templates?filter[key]=…).
    Needs notification.template.read."""
    notificationTemplates(key: String!): [NotificationTemplate!]!
    """Tenant delivery stats for a rolling window like "24h" (GET /admin/stats).
    Needs notification.admin.read."""
    notificationDeliveryStats(window: String): NotificationDeliveryStats!
    """Suppressed email recipients (GET /admin/suppressions). Needs
    notification.admin.read."""
    emailSuppressions: [EmailSuppression!]!

    # ---- Tier 2b: tool-plane registry admin ----------------------------------
    """The tool catalog (tool-plane GET /tools), cursor-paginated. Needs tool.tool.read."""
    tools(first: Int = 50, after: String, ownerService: String): ToolConnection!
    """Per-version status + SLA + rolling health for one tool (GET
    /tools/{id}/health) — the per-tool version list. Needs tool.tool.read."""
    toolHealth(toolId: ID!): ToolHealth
    """A tool version's published schemas (GET /tools/{id}/schema). Needs tool.tool.read."""
    toolSchema(toolId: ID!, version: String): ToolSchema
    """The BYO onboarding queue (tool-plane GET /byo), newest first. \`status\`
    filters to pending_approval|approved|rejected. Needs tool.byo.approve."""
    byoSubmissions(status: String): [ByoSubmission!]!

    # ---- Tier 2b: agent-runtime catalog/registry ------------------------------
    """The agent catalog (agent-runtime GET /registry/agents). Requires the
    operator or tenant.admin JWT scope downstream (Admin-role surface)."""
    agentDefinitions: [AgentDefinition!]!
    """Versions of one agent, newest first (GET /registry/agents/{key}/versions).
    Operator/tenant.admin."""
    agentVersions(agentKey: String!): [AgentVersionInfo!]!
    """The caller-tenant's config for one agent (GET /registry/tenants/self/
    agents/{key}). Operator/tenant.admin."""
    tenantAgentConfig(agentKey: String!): TenantAgentConfig
    """Operator-only: the platform ceilings that clamp every custom agent (BRD 53
    inc3). Errors for non-operators downstream."""
    agentCeilings: AgentCeilings!
    """Run history for the caller's tenant (agent-runtime GET /runs), newest
    first. Any tenant principal; tenant-scoped downstream by RLS."""
    agentRuns(agentKey: String, first: Int = 50): AgentRunListItemConnection!

    # ---- BRD 54 inc2: governed decision tables --------------------------------
    """Tenant decision tables (agent-runtime GET /decision-models). Needs
    case.disposition.read."""
    decisionModels: [DecisionModel!]!
    """One decision table by id (GET /decision-models/{id})."""
    decisionModel(id: ID!): DecisionModel
    """Change log: every version of one logical table, newest first."""
    decisionModelVersions(id: ID!): [DecisionModel!]!

    # ---- BRD 56: entity resolution (steward surface) --------------------------
    """Prior entity-resolution runs for a dataset, newest first (dataset-service
    GET /datasets/{id}/resolution-runs). Needs dataset.entity.read."""
    resolutionRuns(datasetId: ID!, limit: Int = 50): [ResolutionRun!]!
    """The domain ontology — governed entity TYPES with attributes + typed
    relationships (dataset-service GET /ontology/entities). Omit workspaceId to
    list the whole tenant. Needs dataset.ontology.read."""
    ontologyEntities(workspaceId: ID): [OntologyEntity!]!
    """The governed model archetypes — intended-model blueprints a vertical
    declares (experiment-service GET /archetypes). Omit workspaceId to list the
    whole tenant. Needs experiment.archetype.read."""
    modelArchetypes(workspaceId: ID): [ModelArchetype!]!
    """One resolution run with its resolved clusters + member lineage (AC-4;
    GET /resolution-runs/{id}). Needs dataset.entity.read."""
    resolutionRun(id: ID!): ResolutionRunDetail
    """The below-auto merge candidates a steward reviews for a run (four-eyes;
    GET /resolution-runs/{id}/merge-candidates). Needs dataset.entity.read."""
    mergeCandidates(runId: ID!, status: String): [MergeCandidate!]!

    # ---- BRD 23: capability packs (pack-service) ------------------------------
    """The capability-pack catalog (pack-service GET /packs). Needs pack.pack.read."""
    packs: [Pack!]!
    """One pack's manifest detail incl. its honest deferred-component reasons."""
    pack(name: String!): Pack
    """Pack installs into a workspace (GET /installs). Needs pack.install.read."""
    packInstalls(workspaceId: String): [PackInstall!]!
    """One install with its materialization ledger (GET /installs/{id})."""
    packInstall(id: ID!): PackInstall
    """Detect drift of an install vs Core's current state (GET /installs/{id}/drift).
    Read-only. Needs pack.install.read."""
    packDrift(installId: ID!): PackDrift
  }

  "One typed condition in a decision-table rule (BRD 54 DM-FR-010/051)."
  type DecisionCondition { column: String! op: String! value: JSON }
  "The disposition + severity a rule (or the default) applies."
  type DecisionOutcome { dispositionCode: String! severity: String! }
  "A first-match rule: all conditions must hold; then apply the outcome."
  type DecisionRule { when: [DecisionCondition!]! then: DecisionOutcome note: String }
  """A governed decision table — deterministic condition→outcome rules that
   execute to the same four-eyes proposal an agent produces."""
  type DecisionModel {
    id: ID!
    name: String!
    version: Int!
    status: String!
    workspaceId: String
    datasetUrn: String
    createdBy: String
    approvedBy: String
    approvedAt: String
    rules: [DecisionRule!]!
    defaultOutcome: DecisionOutcome
  }
  input DecisionConditionInput { column: String! op: String! value: JSON }
  input DecisionOutcomeInput { dispositionCode: String! severity: String! }
  input DecisionRuleInput { when: [DecisionConditionInput!]! then: DecisionOutcomeInput! note: String }
  input CreateDecisionModelInput {
    name: String!
    workspaceId: String
    rules: [DecisionRuleInput!]!
    defaultOutcome: DecisionOutcomeInput
  }
  input BatchEvaluateInput { workspaceId: String caseIds: [ID!] limit: Int }
  "One case's evaluation in a batch run."
  type BatchEvaluateRow {
    caseId: ID!
    matched: Boolean!
    ruleIndex: Int
    explanation: String!
    outcome: DecisionOutcome
    proposalId: ID
    proposalStatus: String
    executed: Boolean
  }
  type BatchEvaluateSummary {
    cases: Int!
    matched: Int!
    unmatched: Int!
    proposalsCreated: Int!
    byOutcome: JSON!
  }
  type BatchEvaluateResult {
    modelId: ID!
    proposed: Boolean!
    summary: BatchEvaluateSummary!
    results: [BatchEvaluateRow!]!
  }

  # ---- BRD 56: entity resolution (steward surface) --------------------------

  "One attribute of a domain entity type (a named, typed field)."
  type OntologyAttribute { name: String! dataType: String }
  "A typed relationship from one entity type to another (e.g. Vendor has_many Invoice)."
  type OntologyRelationship { name: String! target: String! cardinality: String }
  """A governed domain ontology entity TYPE: a named type with attributes and
  typed relationships to other types. Distinct from dataset-derived semantic
  entities and from entity RESOLUTION (which resolves instances of these types)."""
  type OntologyEntity {
    id: ID!
    entityKey: ID!
    workspaceId: ID!
    name: String!
    description: String!
    attributes: [OntologyAttribute!]!
    relationships: [OntologyRelationship!]!
    createdAt: String
  }
  input OntologyAttributeInput { name: String! dataType: String }
  input OntologyRelationshipInput { name: String! target: String! cardinality: String }
  input CreateOntologyEntityInput {
    workspaceId: ID!
    entityKey: ID!
    name: String!
    description: String
    attributes: [OntologyAttributeInput!]
    relationships: [OntologyRelationshipInput!]
  }

  """A governed model ARCHETYPE (experiment-service inc9): the intended-model
  blueprint a vertical declares — task type, target, expected metrics and
  governance expectations — independent of any trained artifact. Capability
  packs install these; the ml-engineer agent resolves + promotes models against
  them. Distinct from registered MODELS (materialized from runs)."""
  type ModelArchetype {
    id: ID!
    archetypeKey: ID!
    workspaceId: ID!
    name: String!
    taskType: String!
    target: String
    description: String
    expectedMetrics: JSON
    governanceNotes: String
    createdAt: String
  }
  input CreateModelArchetypeInput {
    workspaceId: ID!
    archetypeKey: ID!
    name: String!
    taskType: String!
    target: String
    description: String
    expectedMetrics: JSON
    governanceNotes: String
  }

  type ResolutionRun {
    runId: ID!
    datasetId: ID!
    configId: ID
    entityType: String!
    recordCount: Int!
    resolvedEntityCount: Int!
    mergedClusterCount: Int!
    reviewCandidateCount: Int!
    status: String!
    createdBy: String
    createdAt: String
  }
  "One member record folded into a resolved entity (lineage / audit, AC-4)."
  type ResolvedMember { memberPk: String! method: String evidence: JSON }
  "A resolved-entity cluster: the golden entity + the records that merged into it."
  type ResolvedCluster {
    resolvedEntityId: ID!
    memberCount: Int!
    confidence: Float
    method: String
    members: [ResolvedMember!]!
  }
  "A run plus its resolved clusters and member lineage."
  type ResolutionRunDetail {
    runId: ID!
    datasetId: ID!
    configId: ID
    entityType: String!
    recordCount: Int!
    resolvedEntityCount: Int!
    mergedClusterCount: Int!
    reviewCandidateCount: Int!
    status: String!
    createdBy: String
    createdAt: String
    clusters: [ResolvedCluster!]!
  }
  "The outcome of running a resolution: run summary + the persisted run/config ids."
  type ResolveEntitiesResult {
    datasetId: ID!
    entityType: String!
    recordCount: Int!
    resolvedEntityCount: Int!
    mergedClusterCount: Int!
    reviewCandidateCount: Int!
    runId: ID
    configId: ID
    configVersion: Int
  }
  """A below-auto merge candidate a steward reviews. Confirming one opens a
   four-eyes proposal (proposalId) a DIFFERENT user must approve."""
  type MergeCandidate {
    id: ID!
    runId: ID!
    datasetId: ID!
    entityType: String!
    leftPk: String!
    rightPk: String!
    score: Float
    evidence: JSON
    status: String!
    proposalId: ID
    decidedBy: String
    decidedAt: String
    createdAt: String
  }
  "The pending four-eyes proposal minted for a reviewed merge candidate."
  type EntityMergeProposal {
    proposalId: ID!
    status: String!
    executed: Boolean
    runId: ID
  }
  "The governed resolved-entity dataset produced by materialization (golden records)."
  type MaterializeResolvedResult {
    resolvedDatasetId: ID!
    resolvedDatasetUrn: String!
    name: String!
    rowCount: Int!
    columns: [String!]!
    versionNo: Int!
    icebergTable: String!
  }
  input ScoringFieldInput { column: String! weight: Float = 1.0 }
  "A resolution config the steward runs (deterministic keys + probabilistic scoring)."
  input ResolutionConfigInput {
    entityType: String = "entity"
    deterministicKeys: [[String!]!]
    scoringFields: [ScoringFieldInput!]
    blockingFields: [String!]
    autoMergeThreshold: Float = 0.85
    reviewThreshold: Float = 0.60
  }
  input ResolveEntitiesInput {
    pkColumn: String!
    config: ResolutionConfigInput!
    rowLimit: Int = 20000
  }
  input ProposeEntityMergeInput {
    datasetId: ID!
    runId: ID!
    candidateId: ID!
    leftPk: String
    rightPk: String
    score: Float
    workspaceId: String
    rationale: String
  }
  input MaterializeAttributeInput { column: String! agg: String = "first" }
  input MaterializeResolvedInput {
    name: String
    workspaceId: String
    attributes: [MaterializeAttributeInput!]!
  }

  # ---- BRD 23: capability packs (pack-service) --------------------------------

  "A component kind + how many of it a pack ships (e.g. dashboards × 3)."
  type PackComponentCount { kind: String! count: Int! }
  "A component a pack declares but Core can't materialize yet — never faked."
  type PackDeferred { kind: String! reason: String! }
  """A capability pack: one vertical solution (semantic model, dashboards, case
   taxonomy, roles, agents, decision tables…) shipped as one installable bundle."""
  type Pack {
    name: String!
    version: String!
    description: String!
    publisherName: String
    categories: [String!]!
    regulatory: [String!]!
    components: [PackComponentCount!]!
    deferredKinds: [String!]!
    """Populated on the single-pack detail query: the honest per-kind deferral reasons."""
    deferred: [PackDeferred!]!
  }
  "One operation in a dry-run install plan (create | exists | deferred)."
  type PackPlanOp { kind: String! identity: String! name: String action: String! detail: String }
  """One materialized object in the install ledger — origin-tagged so uninstall
   reverses exactly what the pack created and nothing a user made."""
  type PackLedgerRow {
    id: ID!
    kind: String!
    identity: String!
    targetUrn: String
    targetId: String
    origin: String!
    action: String!
    detail: String
    reversible: Boolean!
    tombstoned: Boolean!
  }
  "A governed install of a pack version into a workspace + its ledger."
  type PackInstall {
    id: ID!
    pack: String!
    version: String!
    workspaceId: String!
    status: String!
    summary: JSON
    createdBy: String
    createdAt: String
    plan: [PackPlanOp!]!
    ledger: [PackLedgerRow!]!
  }
  "The dry-run plan for an install (no side effects)."
  type PackInstallPlan {
    pack: String!
    version: String!
    workspaceId: String!
    plan: [PackPlanOp!]!
  }
  "Outcome of reversing an install (PKG-FR-025)."
  type PackUninstallResult { id: ID! status: String! reversed: Int! tombstoned: Int! }
  "Outcome of phase 2 (dashboards materialized after the semantic model is approved)."
  type PackCompleteResult { id: ID! status: String! dashboards: [PackLedgerRow!]! }
  "Drift of one install vs Core's current state (PKG-FR-031)."
  type PackDrift {
    id: ID!
    pack: String!
    version: String!
    workspaceId: String!
    "True if this install was superseded by an upgrade/rollback (drift is reported on the head)."
    superseded: Boolean!
    "modified + missing objects."
    drifted: Int!
    inSync: Boolean!
    "Counts by status: objects/in_sync/modified/missing/unverified/content_checked."
    summary: JSON!
    "Per-object drift rows (shape owned by pack-service)."
    objects: [JSON!]!
  }
  "Change counts for an upgrade/rollback."
  type PackTransitionDiff { added: Int! removed: Int! retained: Int! }
  """Result of an upgrade or rollback. On dryRun, only the diff is populated; on
   execute, id is the new superseding install and status is its state."""
  type PackTransition {
    "New superseding install id (execute); null on dryRun."
    id: ID
    pack: String!
    operation: String!
    fromVersion: String
    toVersion: String
    dryRun: Boolean!
    "Install status after execute (installed | awaiting_approval | failed); null on dryRun."
    status: String
    supersedes: ID
    summary: JSON
    diff: PackTransitionDiff!
  }

  type Mutation {
    """Register a domain ontology entity type (idempotent by entityKey within the
    workspace). Needs dataset.ontology.create."""
    createOntologyEntity(input: CreateOntologyEntityInput!): OntologyEntity!
    "Remove a domain ontology entity type. Needs dataset.ontology.delete."
    deleteOntologyEntity(entityKey: ID!, workspaceId: ID!): Boolean!
    """Register a governed model archetype (idempotent by archetypeKey within the
    workspace). Needs experiment.archetype.create."""
    createModelArchetype(input: CreateModelArchetypeInput!): ModelArchetype!
    "Remove a governed model archetype. Needs experiment.archetype.delete."
    deleteModelArchetype(archetypeKey: ID!, workspaceId: ID!): Boolean!

    """
    Invite a user (identity-service POST /users/invite). Creates the user in the
    "invited" state. This depends on Keycloak: if the (currently untested-against-
    live) KC admin path errors, the downstream failure surfaces verbatim — the BFF
    never fabricates a success. Admin only.
    """
    inviteUser(input: InviteUserInput!, idempotencyKey: String): User!

    """Create a workspace (rbac-service POST /workspaces). Needs rbac.workspace.create. Admin only."""
    createWorkspace(input: CreateWorkspaceInput!, idempotencyKey: String): Workspace!

    """(Re)generate the tenant's embed secret and set its allowed embedding
    origins (identity-service PUT /tenants/{id}/embed-config). Rotating
    invalidates the previous secret immediately — any embedding host still
    presenting the old one starts getting 401s from /token/embed. Tenant admin
    only (identity.tenant.update)."""
    setEmbedConfig(tenantId: ID!, allowedOrigins: [String!]!, idempotencyKey: String): SetEmbedConfigResult!
    """Register/update the caller tenant's OIDC IdP (BYO-P4, PUT /tenants/self/
    idp). The issuer must be globally unique. Needs the tenant-admin scope."""
    setTenantIdp(input: SetTenantIdpInput!, idempotencyKey: String): TenantIdpConfig!
    """Turn off SSO for the caller's tenant (DELETE /tenants/self/idp)."""
    deleteTenantIdp: Boolean!
    """Set the caller tenant's brand color tokens (BRD 59 WS3, PUT
    /tenants/self/branding). Leaves a previously uploaded logo untouched —
    upload/replace the logo via POST /api/tenant-branding/logo (multipart,
    outside GraphQL). Needs identity.user.admin."""
    setTenantBranding(input: SetTenantBrandingInput!): TenantBranding!
    """Revert the caller tenant to the platform default brand: clears colors
    AND the logo in one action (DELETE /tenants/self/branding). Needs
    identity.user.admin."""
    deleteTenantBranding: Boolean!
    """Propose a new SIEM export destination (BRD 59 WS2, audit-service POST
    /audit/siemconfig) -- four-eyes gated: this creates a pending proposal,
    it does NOT take effect until a DISTINCT admin approves it. The currently
    active destination, if any, keeps delivering unaffected until then. Needs
    audit.siemconfig.create."""
    proposeSiemConfig(input: ProposeSiemConfigInput!): SiemConfig!
    """Approve a pending SIEM destination proposal (POST /audit/siemconfig/
    {id}/approve). Four-eyes: fails if the caller is the same subject who
    proposed it. Needs audit.siemconfig.approve."""
    approveSiemConfig(id: ID!): SiemConfig!
    """Decline a pending SIEM destination proposal (POST /audit/siemconfig/
    {id}/reject); the proposer may reject their own proposal to withdraw it.
    Needs audit.siemconfig.approve."""
    rejectSiemConfig(id: ID!, reason: String): SiemConfig!
    """Remove a decided (approved/rejected) SIEM destination row (DELETE
    /audit/siemconfig/{id}). Needs audit.siemconfig.delete."""
    deleteSiemConfig(id: ID!): Boolean!
    """Upsert one UI label override (identity PUT /tenants/self/labels merges it).
    Returns the full merged override list. Needs identity.user.admin."""
    setTenantLabel(key: String!, value: String!): [LabelOverride!]!
    """Revert one UI label override to the base i18n string (DELETE
    /tenants/self/labels/{key}). Needs identity.user.admin."""
    deleteTenantLabel(key: ID!): Boolean!

    """Add a user to a group (rbac-service PUT /groups/{id}/members/{userId}). Idempotent. Admin only."""
    addGroupMember(groupId: ID!, userId: ID!, idempotencyKey: String): Boolean!
    """Remove a user from a group (rbac-service DELETE /groups/{id}/members/{userId}). Admin only."""
    removeGroupMember(groupId: ID!, userId: ID!): Boolean!

    """Create a Team (rbac-service POST /groups, group_type=permission). Needs
    rbac.group.create. Admin only."""
    createTeam(input: CreateTeamInput!, idempotencyKey: String): Group!
    """Update a Team's name/description (rbac-service PATCH /groups/{id}). Needs
    rbac.group.update. Admin only."""
    updateTeam(id: ID!, input: UpdateTeamInput!, idempotencyKey: String): Group!
    """Delete a Team (rbac-service DELETE /groups/{id}). Needs rbac.group.delete. Admin only."""
    deleteTeam(id: ID!): Boolean!
    """Bind a role to a Team (rbac-service PUT /groups/{id}/roles/{roleId}). Needs
    rbac.group.update. Admin only."""
    assignTeamRole(groupId: ID!, roleId: ID!): Boolean!
    """Unbind a role from a Team (rbac-service DELETE /groups/{id}/roles/{roleId}).
    Needs rbac.group.update. Admin only."""
    unassignTeamRole(groupId: ID!, roleId: ID!): Boolean!

    # ---- Tier 4b: identity/rbac admin (user + service-account lifecycle, -----
    # ---- workspace lifecycle, content groups, roles, content grants) ---------
    """Rename a user (identity-service PATCH /users/{id}, body {full_name}).
    Needs identity.user.admin."""
    updateUser(id: ID!, fullName: String!, idempotencyKey: String): User!
    """Deactivate a user (identity-service POST /users/{id}/deactivate). The
    last-admin guard (BR-9) 409s unless \`overrideLastAdmin\` (super-admin only,
    sent as ?override_last_admin=true) — the real 409 surfaces verbatim. Needs
    identity.user.admin."""
    deactivateUser(id: ID!, overrideLastAdmin: Boolean, idempotencyKey: String): User!
    """Re-issue the activation link for an invited user (identity-service POST
    /users/{id}/invite/resend). Needs identity.user.admin."""
    resendUserInvite(id: ID!, idempotencyKey: String): User!
    """Soft-delete a user (identity-service DELETE /users/{id}, 204). Needs
    identity.user.admin."""
    deleteUser(id: ID!): Boolean!

    """Create a service account (identity-service POST /service-accounts, 201).
    The returned apiKey is shown EXACTLY ONCE — it is never retrievable again.
    Needs identity.service_account.admin."""
    createServiceAccount(input: CreateServiceAccountInput!, idempotencyKey: String): CreatedServiceAccount!
    """Rotate a service account's API key (identity-service POST
    /service-accounts/{id}/rotate). The NEW apiKey is shown exactly once; the
    old secret is invalidated. Needs identity.service_account.admin."""
    rotateServiceAccount(id: ID!, idempotencyKey: String): CreatedServiceAccount!
    """Revoke a service account (identity-service DELETE /service-accounts/{id},
    204). Irreversible. Needs identity.service_account.admin."""
    revokeServiceAccount(id: ID!): Boolean!

    """Edit a workspace's name/description/public flag (rbac-service PATCH
    /workspaces/{id}). Needs rbac.workspace.update."""
    updateWorkspace(id: ID!, input: UpdateWorkspaceInput!, idempotencyKey: String): Workspace!
    """Archive a workspace (rbac-service POST /workspaces/{id}/archive) — sets
    archived_at; content behind it stops resolving for non-admins. Needs
    rbac.workspace.admin."""
    archiveWorkspace(id: ID!, idempotencyKey: String): Workspace!
    """Restore an archived workspace (rbac-service POST /workspaces/{id}/restore).
    Needs rbac.workspace.admin."""
    restoreWorkspace(id: ID!, idempotencyKey: String): Workspace!
    """Link a content group to a workspace (rbac-service PUT
    /workspaces/{id}/content-groups/{groupId}). Needs rbac.workspace.update."""
    linkWorkspaceContentGroup(workspaceId: ID!, groupId: ID!): Boolean!
    """Unlink a content group from a workspace (rbac-service DELETE
    /workspaces/{id}/content-groups/{groupId}). Needs rbac.workspace.update."""
    unlinkWorkspaceContentGroup(workspaceId: ID!, groupId: ID!): Boolean!

    """Create a group of either kind (rbac-service POST /groups) — the general
    path, used for CONTENT groups in particular. groupType is lowercased to the
    wire value. Teams (permission groups) keep createTeam. Needs rbac.group.create."""
    createGroup(input: CreateGroupInput!, idempotencyKey: String): Group!
    """Edit a group's name/description (rbac-service PATCH /groups/{id}) — only
    those two fields are editable; groupType is fixed at creation. Only provided
    fields change. Needs rbac.group.update."""
    updateGroup(input: UpdateGroupInput!, idempotencyKey: String): Group!

    """Bulk add/remove group members (rbac-service POST
    /groups/{id}/members:bulk, ≤500 ops). Returns the route's REAL per-entry
    partial-failure report — one bad entry never poisons the batch. Needs
    rbac.group.assign."""
    bulkGroupMembership(groupId: ID!, operations: [GroupMemberOpInput!]!, idempotencyKey: String): BulkGroupMembershipResult!

    """Create a custom role (rbac-service POST /roles, 201). Needs rbac.role.create."""
    createRole(input: CreateRoleInput!, idempotencyKey: String): Role!
    """Rename a custom role (rbac-service PATCH /roles/{id} — rename ONLY;
    actions change via setRoleActions). System roles reject every mutation with
    409 SYSTEM_IMMUTABLE, surfaced verbatim. Needs rbac.role.update."""
    renameRole(id: ID!, name: String!, idempotencyKey: String): Role!
    """Edit a custom role's name and/or action set in one atomic call
    (rbac-service PATCH /roles/{id}). Both input fields are optional. System
    roles reject with 409 SYSTEM_IMMUTABLE, surfaced verbatim. Needs
    rbac.role.update."""
    updateRole(id: ID!, input: UpdateRoleInput!, idempotencyKey: String): Role!
    """Replace a custom role's action set (rbac-service PUT /roles/{id}/actions).
    System roles 409 SYSTEM_IMMUTABLE. Needs rbac.role.update."""
    setRoleActions(id: ID!, actions: [String!]!, idempotencyKey: String): Role!
    """Delete a custom role (rbac-service DELETE /roles/{id}, 204). System roles
    409 SYSTEM_IMMUTABLE. Needs rbac.role.delete."""
    deleteRole(id: ID!): Boolean!

    """Create a content grant (rbac-service POST /grants, 201 — the wire body
    nests the subject as {subject:{type,id}}). Needs rbac.grant.create."""
    createContentGrant(input: CreateContentGrantInput!, idempotencyKey: String): ContentGrant!
    """Delete a content grant (rbac-service DELETE /grants/{id}, 204). Needs
    rbac.grant.delete."""
    deleteContentGrant(id: ID!): Boolean!

    """Create a budget (usage-service POST /budgets). Needs usage.budget.create. Admin only."""
    createBudget(input: CreateBudgetInput!, idempotencyKey: String): Budget!
    """Update a budget's limit/degrade action (usage-service PATCH /budgets/{id}).
    Needs usage.budget.update. Admin only."""
    updateBudget(id: ID!, input: UpdateBudgetInput!, idempotencyKey: String): Budget!
    """Delete a budget (usage-service DELETE /budgets/{id}). Needs usage.budget.delete. Admin only."""
    deleteBudget(id: ID!): Boolean!
    """Create a draft rate card (usage-service POST /rate-cards). Platform-only —
    needs usage.ratecard.create AND a platform-operator token; a tenant admin
    token gets a real 403, never faked."""
    createRateCard(input: CreateRateCardInput!, idempotencyKey: String): RateCard!
    """Activate a rate card (usage-service POST /rate-cards/{id}/activate).
    Platform-only — needs usage.ratecard.update AND a platform-operator token."""
    activateRateCard(id: ID!): RateCard!

    """Dismiss a detected spend anomaly (usage-service POST
    /anomalies/{id}/dismiss). Needs usage.anomaly.update."""
    dismissAnomaly(id: ID!): Anomaly!

    """
    Verify chain integrity for one tenant-day (audit-service POST
    /audit/verify) — the real pass/fail hash-chain replay against the sealed
    manifest. \`date\` is YYYY-MM-DD. 409s (surfaced verbatim) if the day
    isn't sealed yet — never a fake result. Needs audit.chain.execute.
    """
    verifyChainIntegrity(date: String!, tenantId: String): ChainVerifyResult!

    """Generate a SOC2 compliance pack (audit-service POST /compliance/soc2-
    pack, 202 async). \`from\`/\`to\` are RFC3339. Poll via
    complianceOperation(id) for the download link. Needs audit.compliance.read."""
    generateSoc2Pack(from: DateTime!, to: DateTime!): ComplianceJob!

    """Generate an AI decision log compliance pack (audit-service POST
    /compliance/ai-decision-log, 202 async). \`agentId\` optionally scopes to
    one agent. Needs audit.compliance.read."""
    generateAiDecisionLog(from: DateTime!, to: DateTime!, agentId: String): ComplianceJob!

    """Create a worklist of cases from query/dataset rows (case-service POST
    /cases). Each row becomes one case anchored to (datasetUrn, rowPk); cases
    are dedup-keyed on that pair so re-running a worklist records a recurrence
    rather than a duplicate. \`queryUrn\`/\`dashboardUrn\` are provenance. Returns
    the created cases plus the rows that deduplicated to an existing case.
    Needs case.case.create."""
    createCases(input: CreateCasesInput!, idempotencyKey: String): CreateCasesResult!

    """Update a case (case-service PATCH /cases/{id}). Returns the full resource."""
    updateCase(id: ID!, patch: CasePatchInput!, idempotencyKey: String!): Case!

    """Bulk-assign cases to a user (case-service POST /cases/bulk, operation=
    "assign"). ID-based path only (≤500 ids per call, UI caps selection lower);
    server.go's filter-based async path is not wired here. Real partial-failure
    result — never a blind success."""
    bulkAssignCases(caseIds: [ID!]!, assigneeId: ID!, idempotencyKey: String): BulkCaseResult!

    # ---- Tier 4b: case ops (case-service lifecycle/comments/export/catalog) ---
    """Assign or reassign a case (case-service POST /cases/{id}/assign). Legal
    from unassigned (→ draft) or draft/in_progress (reassign); an illegal
    from-state is the service's real 409 INVALID_TRANSITION. Returns the full
    caseView. Needs case.case.assign."""
    assignCase(id: ID!, assigneeId: ID!, idempotencyKey: String): Case!
    """Unassign a case (case-service POST /cases/{id}/unassign) —
    draft|in_progress → unassigned. Needs case.case.assign."""
    unassignCase(id: ID!): Case!
    """Start work on a case (case-service POST /cases/{id}/start) — draft →
    in_progress. Needs case.case.execute."""
    startCase(id: ID!): Case!
    """Resolve a case (case-service POST /cases/{id}/resolve) — in_progress →
    resolved. Requires an ACTIVE disposition; 422 DISPOSITION_REQUIRED /
    DISPOSITION_NOTE_REQUIRED surface verbatim when the catalog entry demands
    a note. Needs case.case.update."""
    resolveCase(id: ID!, dispositionId: ID!, resolutionNote: String, idempotencyKey: String): Case!
    """Reopen a resolved case (case-service POST /cases/{id}/reopen) — resolved
    → in_progress, only within 30 days of resolvedAt. Needs case.case.update."""
    reopenCase(id: ID!): Case!
    """Close a resolved case (case-service POST /cases/{id}/close) — resolved →
    closed, TERMINAL. Needs case.case.update."""
    closeCase(id: ID!): Case!
    """Escalate a case (case-service POST /cases/{id}/escalate) — bumps severity
    one level, status unchanged; \`to\`/\`reason\` both optional. Needs
    case.case.update."""
    escalateCase(id: ID!, to: String, reason: String): Case!

    """Add a comment (case-service POST /cases/{id}/comments, 201; body 1..8192
    bytes). The returned body is the ONLY chance to read it — there is no list-
    comments route (see CaseComment). Needs case.case.update."""
    addCaseComment(caseId: ID!, body: String!, idempotencyKey: String): CaseComment!
    """Edit an own comment within 15 min (case-service PATCH /comments/{id};
    403 otherwise). The route echoes ONLY {id, body}, so every other CaseComment
    field resolves null here — the BFF never fabricates what the downstream
    didn't return. Needs case.case.update."""
    updateCaseComment(id: ID!, body: String!): CaseComment!
    """Delete an own comment within 15 min (case-service DELETE /comments/{id},
    204; 403 otherwise). Needs case.case.update."""
    deleteCaseComment(id: ID!): Boolean!

    """Start an async CSV export (case-service POST /cases/export, 202; max 5
    concurrent per tenant → real 429). Only the \`status\` filter key is honoured
    by the export worker — other keys are ignored downstream. After the 202 the
    BFF immediately re-reads GET /operations/{id} so the returned status is the
    operation's REAL state, never a fabricated "running". Needs case.case.export
    (+ a workspace_id claim)."""
    exportCases(filter: JSON, format: String): CaseOperation!

    """Create a disposition catalog entry (case-service POST /dispositions, 201;
    duplicate code → 409). Needs case.disposition.create."""
    createDisposition(input: CreateDispositionInput!, idempotencyKey: String): Disposition!
    """Update a disposition (case-service PATCH /dispositions/{id}). Needs
    case.disposition.update."""
    updateDisposition(id: ID!, input: UpdateDispositionInput!): Disposition!

    """Create a custom case-field config (case-service POST /case-fields, 201).
    Needs case.case.update."""
    createCaseTrigger(input: CreateCaseTriggerInput!, idempotencyKey: String): CaseTrigger!
    updateCaseTrigger(input: UpdateCaseTriggerInput!): CaseTrigger!
    deleteCaseTrigger(id: ID!): Boolean!
    createCaseField(input: CreateCaseFieldInput!, idempotencyKey: String): CaseField!
    """Edit a custom case-field config (case-service PATCH /case-fields/{id}) —
    purpose + fieldMeta only; name/dataType/queryUrn are immutable and the service
    rejects any change to them. Needs case.case.update."""
    updateCaseField(input: UpdateCaseFieldInput!): CaseField!
    """Delete a case-field config (case-service DELETE /case-fields/{id}, 204).
    409 FIELD_IN_USE when open cases carry values and \`orphan\` isn't set —
    retry with orphan: true to strand those values deliberately. Needs
    case.case.update."""
    deleteCaseField(id: ID!, orphan: Boolean): Boolean!
    """Register a typed case schema (case-service POST /case-schemas, 201;
    idempotent by schemaKey within the workspace). Needs case.schema.create."""
    createCaseSchema(input: CreateCaseSchemaInput!): CaseSchema!
    "Remove a typed case schema (case-service DELETE /case-schemas/{key}). Needs case.schema.delete."
    deleteCaseSchema(schemaKey: ID!): Boolean!

    """Replace the workspace SLA policy (case-service PUT /sla-policy). Only
    non-zero/non-empty fields override the platform defaults (24h warn,
    auto_unassign, 3 reassigns). Write-only downstream — there is no GET to
    read the current policy back. Needs case.case.admin."""
    putCaseSlaPolicy(input: CaseSlaPolicyInput!): CaseSlaPolicy!

    """Decide a proposal (agent-runtime POST /proposals/{id}/decide). Idempotent, first-wins."""
    decideProposal(id: ID!, decision: DecisionInput!, idempotencyKey: String!): Proposal!

    """Create a data-source connection (ingestion-service POST /connections). Secrets are Vault-backed."""
    createConnection(input: CreateConnectionInput!, idempotencyKey: String!): DataConnection!
    """
    Test a connection (ingestion-service). Pass an id to probe a saved connection,
    or type+config(+secrets) to probe an unsaved config (the create-flow Test button).
    Returns OK or a categorized failure (AUTH_FAILED, SOURCE_UNREACHABLE, ...).
    """
    testConnection(id: ID, type: String, config: JSON, secrets: JSON): ConnectionTestResult!

    """Enqueue a decision write-back (ingestion-service POST /writebacks, 201).
    Idempotent by (connectionId, idempotencyKey) — re-submitting the same key
    returns the existing job rather than duplicating. Needs ingestion.writeback.create."""
    createWriteback(input: CreateWritebackInput!, idempotencyKey: String!): Writeback!
    """Approve a pending write-back (ingestion-service POST /writebacks/{id}/approve).
    422s if the caller is the same principal as the original requester — four-eyes
    is enforced server-side, not just hidden in the UI. Needs ingestion.writeback.approve."""
    approveWriteback(id: ID!): Writeback!
    """Reject a pending write-back (same four-eyes gate as approve — needs
    ingestion.writeback.approve)."""
    rejectWriteback(id: ID!): Writeback!
    """Retry a failed/stranded write-back delivery. Needs ingestion.writeback.execute."""
    retryWriteback(id: ID!): Writeback!
    """Delete a data-source connection (ingestion-service DELETE /connections/{id})."""
    deleteConnection(id: ID!): Boolean!

    """Edit a saved connection (ingestion-service PATCH /connections/{id}).
    Secrets merge write-only; a config/secret change live-probes the source
    unless input.skipTest. Needs ingestion.connection.update."""
    updateConnection(id: ID!, input: UpdateConnectionInput!): DataConnection!

    """
    Create + kick off an ingestion run (ingestion-service POST /ingestions).
    Lands source data as a real dataset version. Needs ingestion.ingestion.create.
    """
    createIngestion(input: CreateIngestionInput!, idempotencyKey: String): Ingestion!

    """Cancel an uncommitted ingestion run (ingestion-service POST
    /ingestions/{id}/cancel; 409 once committed/terminal). Needs
    ingestion.ingestion.execute."""
    cancelIngestion(id: ID!): Ingestion!
    """Retry a FAILED ingestion as a fresh cloned run (ingestion-service POST
    /ingestions/{id}/retry, 202; 409 for any other status). Returns the NEW run.
    Needs ingestion.ingestion.execute."""
    retryIngestion(id: ID!): Ingestion!
    """Re-run a TERMINAL ingestion's config as a new job (ingestion-service POST
    /ingestions/{id}/reingest, 202; 409 while still active). Returns the NEW
    run. Needs ingestion.ingestion.create."""
    reingestIngestion(id: ID!): Ingestion!

    """Create a recurring ingestion schedule (ingestion-service POST /schedules).
    Exactly one of cron/intervalSeconds. Needs ingestion.schedule.create."""
    createIngestionSchedule(input: CreateIngestionScheduleInput!, idempotencyKey: String): IngestionSchedule!
    """Edit a schedule (ingestion-service PATCH /schedules/{id}). Needs
    ingestion.schedule.update."""
    updateIngestionSchedule(id: ID!, input: UpdateIngestionScheduleInput!): IngestionSchedule!
    """Delete a schedule (ingestion-service DELETE /schedules/{id}, 204). Needs
    ingestion.schedule.delete."""
    deleteIngestionSchedule(id: ID!): Boolean!
    """Pause a schedule (ingestion-service POST /schedules/{id}/pause). Needs
    ingestion.schedule.update."""
    pauseIngestionSchedule(id: ID!): IngestionSchedule!
    """Resume a paused schedule (ingestion-service POST /schedules/{id}/resume)."""
    resumeIngestionSchedule(id: ID!): IngestionSchedule!
    """Force one immediate fire (ingestion-service POST /schedules/{id}/run_now;
    409 when the schedule is disabled/deleted). Needs ingestion.schedule.execute."""
    runIngestionScheduleNow(id: ID!): ScheduleRunNowResult!

    """
    Create a resumable upload session against an existing file_upload Ingestion
    (ingestion-service POST /uploads). Returns the part size the caller must
    chunk at and a part-count-free session the browser then PUTs binary chunks
    to directly via /api/uploads/{uploadId}/parts/{n} (NOT through GraphQL).
    Needs ingestion.upload.create.
    """
    createUpload(input: CreateUploadInput!, idempotencyKey: String): Upload!

    """
    Finalize an upload once every part has been PUT and confirmed
    (ingestion-service POST /uploads/{id}/complete). Transitions the owning
    Ingestion to queued/running and returns it. Needs ingestion.upload.execute.
    """
    completeUpload(uploadId: ID!, input: CompleteUploadInput!): Ingestion!

    """
    Run ad-hoc SQL and return the first page of results in one round trip
    (query-service POST /sql/run sync, then GET /executions/{id}/results). The
    statement must classify read-only. Needs query.execution.execute.
    """
    runSql(input: RunSqlInput!): QueryResult!
    """
    Run a saved query by id and return its first page of results (query-service
    POST /queries/{id}/run sync). Needs query.execution.execute.
    """
    runSavedQuery(id: ID!, limit: Int): QueryResult!

    """
    Create a saved query (query-service POST /queries). name, moduleNames (≥1)
    and read-only sqlText are required by the service; every :placeholder must
    have a typed declaration (422 with per-variable details otherwise). The
    owning workspace comes from the caller's JWT workspace_id claim when
    present. Needs query.query.create.
    """
    createSavedQuery(input: SavedQueryInput!, idempotencyKey: String): SavedQuery!
    """
    Update a saved query (query-service PATCH /queries/{id}). Every update
    creates an immutable new version. Needs query.query.update.
    """
    updateSavedQuery(id: ID!, input: SavedQueryInput!): SavedQuery!
    """Soft-delete a saved query (query-service DELETE /queries/{id}, 204).
    Execution history rows persist. Needs query.query.delete."""
    deleteSavedQuery(id: ID!): Boolean!
    """Cancel a queued/running execution (query-service POST
    /executions/{id}/cancel). Needs query.execution.execute."""
    cancelQueryExecution(id: ID!): QueryExecution!

    """
    Archive a dataset (dataset-service DELETE /datasets/{id}, sets deleted_at;
    200 with a small summary, not 204). dataset-service exposes no archived-only
    list read, so this only works when the caller already knows the id (e.g.
    from the audit trail) — see the honest gap noted on the Archive admin screen.
    \`force\` skips the downstream-consumer guard. Needs dataset.dataset.delete.
    """
    archiveDataset(id: ID!, force: Boolean): Boolean!
    """
    Restore an archived dataset (dataset-service POST /datasets/{id}/restore).
    Renames to "Copy of <name>" on a collision (V1-compatible behavior) and only
    works within the service's restore window. Needs dataset.dataset.update.
    """
    restoreDataset(id: ID!): Dataset!

    """
    Edit a dataset's catalog metadata — rename and/or change its description
    (dataset-service PATCH /datasets/{id}). Datasets are created via ingestion
    (no create dialog), so this is the tenant's only path to correct a name or
    description after the fact. Both fields are optional; the backend rejects a
    rename that collides with another dataset in the same workspace (409).
    Needs dataset.dataset.update.
    """
    updateDataset(id: ID!, input: UpdateDatasetInput!): Dataset!

    """
    Manually trigger a re-profile of a dataset version (dataset-service POST
    /datasets/{id}/versions/{n}/profile, 202 async). versionNo omitted =
    current version. Needs dataset.profile.execute.
    """
    reprofileDataset(id: ID!, versionNo: Int, idempotencyKey: String): ReprofileResult!

    """
    Create a dashboard (chart-service POST /dashboards). The owning workspace is
    taken from the caller's JWT \`workspace_id\` claim (the backend takes tenant
    from the token), so it is not part of the input.
    """
    createDashboard(input: CreateDashboardInput!, idempotencyKey: String): Dashboard!
    """Update a dashboard (chart-service PATCH /dashboards/{id}). Returns the resource."""
    updateDashboard(id: ID!, input: UpdateDashboardInput!, idempotencyKey: String): Dashboard!
    """Delete a dashboard (chart-service DELETE /dashboards/{id})."""
    deleteDashboard(id: ID!): Boolean!
    """Archive a dashboard (chart-service POST /dashboards/{id}/archive). Needs
    chart.dashboard.update (archive/restore are authorized as the canonical update)."""
    archiveDashboard(id: ID!): Dashboard!
    """Restore an archived dashboard (chart-service PATCH /dashboards/{id}/restore)."""
    restoreDashboard(id: ID!): Dashboard!

    """
    Subscribe a dashboard to a scheduled email digest (notification-service
    POST /reports, NOTIF-FR-060). The subscription's workspace is taken from
    the target dashboard itself (chart-service), not the caller's claim, since
    a subscribed dashboard need not live in the caller's own default workspace.
    """
    createReportSubscription(input: CreateReportSubscriptionInput!, idempotencyKey: String): ReportSubscription!
    """Update a report subscription (notification-service PATCH /reports/{id})."""
    updateReportSubscription(id: ID!, input: UpdateReportSubscriptionInput!, idempotencyKey: String): ReportSubscription!
    """Delete a report subscription (notification-service DELETE /reports/{id});
    also deletes its underlying Temporal Schedule."""
    deleteReportSubscription(id: ID!): Boolean!
    """Pause or resume a report subscription's Temporal Schedule without deleting
    it (notification-service PATCH /reports/{id} enabled=!paused)."""
    pauseReportSubscription(id: ID!, paused: Boolean!): ReportSubscription!
    """Fire one immediate send outside the cron cadence (notification-service
    POST /reports/{id}/trigger) — the real Temporal \`Schedule.Trigger\` API."""
    triggerReportSubscription(id: ID!): Boolean!

    """Create a chart on a dashboard (chart-service POST /dashboards/{id}/charts)."""
    createChart(input: CreateChartInput!, idempotencyKey: String): Chart!
    """Update a chart (chart-service PATCH /charts/{id}). Returns the resource."""
    updateChart(id: ID!, input: UpdateChartInput!, idempotencyKey: String): Chart!
    """Delete a chart (chart-service DELETE /charts/{id})."""
    deleteChart(id: ID!): Boolean!

    """
    Create a pipeline template (pipeline-orchestrator POST /pipelines). The owning
    workspace is taken from the caller's JWT \`workspace_id\` claim (as the backend
    takes tenant from the token), so it is not part of the input.
    """
    createPipeline(input: CreatePipelineInput!, idempotencyKey: String): PipelineTemplate!
    """
    Update a pipeline template (pipeline-orchestrator PUT /pipelines/{id}). Mints a
    new immutable version from the edited definition. Needs pipeline.template.update.
    """
    updatePipeline(id: ID!, input: UpdatePipelineInput!, idempotencyKey: String): PipelineTemplate!
    """
    Validate a pipeline definition (pipeline-orchestrator POST /pipelines/validate).
    Returns a report; an invalid definition is a normal { valid:false, issues } result,
    not an error.
    """
    validatePipeline(definition: JSON!, pipelineType: String!): PipelineValidationResult!
    """Submit a pipeline run (pipeline-orchestrator POST /pipelines/{id}/run, 202)."""
    runPipeline(id: ID!, input: RunPipelineInput, idempotencyKey: String): PipelineRun!

    """Terminate a live pipeline run (pipeline-orchestrator PUT
    /runs/{id}/terminate). A terminal run is an idempotent no-op (BR-6). Needs
    pipeline.run.execute."""
    terminatePipelineRun(id: ID!): PipelineRun!
    """Retry a FAILED pipeline run (pipeline-orchestrator POST /runs/{id}/retry,
    202; 409 for any other status). Returns the NEW run. Needs pipeline.run.create."""
    retryPipelineRun(id: ID!, idempotencyKey: String): PipelineRun!
    """Clone a template into a new one (pipeline-orchestrator POST
    /pipelines/{id}/clone, 201). Needs pipeline.template.create."""
    clonePipelineTemplate(id: ID!, idempotencyKey: String): PipelineTemplate!
    """Set a template's active version (pipeline-orchestrator POST
    /pipelines/{id}/versions/{versionId}/activate). Needs pipeline.template.update."""
    activatePipelineTemplateVersion(templateId: ID!, versionId: ID!): PipelineTemplate!
    """Compile a template's active version to an Argo manifest
    (pipeline-orchestrator POST /pipelines/{id}/compile). Needs pipeline.template.execute."""
    compilePipelineTemplate(id: ID!): CompiledPipelineManifest!
    """Archive a template (pipeline-orchestrator DELETE /pipelines/{id}; 409 for
    system-owned templates). Soft-delete — restorable. Needs pipeline.template.delete."""
    deletePipelineTemplate(id: ID!): PipelineTemplate!
    """Restore an archived template (pipeline-orchestrator PATCH
    /pipelines/{id}/restore). Needs pipeline.template.update."""
    restorePipelineTemplate(id: ID!): PipelineTemplate!

    """Create a recurring pipeline schedule (pipeline-orchestrator POST
    /pipeline-schedules, 201). Needs pipeline.schedule.create."""
    createPipelineSchedule(input: CreatePipelineScheduleInput!, idempotencyKey: String): PipelineSchedule!
    """Pause a schedule (pipeline-orchestrator POST /pipeline-schedules/{id}/pause).
    Needs pipeline.schedule.update."""
    pausePipelineSchedule(id: ID!): PipelineSchedule!
    """Resume a schedule (pipeline-orchestrator POST /pipeline-schedules/{id}/resume).
    Needs pipeline.schedule.update."""
    resumePipelineSchedule(id: ID!): PipelineSchedule!
    """Force one fire now (pipeline-orchestrator POST
    /pipeline-schedules/{id}/run-now, 202). Returns the newly created run. Needs
    pipeline.schedule.execute."""
    runNowPipelineSchedule(id: ID!): PipelineRun!
    """Delete a schedule (pipeline-orchestrator DELETE /pipeline-schedules/{id},
    204). Needs pipeline.schedule.delete."""
    deletePipelineSchedule(id: ID!): Boolean!

    """
    Create an ML experiment (experiment-service POST /experiments, 201). The owning
    workspace is taken from the caller's JWT \`workspace_id\` claim (as the backend
    takes tenant from the same token), so it is not part of the input. Needs
    experiment.experiment.create.
    """
    createExperiment(input: CreateExperimentInput!, idempotencyKey: String): Experiment!

    """Archive an experiment (experiment-service DELETE /experiments/{id}, sets
    deleted_at). Needs experiment.experiment.delete."""
    archiveExperiment(id: ID!): Experiment!
    """Restore an archived experiment (experiment-service PATCH /experiments/{id}/restore).
    Needs experiment.experiment.update."""
    restoreExperiment(id: ID!): Experiment!

    """
    Request a model-version stage transition (experiment-service POST
    /models/{id}/versions/{v}/promote, 202). This only OPENS a pending promotion;
    a SECOND person must approve it (four-eyes). Needs experiment.model.update.
    """
    promoteModelVersion(
      modelId: ID!
      version: Int!
      targetStage: String!
      rationale: String
      idempotencyKey: String
    ): PromotionRequest!

    """
    Decide a pending promotion (experiment-service POST /promotions/{id}/decision).
    \`decision\` is approve | reject. The service FORBIDS self-approval — the same
    user who requested the promotion cannot approve it (four-eyes). Needs
    experiment.promotion.approve.
    """
    decidePromotion(promotionId: ID!, decision: String!, message: String): JSON!

    """
    Submit a batch inference job (inference-service POST /inferences, 202). A
    compatibility failure is a real 422, surfaced verbatim (no fake job). Needs
    inference.job.create.
    """
    createInferenceJob(input: CreateInferenceJobInput!, idempotencyKey: String): InferenceJob!

    # ---- Tier 4b: ml ops (experiment-service registration/notes/cards + ------
    # ---- inference-service job lifecycle/validate/schedules) -----------------
    """
    Register a FINISHED run as a model version (experiment-service POST
    /experiments/{eid}/runs/{rid}/register, 201, idempotent). A not-yet-finished
    run answers RunNotFinished (409/422) and a name registered under a different
    model_type answers ModelTypeMismatch — both surface verbatim. Needs
    experiment.model.create.
    """
    registerRunAsModel(experimentId: ID!, runId: ID!, input: RegisterRunInput!, idempotencyKey: String): RegisterModelResult!
    """Edit an experiment's name/description/note (experiment-service PATCH
    /experiments/{id}; omitted fields unchanged). Needs experiment.experiment.update."""
    updateExperiment(id: ID!, input: UpdateExperimentInput!): Experiment!
    """Set/replace the run's note (experiment-service PUT /runs/{id}/note —
    upsert). Needs experiment.run.update."""
    upsertRunNote(runId: ID!, description: String!): RunNote!
    """Delete the run's note (experiment-service DELETE /runs/{id}/note). Needs
    experiment.run.update."""
    deleteRunNote(runId: ID!): Boolean!
    """
    Update the human overlay of a model card (experiment-service PATCH
    /models/{id}/versions/{v}/card — any subset of the 4 overlay fields; auto
    fields are service-owned and not writable here). Answers the full merged
    card verbatim as JSON. Needs experiment.model_card.update.
    """
    updateModelCard(modelId: ID!, version: Int!, input: ModelCardOverlayInput!): JSON!

    """
    Cancel an inference job (inference-service POST /inferences/{id}/cancel).
    Legal from queued|submitted|running; an already cancelled/cancelling job is
    an idempotent no-op; anything else 409s verbatim. Needs inference.job.update.
    """
    cancelInferenceJob(id: ID!): InferenceJob!
    """
    Retry a terminal-FAILURE job (rejected|failed|cancelled) as a NEW job
    (inference-service POST /inferences/{id}/retry, 202 → GET the new job).
    Returns the NEW job (its retriedFromJobId points back); any other state 409s
    verbatim. Needs inference.job.create.
    """
    retryInferenceJob(id: ID!, idempotencyKey: String): InferenceJob!
    """Delete a TERMINAL job (inference-service DELETE /inferences/{id}, 204;
    409 for non-terminal). Needs inference.job.delete."""
    deleteInferenceJob(id: ID!): Boolean!
    """
    Standalone model×dataset compatibility check (inference-service POST
    /inferences/validate) — read-only preflight; submit re-validates regardless.
    Needs inference.job.read.
    """
    validateInference(input: ValidateInferenceInput!): InferenceCompatibilityReport!
    """
    Submit one model over up to 20 datasets (inference-service POST
    /inferences/bulk). Answers the REAL per-dataset result list verbatim:
    [{input_dataset_urn, job_id, status} | {input_dataset_urn, error: {code,
    message}}] — partial failure is per-entry, never a blind success. Needs
    inference.job.create.
    """
    bulkCreateInferenceJobs(input: BulkCreateInferenceInput!): JSON!

    """Create a recurring scoring schedule (inference-service POST /schedules,
    201; duplicate name 409, timing/model XOR violations 422 — verbatim). Needs
    inference.schedule.create."""
    createInferenceSchedule(input: CreateInferenceScheduleInput!): InferenceSchedule!
    """Edit a schedule's timing/overlap/selectors/notify flag (inference-service
    PATCH /schedules/{id}); name/model/stage are immutable after creation. Needs
    inference.schedule.update."""
    updateInferenceSchedule(id: ID!, input: UpdateInferenceScheduleInput!): InferenceSchedule!
    """Delete a schedule (inference-service DELETE /schedules/{id}, 204). Needs
    inference.schedule.delete."""
    deleteInferenceSchedule(id: ID!): Boolean!
    """Pause a schedule (inference-service POST /schedules/{id}/pause). Needs
    inference.schedule.update."""
    pauseInferenceSchedule(id: ID!): InferenceSchedule!
    """Resume a schedule — also resets the failure breaker (inference-service
    POST /schedules/{id}/resume). Needs inference.schedule.update."""
    resumeInferenceSchedule(id: ID!): InferenceSchedule!
    """
    Force one immediate fire (inference-service POST /schedules/{id}/trigger,
    202 — authorized as inference.schedule.update; there is no separate execute
    action). Answers the REAL fire result verbatim: {fired: true, job_id,
    status} or {fired: false, reason, error?}.
    """
    triggerInferenceSchedule(id: ID!): JSON!

    # ---- semantic model authoring (semantic-service) -------------------------
    """Create a semantic model + open its draft v1 (semantic-service POST /models,
    201). Needs semantic.model.create."""
    createSemanticModel(input: CreateSemanticModelInput!, idempotencyKey: String): SemanticModelSummary!
    """Patch a model's name/description (semantic-service PATCH /models/{id}).
    Needs semantic.model.update."""
    updateSemanticModel(id: ID!, input: UpdateSemanticModelInput!): SemanticModelSummary!
    """Delete a semantic model (semantic-service DELETE /models/{id}). Needs
    semantic.model.delete."""
    deleteSemanticModel(id: ID!): Boolean!

    """Open a new draft version from the published one (semantic-service POST
    /models/{id}/versions, 201); 409 if a draft/in_review version is already
    open. Needs semantic.model.update."""
    createSemanticModelVersion(modelId: ID!, idempotencyKey: String): SemanticModelVersion!
    """
    Replace a DRAFT version's definition (semantic-service PATCH
    /models/{id}/versions/{version_no}) — the editor's save call. Runs real
    structural/expression validation immediately (SEM-FR-006); a bad expr or
    malformed field 422s with VALIDATION_FAILED (surfaced in the GraphQL error's
    \`details\`) rather than saving. Full binding validation against the dataset's
    real columns runs at submit time (see \`submitSemanticModelVersion\`). Needs
    semantic.model.update.
    """
    updateSemanticModelDraft(modelId: ID!, versionNo: Int!, definition: JSON!): SemanticModelVersion!
    """
    Submit a draft for review (semantic-service POST .../submit): runs FULL
    validation (bindings against the real dataset schema, join graph, limits) and
    422s with VALIDATION_FAILED + a \`details: [{object, problem}]\` list on
    failure — this is the authoritative pre-publish gate. 409 on an illegal state
    transition. Needs semantic.model.update.
    """
    submitSemanticModelVersion(modelId: ID!, versionNo: Int!): SemanticModelVersion!
    """
    Approve an in-review version, publishing it (semantic-service POST
    .../approve). The service FORBIDS self-approval — a 403 PERMISSION_DENIED if
    the caller is the version's own author (SEM-FR-007, four-eyes); the UI should
    hide this control for the author rather than let the click fail. Needs
    semantic.model.approve.
    """
    approveSemanticModelVersion(modelId: ID!, versionNo: Int!, note: String): SemanticModelVersion!
    """
    Reject an in-review version back to \`rejected\` (semantic-service POST
    .../reject). \`note\` is REQUIRED — the service 422s without one. Also forbids
    self-review. Needs semantic.model.approve.
    """
    rejectSemanticModelVersion(modelId: ID!, versionNo: Int!, note: String!): SemanticModelVersion!

    """
    Auto-draft a model's definition from dataset schemas (semantic-service POST
    /models/{id}/bootstrap, 202 async). Returns the operation to poll via
    semanticOperation(id); on completion the model's open draft carries the
    bootstrapped entities/dimensions/measures. Needs semantic.model.update.
    """
    bootstrapSemanticModel(modelId: ID!, sources: JSON, idempotencyKey: String): SemanticOperation!

    """Author a verified NL↔SQL pair as a draft (semantic-service POST
    /verified-queries). The owning workspace comes from the caller's JWT
    workspace_id claim. Needs semantic.verified_query.create."""
    createVerifiedQuery(input: CreateVerifiedQueryInput!, idempotencyKey: String): VerifiedQuery!
    """Edit a draft/rejected pair (semantic-service PATCH /verified-queries/{id};
    409 in any other state; a rejected pair returns to draft). Needs
    semantic.verified_query.update."""
    updateVerifiedQuery(id: ID!, input: UpdateVerifiedQueryInput!): VerifiedQuery!
    """Submit a draft pair for review (semantic-service POST .../submit; draft ->
    pending_review). Needs semantic.verified_query.update."""
    submitVerifiedQuery(id: ID!): VerifiedQuery!
    """
    Approve a pending pair (semantic-service POST .../approve). The service
    FORBIDS self-approval — 403 PERMISSION_DENIED when the caller authored the
    pair (SEM-FR-040, four-eyes); the UI hides this control for the author.
    Needs semantic.verified_query.approve.
    """
    approveVerifiedQuery(id: ID!): VerifiedQuery!
    """Reject a pending pair (semantic-service POST .../reject; also four-eyes).
    Needs semantic.verified_query.approve."""
    rejectVerifiedQuery(id: ID!, note: String): VerifiedQuery!
    """Archive a pair from any state (semantic-service POST .../archive) —
    terminal; archived pairs leave the retrieval index. Needs
    semantic.verified_query.update."""
    archiveVerifiedQuery(id: ID!): VerifiedQuery!

    # ---- kill switches (agent-runtime + tool-plane, emergency stop) ----------
    """
    Set an agent kill switch (agent-runtime POST /registry/kill-switches).
    \`scope\` defaults to "agent_version_tenant" (kill this agent for the
    caller's own tenant only); "agent"/"agent_version" require operator scope
    (platform-wide kill). \`reason\` is REQUIRED. Gated server-side on the
    operator/tenant-admin JWT scope, not an rbac action — agent-runtime's kill
    routes predate the rbac action-catalog convention.
    """
    createAgentKillSwitch(
      agentKey: String!
      scope: String
      version: Int
      tenantId: String
      reason: String!
      idempotencyKey: String
    ): KillSwitch!
    """Lift an agent kill switch (agent-runtime DELETE /registry/kill-switches/{id})."""
    deleteAgentKillSwitch(killId: ID!): KillSwitchLiftResult!

    """
    Set a tool kill switch (tool-plane POST /kill-switches). \`scope\` is one of
    tool | tool_version | tool_tenant. \`reason\` is REQUIRED. Needs
    tool.kill.create; a cross-tenant tool_tenant kill additionally needs
    platform-operator scope.
    """
    createToolKillSwitch(
      toolId: String!
      scope: String!
      version: String
      tenantId: String
      reason: String!
      idempotencyKey: String
    ): KillSwitch!
    """Lift a tool kill switch (tool-plane DELETE /kill-switches/{id}). Needs tool.kill.delete."""
    deleteToolKillSwitch(id: ID!): KillSwitchLiftResult!

    """
    Start a right-to-be-forgotten erasure for a subject (memory-service POST
    /erasure, 202) — a compliance-sensitive, IRREVERSIBLE destructive action.
    \`subjectType\` defaults to "user". Poll the returned operation via the
    \`erasure(id)\` query. Needs memory.erasure.create.
    """
    requestMemoryErasure(subjectId: String!, subjectType: String): ErasureRequest!

    # ---- Tier 2a: eval (eval-service) --------------------------------------
    """Register a new eval suite version (eval-service POST /suites, 201).
    BR-1: the gate rule must reference >=1 deterministic scorer. Needs eval.suite.write."""
    createEvalSuite(input: CreateEvalSuiteInput!): EvalSuite!
    """Edit an eval suite version (eval-service PATCH /suites/{suiteId}, optional
    ?version). suiteId/agentKey are immutable; only provided fields are patched.
    BR-1: the gate rule must still reference >=1 deterministic scorer. Needs eval.suite.write."""
    updateEvalSuite(input: UpdateEvalSuiteInput!): EvalSuite!
    """
    Start a real scoring run — this SYNCHRONOUSLY executes the suite against the
    candidate and returns the completed (or failed/budget-capped) run (eval-service
    POST /runs, 201). Needs eval.run.execute.
    """
    createEvalRun(input: CreateEvalRunInput!): EvalRun!
    """Cancel a running eval run (eval-service POST /runs/{id}/cancel). Needs eval.run.execute."""
    cancelEvalRun(id: ID!): EvalRun!
    """Open a new draft eval dataset (eval-service POST /datasets, 201). Needs eval.dataset.write."""
    createEvalDataset(input: CreateEvalDatasetInput!): EvalDataset!
    """Freeze a dataset version (eval-service POST .../freeze) — requires >=1 active
    case (freeze guard). Needs eval.dataset.write."""
    freezeEvalDataset(datasetKey: String!, version: Int!): EvalDataset!
    """Add a case to the curation queue (eval-service POST /cases, 201). Needs eval.case.curate."""
    createEvalCase(input: CreateEvalCaseInput!): EvalCase!
    """Promote a candidate case to active (eval-service POST .../promote).
    Production-sourced cases require prior attestation (BR-3). Needs eval.case.curate."""
    promoteEvalCase(id: ID!): EvalCase!
    """Attest a case's anonymization (eval-service POST .../attest) — required
    before a production-sourced case can be promoted. Needs eval.case.curate."""
    attestEvalCase(id: ID!, attestedBy: String!): EvalCase!
    """Reject a candidate case (eval-service POST .../reject, sets retired). Needs eval.case.curate."""
    rejectEvalCase(id: ID!): EvalCase!
    """Retire an active case (eval-service POST .../retire). Needs eval.case.curate."""
    retireEvalCase(id: ID!): EvalCase!
    """Edit a case's input/expected/tags/weight/attestation (eval-service PATCH
    /cases/{id}). Blocked on a frozen dataset (copy-on-write required). Needs eval.case.curate."""
    updateEvalCase(id: ID!, patch: EvalCasePatchInput!): EvalCase!
    """Register a new scorer version (eval-service POST /scorers, 201). Needs eval.scorer.admin."""
    createEvalScorer(input: CreateEvalScorerInput!): EvalScorer!
    """Edit a scorer version (eval-service PATCH /scorers/{scorerKey}, optional
    ?version). scorerKey/kind are immutable; only provided fields are patched.
    BR-1: an llm_judge scorer can never become gate-eligible. Needs eval.scorer.admin."""
    updateEvalScorer(input: UpdateEvalScorerInput!): EvalScorer!
    """Activate a scorer version (eval-service POST .../activate) — llm_judge
    scorers are blocked below 0.8 judge-vs-human agreement (EVL-FR-014). Needs eval.scorer.admin."""
    activateEvalScorer(scorerKey: String!, version: Int!): EvalScorer!
    """Start a canary A/B comparison (eval-service POST /canaries, 201). Needs eval.canary.manage."""
    createEvalCanary(input: CreateEvalCanaryInput!): EvalCanary!
    """Ingest paired candidate/baseline scores into a canary (eval-service POST
    .../samples) — recomputes the report; may flip status to ready/failed_early.
    \`pairedScores\` is {scorer: [[candidate, baseline], ...]}. Needs eval.canary.manage."""
    ingestEvalCanarySamples(comparisonId: String!, pairedScores: JSON!): EvalCanary!
    """Stop a collecting canary early (eval-service POST .../stop). Needs eval.canary.manage."""
    stopEvalCanary(comparisonId: String!): EvalCanary!
    """Set SLO alert targets for an agent (eval-service POST /slos/targets). Needs eval.slo.read."""
    setEvalSloTargets(agentKey: String!, agentVersion: String, targets: JSON!): Boolean!

    # ---- Tier 2a: ai-gateway admin ------------------------------------------
    """Register a new provider/deployment (ai-gateway POST /admin/providers, 201).
    Needs ai.provider.write + the platform-operator scope."""
    createAiProvider(input: CreateAiProviderInput!, idempotencyKey: String): AiProviderDeployment!
    """Patch a deployment's status/priority/limits (ai-gateway PATCH /admin/providers/{id}).
    Needs ai.provider.write + the platform-operator scope."""
    patchAiProvider(deploymentId: ID!, input: PatchAiProviderInput!, force: Boolean): AiProviderDeployment!
    """Drain a deployment (stop routing new traffic to it; ai-gateway POST
    .../drain). Needs ai.provider.write + the platform-operator scope."""
    drainAiProvider(deploymentId: ID!, force: Boolean): AiProviderDeployment!
    """Replace a request class's routing ladder (ai-gateway PUT /admin/ladders/{class}).
    \`scope\` platform requires the platform-operator scope; tenant needs only ai.ladder.write."""
    putAiLadder(requestClass: String!, rungs: JSON!, maxRung: Int, scope: String): AiModelLadder!
    """Create an ai-gateway LLM-spend budget (ai-gateway POST /admin/budgets, 201).
    A platform-scoped budget additionally needs the platform-operator scope. Needs ai.budget.write."""
    createAiBudget(input: CreateAiBudgetInput!, idempotencyKey: String): AiBudget!
    """Patch an ai-gateway budget's limit/degrade-threshold/status (ai-gateway
    PATCH /admin/budgets/{id}). Needs ai.budget.write."""
    updateAiBudget(id: ID!, input: PatchAiBudgetInput!): AiBudget!
    """Disable an ai-gateway budget (ai-gateway DELETE /admin/budgets/{id}, soft-delete). Needs ai.budget.write."""
    deleteAiBudget(id: ID!): AiBudget!
    """Issue a new virtual API key (ai-gateway POST /admin/keys, 201) — the
    returned \`secret\` is shown ONCE and never retrievable again. Needs ai.key.write."""
    createAiVirtualKey(input: CreateAiVirtualKeyInput!, idempotencyKey: String): AiVirtualKey!
    """Revoke a virtual key (ai-gateway POST .../revoke). Needs ai.key.write."""
    revokeAiVirtualKey(id: ID!): AiVirtualKey!
    """Rotate a virtual key (ai-gateway POST .../rotate) — issues a new secret,
    shown ONCE, and invalidates the old one. Needs ai.key.write."""
    rotateAiVirtualKey(id: ID!): AiVirtualKey!
    """Replace the tenant's guardrail policy (ai-gateway PUT /admin/guardrails).
    Disabling PII redaction (pii.mode=off) requires the platform-operator scope. Needs ai.guardrail.write."""
    putAiGuardrailPolicy(policy: JSON!): AiGuardrailPolicy!

    # ---- Tier 2b: notification-service ---------------------------------------
    """Mark one inbox notification read (POST /notifications/{id}/read, 204).
    Needs notification.inbox.read."""
    markNotificationRead(id: ID!): Boolean!
    """Mark one inbox notification unread (POST /notifications/{id}/unread, 204)."""
    markNotificationUnread(id: ID!): Boolean!
    """Mark every inbox notification read; returns the number marked
    (POST /notifications/mark-all-read)."""
    markAllNotificationsRead: Int!
    """Replace the caller's notification preferences (PUT /preferences). Needs
    notification.preference.update."""
    updateNotificationPreferences(input: NotificationPreferencesInput!, idempotencyKey: String): NotificationPreferences!
    """Create a subscription rule (POST /rules, 201). Needs notification.rule.create."""
    createNotificationRule(input: NotificationRuleInput!, idempotencyKey: String): NotificationRule!
    """Patch a subscription rule (PATCH /rules/{id}). Needs notification.rule.update."""
    updateNotificationRule(id: ID!, input: NotificationRuleInput!, idempotencyKey: String): NotificationRule!
    """Delete a subscription rule (DELETE /rules/{id}, 204). Needs notification.rule.delete."""
    deleteNotificationRule(id: ID!): Boolean!
    """Register a webhook endpoint (POST /webhooks, 201). The service performs a
    REAL challenge handshake against the URL before persisting — an unreachable
    endpoint fails with VERIFY_FAILED, never a fake success. The response carries
    the signing secret (v1) — surface it ONCE. Needs notification.webhook.create."""
    createNotificationWebhook(input: CreateWebhookInput!, idempotencyKey: String): WebhookEndpoint!
    """Patch a webhook's url/event types/active flag (PATCH /webhooks/{id}).
    Needs notification.webhook.update."""
    updateNotificationWebhook(id: ID!, input: UpdateWebhookInput!, idempotencyKey: String): WebhookEndpoint!
    """Delete a webhook endpoint (DELETE /webhooks/{id}, 204). Needs
    notification.webhook.delete."""
    deleteNotificationWebhook(id: ID!): Boolean!
    """Rotate a webhook's signing secret (POST /webhooks/{id}/rotate-secret) —
    the prior secret stays valid for 24h (NOTIF-FR-022 AC-6). The new secret is
    in the response — surface it ONCE. Needs notification.webhook.update."""
    rotateNotificationWebhookSecret(id: ID!, idempotencyKey: String): WebhookEndpoint!
    """Requeue one webhook delivery for immediate re-send (POST
    /webhooks/{id}/deliveries/{did}/redeliver, 202). Needs notification.webhook.execute."""
    redeliverNotificationWebhookDelivery(webhookId: ID!, deliveryId: ID!, idempotencyKey: String): Boolean!
    """Create a draft template version (POST /templates, 201) — referenced
    variables are validated against the event type's whitelist. Needs
    notification.template.create."""
    createNotificationTemplate(input: CreateNotificationTemplateInput!, idempotencyKey: String): NotificationTemplate!
    """Publish a draft template version (POST /templates/{key}/publish). Needs
    notification.template.update."""
    publishNotificationTemplate(key: String!, templateId: ID!, idempotencyKey: String): NotificationTemplate!
    """Render a template against a sample event (POST /templates/{key}/preview)
    — a REAL render through the runtime pipeline, no fake preview. Needs
    notification.template.read."""
    previewNotificationTemplate(key: String!, channel: String, locale: String, sampleEvent: JSON): NotificationTemplatePreview!
    """Clear an email suppression (DELETE /admin/suppressions?email_hash=…, 204).
    Needs notification.suppression.delete."""
    clearEmailSuppression(emailHash: String!): Boolean!

    # ---- Tier 2b: tool-plane registry admin ----------------------------------
    """Register a catalog tool (tool-plane POST /tools, 201). Needs tool.tool.create."""
    registerTool(input: RegisterToolInput!, idempotencyKey: String): Tool!
    """Create a draft tool version (POST /tools/{id}/versions, 201). Needs tool.tool.update."""
    addToolVersion(toolId: ID!, input: AddToolVersionInput!, idempotencyKey: String): ToolVersion!
    """Publish a draft version (POST .../publish) — validates the schema and
    computes the REAL discovery embedding first (AC-7). Needs tool.tool.update."""
    publishToolVersion(toolId: ID!, version: String!, idempotencyKey: String): ToolVersion!
    """Deprecate a published version (POST .../deprecate; window ≥30d, default
    90d). Needs tool.tool.update."""
    deprecateToolVersion(toolId: ID!, version: String!, deprecationEndsAt: DateTime, idempotencyKey: String): ToolVersionLifecycleResult!
    """Retire a version (POST .../retire) — requires the deprecation window to
    have elapsed OR force with a reason. Needs tool.tool.delete."""
    retireToolVersion(toolId: ID!, version: String!, force: Boolean, reason: String, idempotencyKey: String): ToolVersionLifecycleResult!
    """Upsert the caller-tenant's enablement for a tool (PUT
    /tenants/self/tools/{id}). BR-2: a destructive tool can never be enabled at
    write-direct. Needs tool.enablement.update."""
    setToolEnablement(toolId: ID!, input: SetToolEnablementInput!, idempotencyKey: String): TenantToolSettings!
    """Submit an external tool for onboarding review (POST /byo, 201) —
    write-direct/admin tiers are forbidden for external tools. Needs tool.byo.create."""
    submitByoTool(input: SubmitByoToolInput!, idempotencyKey: String): ByoSubmission!
    """Approve a pending BYO submission (POST /byo/{id}/approve). Needs tool.byo.approve."""
    approveByoTool(id: ID!, message: String, idempotencyKey: String): ByoDecision!
    """Reject a pending BYO submission (POST /byo/{id}/reject). Needs tool.byo.approve."""
    rejectByoTool(id: ID!, message: String, idempotencyKey: String): ByoDecision!

    # ---- Tier 2b: agent-runtime catalog/registry ------------------------------
    """Publish an agent version (agent-runtime POST /registry/agents/{key}/
    versions/{v}/publish). Requires a passing eval-gate result unless force+
    reason (operator scope downstream)."""
    publishAgentVersion(agentKey: String!, version: Int!, force: Boolean, reason: String, idempotencyKey: String): AgentVersionPublishResult!
    """Upsert the caller-tenant's config for an agent (PUT /registry/tenants/
    self/agents/{key}) — enable/disable, pin a version, auto-execute policy
    (destructive/admin auto is rejected downstream, AC-5), self-approval.
    Requires tenant.admin downstream."""
    putTenantAgentConfig(agentKey: String!, input: TenantAgentConfigInput!, idempotencyKey: String): TenantAgentConfig!

    """BRD 53 inc2b: author a tenant custom agent (POST /registry/tenants/self/
    agents) with its guardrail envelope. Needs ai.agent.admin; the server
    validates + clamps the envelope and forces the shared safe graph."""
    createCustomAgent(input: CreateCustomAgentInput!): CustomAgentResult!

    """BRD 53 inc3: bind persona copilots for the given (pack) roles. Idempotent;
    needs ai.agent.admin."""
    autobindPersonaCopilots(roles: [String!]!, proposeTool: String): AutobindResult!

    """Operator-only: set the platform ceilings that clamp every custom agent."""
    setAgentCeilings(maxBudgetTokens: Int!, maxTier: String!): AgentCeilings!

    # ---- BRD 54 inc2: governed decision tables --------------------------------
    """Author + publish a decision table (agent-runtime POST /decision-models).
    Outcome disposition_codes are validated against the workspace catalog. Needs
    case.disposition.create."""
    createDecisionModel(input: CreateDecisionModelInput!, idempotencyKey: String): DecisionModel!
    """Run a decision table across a worklist (POST /decision-models/{id}/
    batch-evaluate). propose=false (default) is a dry-run preview with no side
    effect; propose=true mints one governed four-eyes proposal per matched
    case — no batch bypass of approval."""
    batchEvaluateDecisionModel(id: ID!, input: BatchEvaluateInput!, propose: Boolean = false, idempotencyKey: String): BatchEvaluateResult!
    """Four-eyes approval of the decision LOGIC (POST /decision-models/{id}/
    approve): publish a draft table. The approver must differ from the author.
    Needs case.disposition.create."""
    approveDecisionModel(id: ID!, idempotencyKey: String): DecisionModel!
    """Edit a table as a new DRAFT version (POST /decision-models/{id}/versions);
    the prior version is never mutated. Needs case.disposition.create."""
    newDecisionModelVersion(id: ID!, input: CreateDecisionModelInput!, idempotencyKey: String): DecisionModel!

    # ---- BRD 56: entity resolution (steward surface) --------------------------
    """Run + persist an entity-resolution run over a dataset (dataset-service
    POST /datasets/{id}/entity-resolution). Link layer only — the source of
    record is never mutated. Needs dataset.entity.execute."""
    resolveEntities(datasetId: ID!, input: ResolveEntitiesInput!, idempotencyKey: String): ResolveEntitiesResult!
    """Confirm a reviewed merge candidate by opening a four-eyes proposal
    (agent-runtime POST /entity-merges). The caller must hold dataset.entity.merge;
    a DIFFERENT user approves it in the proposals inbox. Self-approval is blocked."""
    proposeEntityMerge(input: ProposeEntityMergeInput!, idempotencyKey: String): EntityMergeProposal!
    """Materialize a run's resolved entities into a governed derived dataset
    (golden records; POST /resolution-runs/{id}/materialize). It becomes a normal,
    semantic-bindable governed dataset. Needs dataset.entity.execute."""
    materializeResolvedEntities(runId: ID!, input: MaterializeResolvedInput!, idempotencyKey: String): MaterializeResolvedResult!

    # ---- BRD 23: capability packs (pack-service) ------------------------------
    """Dry-run: compute what installing a pack WOULD do (create | exists |
    deferred) with no side effects. Needs pack.install.execute."""
    planPackInstall(pack: String!, workspaceId: String!, version: String): PackInstallPlan!
    """Install a pack into a workspace: materializes AS the caller (the JWT is
    forwarded, so every Core write is authorized truthfully) + records the
    origin-tagged ledger. Needs pack.install.execute."""
    installPack(pack: String!, workspaceId: String!, version: String, idempotencyKey: String): PackInstall!
    """Reverse a pack install (POST /installs/{id}/uninstall): delete objects
    whose Core service exposes a revert verb, tombstone the rest honestly.
    Needs pack.install.execute."""
    uninstallPack(installId: ID!, idempotencyKey: String): PackUninstallResult!
    """Phase 2: after a steward approves the pack's semantic model, materialize
    its dashboards (POST /installs/{id}/complete). Errors if still awaiting
    approval. Needs pack.install.execute."""
    completePackInstall(installId: ID!, idempotencyKey: String): PackCompleteResult!
    """Upgrade a live install to the pack's current on-disk version (POST
    /installs/{id}/upgrade). dryRun returns only the diff (no side effects); a real
    upgrade supersedes this install with a new one. Needs pack.install.execute."""
    upgradePack(installId: ID!, dryRun: Boolean = false, idempotencyKey: String): PackTransition!
    """Roll a live install back to a prior version (POST /installs/{id}/rollback).
    Defaults to the version this one superseded; toInstallId targets a specific
    prior install. dryRun returns only the diff. Needs pack.install.execute."""
    rollbackPack(installId: ID!, toInstallId: ID, dryRun: Boolean = false, idempotencyKey: String): PackTransition!
  }
`;
