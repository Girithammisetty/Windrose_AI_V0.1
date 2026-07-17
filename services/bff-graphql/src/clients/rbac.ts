/**
 * rbac-service REST client (BRD 02). Backs the display-only capability view on
 * the Viewer type PLUS the admin RBAC surfaces (workspaces, groups, members).
 *
 * The BFF makes NO authorization decision here — it forwards the caller's JWT
 * and reshapes rbac's REST payloads (snake→camel) for the UI. rbac-service still
 * enforces every action guard (BFF-FR-003/011).
 */
import { ServiceClient } from "./base.js";
import type { Page } from "./types.js";

export interface MeCapabilitiesDTO {
  user_id?: string;
  tenant_id?: string;
  roles?: string[];
  capabilities?: string[];
  admin?: boolean;
  workspace_name?: string;
}

export interface ViewerCapabilities {
  roles: string[];
  capabilities: string[];
  admin: boolean;
  /** Display name of the token's workspace (empty when unresolvable). */
  workspaceName: string;
}

/** POST /api/v1/authz/explain body (rbac explainRequest). Tenant is NEVER
 * accepted here — it's always the caller's own verified JWT tenant. */
export interface ExplainAuthzBody {
  user_id: string;
  typ?: string;
  scopes?: string[];
  action: string;
  resource_urn?: string;
  workspace_id?: string;
}

/** rbac authz.Explanation. One ChainStep per rule the decision engine walked. */
export interface ExplainChainStepDTO {
  type: string; // membership|role|workspace_assignment|grant|flag|scope_excluded
  group?: string;
  group_type?: string;
  role?: string;
  action?: string;
  workspace_scoped?: boolean;
  via_group?: string;
  workspace?: string;
  level?: string;
  subject?: string;
  admin?: boolean;
  detail?: string;
}

export interface ExplainAuthzDTO {
  allowed: boolean;
  reason: string;
  chain: ExplainChainStepDTO[];
}

/** rbac domain.Workspace (archived state is expressed via archived_at, not a status field). */
export interface WorkspaceDTO {
  id: string;
  tenant_id: string;
  name: string;
  description?: string;
  public?: boolean;
  created_by?: string;
  archived_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/** POST /workspaces body (rbac workspaceRequest). */
export interface CreateWorkspaceBody {
  name: string;
  description?: string;
  public?: boolean;
}

/** rbac domain.Group (note: the JSON key for the type is `group_type`). */
export interface GroupDTO {
  id: string;
  tenant_id: string;
  name: string;
  description?: string;
  group_type?: string;
  system?: boolean;
  auto_generated?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

/** rbac domain.Member (members-list cursor is the user_id). */
export interface MemberDTO {
  group_id: string;
  user_id: string;
  expires_at?: string | null;
  created_at?: string | null;
}

/** POST /groups + PATCH /groups/{id} body (rbac groupRequest). */
export interface GroupBody {
  name?: string;
  description?: string;
  /** Only read by the server on create; PATCH ignores it (name/description only). */
  group_type?: string;
}

/** rbac domain.Role (system roles carry tenant_id: null). */
export interface RoleDTO {
  id: string;
  tenant_id?: string | null;
  name: string;
  system?: boolean;
  version?: number;
  actions?: string[];
  created_at?: string | null;
  updated_at?: string | null;
}

// ---- Tier 4b: identity/rbac admin DTOs --------------------------------------
/** PATCH /workspaces/{id} body (rbac workspaceRequest; absent fields unchanged). */
export interface UpdateWorkspaceBody {
  name?: string;
  description?: string;
  public?: boolean;
}

/** One entry of POST /groups/{id}/members:bulk (rbac store.BulkMemberOp). */
export interface BulkMemberOpBody {
  op: "add" | "remove";
  user_id: string;
}

/** Per-entry outcome of a bulk membership call (rbac store.BulkMemberResult). */
export interface BulkMemberResultDTO {
  user_id: string;
  op: string;
  ok: boolean;
  code?: string;
}

/** POST /groups/{id}/members:bulk response ({results, succeeded, failed}). */
export interface BulkMembersResponseDTO {
  results: BulkMemberResultDTO[];
  succeeded: number;
  failed: number;
}

/** rbac domain.ContentGrant (subject flattened to subject_type/subject_id on reads). */
export interface ContentGrantDTO {
  id: string;
  tenant_id?: string;
  workspace_id: string;
  resource_urn: string;
  subject_type: string;
  subject_id: string;
  level: string;
  implicit?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

/** POST /grants body (rbac createGrantRequest — note the NESTED subject object). */
export interface CreateGrantBody {
  workspace_id: string;
  resource_urn: string;
  subject: { type: string; id: string };
  level: string;
}

/** One row of GET /grants?resource_urn= (rbac store.EffectiveAccessEntry):
 * direct + implicit_creator + via_group expansion with provenance. */
export interface EffectiveAccessEntryDTO {
  subject_type: string;
  subject_id: string;
  level: string;
  provenance: string; // direct | implicit_creator | via_group
  via?: string;
  grant_id: string;
  workspace_id: string;
}

export class RbacClient {
  constructor(private readonly http: ServiceClient) {}

  /** GET /api/v1/me/capabilities — the caller's own roles + allowed actions. */
  async meCapabilities(): Promise<ViewerCapabilities> {
    const d = await this.http.get<MeCapabilitiesDTO>("/api/v1/me/capabilities");
    return {
      roles: d.roles ?? [],
      capabilities: d.capabilities ?? [],
      admin: d.admin ?? false,
      workspaceName: d.workspace_name ?? "",
    };
  }

  // ---- workspaces ----------------------------------------------------------
  /** GET /api/v1/workspaces — visibility-filtered, cursor-paginated. `archived`
   * is ""(default, excludes archived) | "only" | "with". */
  workspaces(limit: number, cursor?: string, archived?: string): Promise<Page<WorkspaceDTO>> {
    return this.http.get<Page<WorkspaceDTO>>("/api/v1/workspaces", {
      query: { limit, cursor, archived },
    });
  }

  /** GET /api/v1/workspaces/{id}. */
  workspace(id: string): Promise<WorkspaceDTO> {
    return this.http.get<WorkspaceDTO>(`/api/v1/workspaces/${encodeURIComponent(id)}`);
  }

  /** POST /api/v1/workspaces — create (201; needs rbac.workspace.create). */
  createWorkspace(body: CreateWorkspaceBody, idempotencyKey?: string): Promise<WorkspaceDTO> {
    return this.http.post<WorkspaceDTO>("/api/v1/workspaces", { body, idempotencyKey });
  }

  // ---- Tier 4b: identity/rbac admin — workspace lifecycle + content groups --
  /** PATCH /api/v1/workspaces/{id} — name/description/public (needs rbac.workspace.update). */
  updateWorkspace(id: string, body: UpdateWorkspaceBody, idempotencyKey?: string): Promise<WorkspaceDTO> {
    return this.http.patch<WorkspaceDTO>(`/api/v1/workspaces/${encodeURIComponent(id)}`, {
      body, idempotencyKey,
    });
  }

  /** POST /api/v1/workspaces/{id}/archive (needs rbac.workspace.admin). 200 Workspace. */
  archiveWorkspace(id: string, idempotencyKey?: string): Promise<WorkspaceDTO> {
    return this.http.post<WorkspaceDTO>(`/api/v1/workspaces/${encodeURIComponent(id)}/archive`, {
      idempotencyKey,
    });
  }

  /** POST /api/v1/workspaces/{id}/restore (needs rbac.workspace.admin). 200 Workspace. */
  restoreWorkspace(id: string, idempotencyKey?: string): Promise<WorkspaceDTO> {
    return this.http.post<WorkspaceDTO>(`/api/v1/workspaces/${encodeURIComponent(id)}/restore`, {
      idempotencyKey,
    });
  }

  /** PUT /api/v1/workspaces/{id}/content-groups/{groupId} — link a content group
   * (200 {status:"linked"}; needs rbac.workspace.update). */
  async linkContentGroup(workspaceId: string, groupId: string): Promise<void> {
    await this.http.put<unknown>(
      `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/content-groups/${encodeURIComponent(groupId)}`,
    );
  }

  /** DELETE /api/v1/workspaces/{id}/content-groups/{groupId} — unlink (200
   * {status:"unlinked"}; needs rbac.workspace.update). */
  async unlinkContentGroup(workspaceId: string, groupId: string): Promise<void> {
    await this.http.delete<unknown>(
      `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/content-groups/${encodeURIComponent(groupId)}`,
    );
  }

  // ---- groups --------------------------------------------------------------
  /** GET /api/v1/groups — cursor-paginated. `type` filters permission|content. */
  groups(limit: number, cursor?: string, type?: string): Promise<Page<GroupDTO>> {
    return this.http.get<Page<GroupDTO>>("/api/v1/groups", { query: { limit, cursor, type } });
  }

  /** GET /api/v1/groups/{id}. */
  group(id: string): Promise<GroupDTO> {
    return this.http.get<GroupDTO>(`/api/v1/groups/${encodeURIComponent(id)}`);
  }

  /** GET /api/v1/groups/{id}/members — cursor-paginated (cursor = user_id). */
  groupMembers(groupId: string, limit: number, cursor?: string): Promise<Page<MemberDTO>> {
    return this.http.get<Page<MemberDTO>>(
      `/api/v1/groups/${encodeURIComponent(groupId)}/members`,
      { query: { limit, cursor } },
    );
  }

  /** PUT /api/v1/groups/{id}/members/{userId} — idempotent add (200/201). */
  async addGroupMember(groupId: string, userId: string, idempotencyKey?: string): Promise<void> {
    await this.http.put<unknown>(
      `/api/v1/groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(userId)}`,
      { idempotencyKey },
    );
  }

  /** DELETE /api/v1/groups/{id}/members/{userId} — remove (204). */
  async removeGroupMember(groupId: string, userId: string): Promise<void> {
    await this.http.delete<void>(
      `/api/v1/groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(userId)}`,
    );
  }

  /** POST /api/v1/groups — create (201; needs rbac.group.create). A "Team" in the
   * UI is specifically a permission-type group (per-workspace user groupings for
   * role assignment) — the caller fixes group_type to "permission". */
  createGroup(body: GroupBody, idempotencyKey?: string): Promise<GroupDTO> {
    return this.http.post<GroupDTO>("/api/v1/groups", { body, idempotencyKey });
  }

  /** PATCH /api/v1/groups/{id} — name/description only (needs rbac.group.update). */
  updateGroup(id: string, body: GroupBody, idempotencyKey?: string): Promise<GroupDTO> {
    return this.http.patch<GroupDTO>(`/api/v1/groups/${encodeURIComponent(id)}`, { body, idempotencyKey });
  }

  /** DELETE /api/v1/groups/{id} — remove (204; needs rbac.group.delete). */
  async deleteGroup(id: string): Promise<void> {
    await this.http.delete<void>(`/api/v1/groups/${encodeURIComponent(id)}`);
  }

  /** GET /api/v1/groups/{id}/roles — the roles currently bound to a permission
   * group (read side of bind/unbind); cursor-paginated (needs rbac.group.read). */
  groupRoles(groupId: string, limit: number, cursor?: string): Promise<Page<RoleDTO>> {
    return this.http.get<Page<RoleDTO>>(
      `/api/v1/groups/${encodeURIComponent(groupId)}/roles`,
      { query: { limit, cursor } },
    );
  }

  /** PUT /api/v1/groups/{id}/roles/{roleId} — bind a role to a permission group
   * (200; needs rbac.group.update). */
  async bindGroupRole(groupId: string, roleId: string): Promise<void> {
    await this.http.put<unknown>(
      `/api/v1/groups/${encodeURIComponent(groupId)}/roles/${encodeURIComponent(roleId)}`,
    );
  }

  /** DELETE /api/v1/groups/{id}/roles/{roleId} — unbind (204; needs rbac.group.update). */
  async unbindGroupRole(groupId: string, roleId: string): Promise<void> {
    await this.http.delete<void>(
      `/api/v1/groups/${encodeURIComponent(groupId)}/roles/${encodeURIComponent(roleId)}`,
    );
  }

  /** POST /api/v1/groups/{id}/members:bulk — up to 500 add/remove ops with a
   * per-entry partial-failure report (needs rbac.group.assign). */
  bulkGroupMembers(
    groupId: string,
    operations: BulkMemberOpBody[],
    idempotencyKey?: string,
  ): Promise<BulkMembersResponseDTO> {
    return this.http.post<BulkMembersResponseDTO>(
      `/api/v1/groups/${encodeURIComponent(groupId)}/members:bulk`,
      { body: { operations }, idempotencyKey },
    );
  }

  // ---- users ----------------------------------------------------------------
  /** GET /api/v1/users/{id}/groups — the groups a user belongs to (reverse of
   * group membership); cursor-paginated (needs rbac.group.read). */
  userGroups(userId: string, limit: number, cursor?: string): Promise<Page<GroupDTO>> {
    return this.http.get<Page<GroupDTO>>(
      `/api/v1/users/${encodeURIComponent(userId)}/groups`,
      { query: { limit, cursor } },
    );
  }

  // ---- roles -----------------------------------------------------------------
  /** GET /api/v1/roles — cursor-paginated (needs rbac.role.list). Feeds the
   * role picker used to bind a role to a team. */
  roles(limit: number, cursor?: string): Promise<Page<RoleDTO>> {
    return this.http.get<Page<RoleDTO>>("/api/v1/roles", { query: { limit, cursor } });
  }

  // ---- Tier 4b: identity/rbac admin — custom-role CRUD ----------------------
  /** POST /api/v1/roles — create a custom role (201; needs rbac.role.create). */
  createRole(name: string, actions: string[], idempotencyKey?: string): Promise<RoleDTO> {
    return this.http.post<RoleDTO>("/api/v1/roles", { body: { name, actions }, idempotencyKey });
  }

  /** PATCH /api/v1/roles/{id} — rename ONLY (needs rbac.role.update). System
   * roles answer 409 SYSTEM_IMMUTABLE, surfaced verbatim. */
  renameRole(id: string, name: string, idempotencyKey?: string): Promise<RoleDTO> {
    return this.http.patch<RoleDTO>(`/api/v1/roles/${encodeURIComponent(id)}`, {
      body: { name }, idempotencyKey,
    });
  }

  /** PATCH /api/v1/roles/{id} — edit a custom role's name and/or action set in
   * one atomic call (needs rbac.role.update). Both fields are optional; omit a
   * field to leave it unchanged. System roles answer 409 SYSTEM_IMMUTABLE,
   * surfaced verbatim. */
  updateRole(
    id: string,
    input: { name?: string; actions?: string[] },
    idempotencyKey?: string,
  ): Promise<RoleDTO> {
    return this.http.patch<RoleDTO>(`/api/v1/roles/${encodeURIComponent(id)}`, {
      body: input, idempotencyKey,
    });
  }

  /** PUT /api/v1/roles/{id}/actions — replace the action set (needs
   * rbac.role.update). System roles answer 409 SYSTEM_IMMUTABLE. */
  setRoleActions(id: string, actions: string[], idempotencyKey?: string): Promise<RoleDTO> {
    return this.http.put<RoleDTO>(`/api/v1/roles/${encodeURIComponent(id)}/actions`, {
      body: { actions }, idempotencyKey,
    });
  }

  /** DELETE /api/v1/roles/{id} (204; needs rbac.role.delete). System roles 409. */
  async deleteRole(id: string): Promise<void> {
    await this.http.delete<void>(`/api/v1/roles/${encodeURIComponent(id)}`);
  }

  // ---- Tier 4b: identity/rbac admin — content grants -------------------------
  /** GET /api/v1/grants?resource_urn=<urn> — effective access for one resource
   * ({data:[EffectiveAccessEntry]}; needs rbac.grant.list). resource_urn is
   * REQUIRED by the route. */
  async grants(resourceUrn: string): Promise<EffectiveAccessEntryDTO[]> {
    const res = await this.http.get<Page<EffectiveAccessEntryDTO>>("/api/v1/grants", {
      query: { resource_urn: resourceUrn },
    });
    return res.data ?? [];
  }

  /** POST /api/v1/grants — create a content grant (201 ContentGrant; needs
   * rbac.grant.create). The body nests the subject: {subject: {type, id}}. */
  createGrant(body: CreateGrantBody, idempotencyKey?: string): Promise<ContentGrantDTO> {
    return this.http.post<ContentGrantDTO>("/api/v1/grants", { body, idempotencyKey });
  }

  /** DELETE /api/v1/grants/{id} (204; needs rbac.grant.delete). */
  async deleteGrant(id: string): Promise<void> {
    await this.http.delete<void>(`/api/v1/grants/${encodeURIComponent(id)}`);
  }

  // ---- authz debug explain -----------------------------------------------
  /** POST /api/v1/authz/explain — the real decision trace for a
   * subject+action(+resource) tuple (debug tool). Needs audit.log.read;
   * tenant is always the caller's own verified token tenant. */
  explainAuthz(body: ExplainAuthzBody): Promise<ExplainAuthzDTO> {
    return this.http.post<ExplainAuthzDTO>("/api/v1/authz/explain", { body });
  }
}
