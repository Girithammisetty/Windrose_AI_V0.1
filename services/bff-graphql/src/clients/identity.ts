/** identity-service REST client (BRD 01). Backs: Viewer, User, Tenant,
 * ServiceAccount + the admin user directory. Pure passthrough — the caller's JWT
 * is forwarded verbatim by ServiceClient and identity-service enforces every
 * `identity.*` action guard. The BFF makes no authz/business decision here. */
import { ServiceClient } from "./base.js";
import type { Page } from "./types.js";

export interface UserDTO {
  id: string;
  tenant_id: string;
  email: string;
  full_name?: string;
  status?: string;
  idp_subject?: string | null;
  last_login_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/** POST /users/invite body (identity domain.InviteRequest). */
export interface InviteUserBody {
  email: string;
  full_name?: string;
  groups?: string[];
}

/** identity domain.ServiceAccount (secret hashes are json:"-", never serialized). */
export interface ServiceAccountDTO {
  id: string;
  tenant_id: string;
  name: string;
  scopes?: string[];
  expires_at?: string | null;
  last_used_at?: string | null;
  revoked_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

// Tier 4b: identity/rbac admin — user lifecycle + service-account lifecycle.
/** POST /service-accounts body (identity domain.CreateServiceAccountRequest). */
export interface CreateServiceAccountBody {
  name: string;
  scopes?: string[];
  expires_at?: string;
}

/** identity domain.CreatedServiceAccount — the ONLY shape that ever carries the
 * api_key (format wr_sa_<id>.<secret>). Returned exactly once on create and
 * rotate; never retrievable again (BR-11). */
export interface CreatedServiceAccountDTO {
  service_account: ServiceAccountDTO;
  api_key: string;
}

/** identity domain.Quotas. */
export interface QuotasDTO {
  cpu?: number;
  memory?: string;
  processing_cpu?: number;
  processing_memory?: string;
}

/** identity domain.Tenant (settings live on the tenant object itself). */
export interface TenantDTO {
  id: string;
  name: string;
  display_name?: string;
  owner_email?: string;
  tier?: string;
  cloud?: string;
  status?: string;
  quotas?: QuotasDTO;
  platform_version?: string;
  subdomain?: string;
  auto_upgrade?: boolean;
  modules?: string[];
  created_at?: string | null;
  updated_at?: string | null;
}

/** GET /api/v1/tenants/self — the member-safe subset of the caller's tenant. */
export interface TenantSelfDTO {
  id?: string;
  name?: string;
  display_name?: string;
  status?: string;
}

/** GET /api/v1/tenants/{id}/embed-config — never carries the secret itself
 * (only its hash is stored server-side); "configured" distinguishes "never
 * set up" from "configured with zero origins". */
export interface EmbedConfigDTO {
  configured: boolean;
  allowed_origins: string[];
  updated_at?: string | null;
}

/** PUT /api/v1/tenants/{id}/embed-config response — the plaintext secret is
 * returned exactly once, at generation time; it is never retrievable again. */
export interface SetEmbedConfigDTO {
  embed_secret: string;
  allowed_origins: string[];
}

/** GET/PUT /api/v1/tenants/self/idp — the caller tenant's OIDC IdP (BYO-P4). */
export interface IdpConfigDTO {
  issuer: string;
  client_id: string;
  discovery_url: string;
  enabled: boolean;
  updated_at?: string | null;
}

export class IdentityClient {
  constructor(private readonly http: ServiceClient) {}

  /** GET /api/v1/tenants/self — name/display name of the CALLER's own tenant
   * (member-visible; no admin scope needed, unlike GET /tenants/{id}). */
  tenantSelf(): Promise<TenantSelfDTO> {
    return this.http.get<TenantSelfDTO>("/api/v1/tenants/self");
  }

  /** GET /api/v1/users/profiles?filter[id]=a,b,c — batch hydration for the
   * userById loader (Case.assignee, CaseComment.author, CaseActivity.actor).
   * Deliberately NOT GET /api/v1/users (identity.user.admin, the tenant
   * directory listing): these are display-only lookups of ids the caller
   * already has from an authorized resource, so they go through identity's
   * member-visible /users/profiles endpoint instead — no admin scope
   * required, and it returns only {id, email, full_name} (no
   * status/idp_subject/last_login_at/timestamps). */
  async usersByIds(ids: string[]): Promise<UserDTO[]> {
    if (ids.length === 0) return [];
    const res = await this.http.get<Page<UserDTO>>("/api/v1/users/profiles", {
      query: { "filter[id]": ids.join(","), limit: ids.length },
    });
    return res.data ?? [];
  }

  /** GET /api/v1/users/{id}. */
  user(id: string): Promise<UserDTO> {
    return this.http.get<UserDTO>(`/api/v1/users/${encodeURIComponent(id)}`);
  }

  /** GET /api/v1/users (tenant admin list; cursor-paginated). Admin only —
   * requires identity.user.admin, returns the full admin DTO. */
  users(limit: number, cursor?: string): Promise<Page<UserDTO>> {
    return this.http.get<Page<UserDTO>>("/api/v1/users", { query: { limit, cursor } });
  }

  /** GET /api/v1/users/assignable — member-safe directory of ACTIVE tenant
   * users (id/email/full_name only) for the case assignment + mention pickers.
   * Deliberately NOT GET /api/v1/users: no admin scope required (mirrors
   * /users/profiles), so a case worker with case.case.assign can list assignees
   * without identity.user.admin. status/last_login_at/etc. are never returned. */
  assignableUsers(limit: number, cursor?: string): Promise<Page<UserDTO>> {
    return this.http.get<Page<UserDTO>>("/api/v1/users/assignable", { query: { limit, cursor } });
  }

  /** POST /api/v1/users/invite — create a user in the "invited" state (201).
   * Depends on Keycloak; a downstream 5xx on the KC path surfaces honestly. */
  inviteUser(body: InviteUserBody, idempotencyKey?: string): Promise<UserDTO> {
    return this.http.post<UserDTO>("/api/v1/users/invite", { body, idempotencyKey });
  }

  /** GET /api/v1/service-accounts (cursor-paginated; no filters). */
  serviceAccounts(limit: number, cursor?: string): Promise<Page<ServiceAccountDTO>> {
    return this.http.get<Page<ServiceAccountDTO>>("/api/v1/service-accounts", {
      query: { limit, cursor },
    });
  }

  /** GET /api/v1/tenants/{id} — the tenant object + its settings. */
  tenant(id: string): Promise<TenantDTO> {
    return this.http.get<TenantDTO>(`/api/v1/tenants/${encodeURIComponent(id)}`);
  }

  /** GET /api/v1/tenants/{id}/embed-config — 404s (via nullOn404 at the
   * resolver) when the tenant has never configured embedding. */
  embedConfig(id: string): Promise<EmbedConfigDTO> {
    return this.http.get<EmbedConfigDTO>(`/api/v1/tenants/${encodeURIComponent(id)}/embed-config`);
  }

  /** PUT /api/v1/tenants/{id}/embed-config — (re)generates the embed secret
   * and sets allowed origins; the plaintext secret is returned exactly once. */
  setEmbedConfig(id: string, allowedOrigins: string[], idempotencyKey?: string): Promise<SetEmbedConfigDTO> {
    return this.http.put<SetEmbedConfigDTO>(`/api/v1/tenants/${encodeURIComponent(id)}/embed-config`, {
      body: { allowed_origins: allowedOrigins },
      idempotencyKey,
    });
  }

  // ---- BYO-P4: per-tenant OIDC IdP config (self-scoped, tenant admin) -------
  /** GET /api/v1/tenants/self/idp — the caller tenant's OIDC IdP, or 404. */
  tenantIdp(): Promise<IdpConfigDTO> {
    return this.http.get<IdpConfigDTO>("/api/v1/tenants/self/idp");
  }
  /** PUT /api/v1/tenants/self/idp — register/update the caller tenant's IdP. */
  setTenantIdp(body: { issuer: string; client_id?: string; discovery_url?: string; enabled?: boolean }, idempotencyKey?: string): Promise<IdpConfigDTO> {
    return this.http.put<IdpConfigDTO>("/api/v1/tenants/self/idp", { body, idempotencyKey });
  }
  /** DELETE /api/v1/tenants/self/idp — turn off SSO for the caller's tenant. */
  deleteTenantIdp(): Promise<void> {
    return this.http.delete<void>("/api/v1/tenants/self/idp");
  }

  // ---- Tier 4b: identity/rbac admin — user + service-account lifecycle -----
  /** PATCH /api/v1/users/{id} — rename (identity.user.admin). Bare User back. */
  patchUser(id: string, fullName: string, idempotencyKey?: string): Promise<UserDTO> {
    return this.http.patch<UserDTO>(`/api/v1/users/${encodeURIComponent(id)}`, {
      body: { full_name: fullName }, idempotencyKey,
    });
  }

  /** POST /api/v1/users/{id}/deactivate (identity.user.admin). The last-admin
   * guard (BR-9) 409s unless overrideLastAdmin (super-admin only) is passed as
   * ?override_last_admin=true. */
  deactivateUser(id: string, overrideLastAdmin?: boolean, idempotencyKey?: string): Promise<UserDTO> {
    return this.http.post<UserDTO>(`/api/v1/users/${encodeURIComponent(id)}/deactivate`, {
      query: { override_last_admin: overrideLastAdmin ? "true" : undefined },
      idempotencyKey,
    });
  }

  /** POST /api/v1/users/{id}/invite/resend (identity.user.admin) — re-issue the
   * activation link for an invited user. 200 User. */
  resendInvite(id: string, idempotencyKey?: string): Promise<UserDTO> {
    return this.http.post<UserDTO>(`/api/v1/users/${encodeURIComponent(id)}/invite/resend`, {
      idempotencyKey,
    });
  }

  /** DELETE /api/v1/users/{id} — soft delete (identity.user.admin). 204. */
  async deleteUser(id: string): Promise<void> {
    await this.http.delete<void>(`/api/v1/users/${encodeURIComponent(id)}`);
  }

  /** POST /api/v1/service-accounts (identity.service_account.admin, 201). The
   * response carries the api_key EXACTLY ONCE — pass it through verbatim. */
  createServiceAccount(body: CreateServiceAccountBody, idempotencyKey?: string): Promise<CreatedServiceAccountDTO> {
    return this.http.post<CreatedServiceAccountDTO>("/api/v1/service-accounts", { body, idempotencyKey });
  }

  /** POST /api/v1/service-accounts/{id}/rotate (identity.service_account.admin).
   * Issues a NEW api_key (shown once) and invalidates the old secret. */
  rotateServiceAccount(id: string, idempotencyKey?: string): Promise<CreatedServiceAccountDTO> {
    return this.http.post<CreatedServiceAccountDTO>(
      `/api/v1/service-accounts/${encodeURIComponent(id)}/rotate`,
      { idempotencyKey },
    );
  }

  /** DELETE /api/v1/service-accounts/{id} — revoke (identity.service_account.admin). 204. */
  async revokeServiceAccount(id: string): Promise<void> {
    await this.http.delete<void>(`/api/v1/service-accounts/${encodeURIComponent(id)}`);
  }
}
