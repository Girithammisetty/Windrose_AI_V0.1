package store

import (
	"context"
	"errors"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/tool-plane/internal/domain"
)

// ResolveVersion returns the version the gateway should evaluate: the published
// version when version=="" (BR-4 resolves the newest in-range at session start —
// here the single published one), or a specific version (any status) so the
// pipeline can surface TOOL_RETIRED / deprecation. Returns nil on not found.
func (s *PG) ResolveVersion(ctx context.Context, toolID, version string) (*domain.ToolVersion, error) {
	v, err := s.GetPublishedVersion(ctx, toolID, version)
	if errors.Is(err, ErrNotFound) {
		return nil, nil
	}
	return v, err
}

// CreateBackend registers an MCP facade / external endpoint (TPL-FR-010/012).
func (s *PG) CreateBackend(ctx context.Context, b *domain.MCPBackend) error {
	allow := b.EgressAllowlist
	if allow == nil {
		allow = []string{}
	}
	return s.withPlatform(ctx, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO mcp_backends (name, tenant_id, internal_url, spiffe_id, kind, egress_allowlist, vault_auth_ref, status)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
			ON CONFLICT (name) DO UPDATE SET internal_url=$3, spiffe_id=$4, kind=$5, egress_allowlist=$6, vault_auth_ref=$7, status=$8`,
			b.Name, domain.PlatformTenant, b.InternalURL, b.SpiffeID, b.Kind, allow, b.VaultAuthRef, b.Status)
		return err
	})
}

// GetBackendByService resolves the backend for an owner service (tool_id prefix
// → owning MCP server, TPL-FR-012). Returns nil on not found.
func (s *PG) GetBackendByService(ctx context.Context, ownerService string) (*domain.MCPBackend, error) {
	var out *domain.MCPBackend
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		var b domain.MCPBackend
		err := tx.QueryRow(ctx, `
			SELECT name, internal_url, spiffe_id, kind, egress_allowlist, vault_auth_ref, status
			FROM mcp_backends WHERE name=$1 AND status='active'`, ownerService).Scan(
			&b.Name, &b.InternalURL, &b.SpiffeID, &b.Kind, &b.EgressAllowlist, &b.VaultAuthRef, &b.Status)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		out = &b
		return nil
	})
	return out, err
}

// CallableTool is one entry in a caller-scoped tools/list (TPL-FR-011).
type CallableTool struct {
	Version *domain.ToolVersion
	OwnerService string
}

// ListEnabledVersions returns published/deprecated versions of tools the tenant
// has enabled (caller-scoping for tools/list; kill + toolset filtering applied by
// the gateway). Capped at 100 (BR-10).
func (s *PG) ListEnabledVersions(ctx context.Context, tenant uuid.UUID) ([]CallableTool, error) {
	var out []CallableTool
	// Tenant RLS session: catalog rows visible via tenant_read, enablement rows
	// via tenant_isolation (caller-scoping for tools/list).
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT `+prefixCols("tv")+`, t.owner_service
			FROM tool_versions tv
			JOIN tools t ON t.tool_id = tv.tool_id
			JOIN tenant_tool_settings ts ON ts.tool_id = tv.tool_id AND ts.tenant_id = $1 AND ts.enabled = true
			WHERE tv.status IN ('published','deprecated')
			ORDER BY tv.tool_id LIMIT 100`, tenant)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var v domain.ToolVersion
			var inSchema, outSchema, sla, examples []byte
			var owner string
			if err := rows.Scan(&v.ToolID, &v.Version, &v.Status, &inSchema, &outSchema, &v.SemanticDescription,
				&v.PermissionTier, &v.CostWeight, &sla, &v.SideEffects, &examples, &v.EmbeddingModelVer,
				&v.DeprecationEndsAt, &v.PublishedAt, &v.CreatedAt, &v.UpdatedAt, &owner); err != nil {
				return err
			}
			unmarshalVersion(&v, inSchema, outSchema, sla, examples)
			out = append(out, CallableTool{Version: &v, OwnerService: owner})
		}
		return rows.Err()
	})
	return out, err
}
