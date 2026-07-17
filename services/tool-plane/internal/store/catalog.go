package store

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/tool-plane/internal/domain"
	"github.com/windrose-ai/tool-plane/internal/events"
)

// ---- Tools ------------------------------------------------------------------

// CreateTool inserts a catalog tool (platform-scoped) + lifecycle event.
func (s *PG) CreateTool(ctx context.Context, t *domain.Tool, envs []events.Envelope) error {
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		tags := t.Tags
		if tags == nil {
			tags = []string{}
		}
		_, err := tx.Exec(ctx, `
			INSERT INTO tools (tool_id, tenant_id, display_name, owner_service, owner_team, enabled_by_default, side_effects, tags)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`,
			t.ToolID, domain.PlatformTenant, t.DisplayName, t.OwnerService, t.OwnerTeam,
			t.EnabledByDefault, t.SideEffects, tags)
		if err != nil {
			return err
		}
		return insertOutboxTx(ctx, tx, envs)
	})
	if isUniqueViolation(err) {
		return ErrConflict
	}
	return err
}

// GetTool fetches a tool by id (platform-scoped).
func (s *PG) GetTool(ctx context.Context, toolID string) (*domain.Tool, error) {
	var t *domain.Tool
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		var got domain.Tool
		err := tx.QueryRow(ctx, `
			SELECT tool_id, display_name, owner_service, owner_team, enabled_by_default, side_effects, tags, created_at, updated_at
			FROM tools WHERE tool_id = $1`, toolID).Scan(
			&got.ToolID, &got.DisplayName, &got.OwnerService, &got.OwnerTeam,
			&got.EnabledByDefault, &got.SideEffects, &got.Tags, &got.CreatedAt, &got.UpdatedAt)
		if errors.Is(err, pgx.ErrNoRows) {
			return ErrNotFound
		}
		if err != nil {
			return err
		}
		t = &got
		return nil
	})
	return t, err
}

// ToolFilter filters catalog list (indexed fields only, MASTER-FR-023).
type ToolFilter struct {
	OwnerService string
	Limit        int
	AfterID      string
}

// ListTools returns a cursor page of tools (platform-scoped).
func (s *PG) ListTools(ctx context.Context, f ToolFilter) ([]*domain.Tool, string, error) {
	limit := clampLimit(f.Limit)
	var out []*domain.Tool
	var next string
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		q := `SELECT tool_id, display_name, owner_service, owner_team, enabled_by_default, side_effects, tags, created_at, updated_at FROM tools WHERE true`
		args := []any{}
		if f.OwnerService != "" {
			args = append(args, f.OwnerService)
			q += " AND owner_service = $1"
		}
		if f.AfterID != "" {
			args = append(args, f.AfterID)
			q += " AND tool_id > $" + itoa(len(args))
		}
		args = append(args, limit+1)
		q += " ORDER BY tool_id ASC LIMIT $" + itoa(len(args))
		rows, err := tx.Query(ctx, q, args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var t domain.Tool
			if err := rows.Scan(&t.ToolID, &t.DisplayName, &t.OwnerService, &t.OwnerTeam,
				&t.EnabledByDefault, &t.SideEffects, &t.Tags, &t.CreatedAt, &t.UpdatedAt); err != nil {
				return err
			}
			out = append(out, &t)
		}
		return rows.Err()
	})
	if err != nil {
		return nil, "", err
	}
	if len(out) > limit {
		out = out[:limit]
		next = out[len(out)-1].ToolID
	}
	return out, next, nil
}

// ---- Tool versions ----------------------------------------------------------

const versionCols = `tool_id, version, status, input_schema, output_schema, semantic_description,
	permission_tier, cost_weight, declared_sla, side_effects, examples, embedding_model_ver,
	deprecation_ends_at, published_at, created_at, updated_at`

func scanVersion(row pgx.Row) (*domain.ToolVersion, error) {
	var v domain.ToolVersion
	var inSchema, outSchema, sla, examples []byte
	err := row.Scan(&v.ToolID, &v.Version, &v.Status, &inSchema, &outSchema, &v.SemanticDescription,
		&v.PermissionTier, &v.CostWeight, &sla, &v.SideEffects, &examples, &v.EmbeddingModelVer,
		&v.DeprecationEndsAt, &v.PublishedAt, &v.CreatedAt, &v.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	unmarshalVersion(&v, inSchema, outSchema, sla, examples)
	return &v, nil
}

// unmarshalVersion decodes the JSONB columns onto a ToolVersion.
func unmarshalVersion(v *domain.ToolVersion, inSchema, outSchema, sla, examples []byte) {
	_ = json.Unmarshal(inSchema, &v.InputSchema)
	_ = json.Unmarshal(outSchema, &v.OutputSchema)
	_ = json.Unmarshal(sla, &v.DeclaredSLA)
	_ = json.Unmarshal(examples, &v.Examples)
}

// CreateVersion inserts a draft version.
func (s *PG) CreateVersion(ctx context.Context, v *domain.ToolVersion, envs []events.Envelope) error {
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO tool_versions (tool_id, tenant_id, version, status, input_schema, output_schema,
				semantic_description, permission_tier, cost_weight, declared_sla, side_effects, examples)
			VALUES ($1,$2,$3,'draft',$4,$5,$6,$7,$8,$9,$10,$11)`,
			v.ToolID, domain.PlatformTenant, v.Version, mustJSON(v.InputSchema), mustJSON(v.OutputSchema),
			v.SemanticDescription, v.PermissionTier, v.CostWeight, mustJSON(v.DeclaredSLA),
			v.SideEffects, mustJSON(v.Examples))
		if err != nil {
			return err
		}
		return insertOutboxTx(ctx, tx, envs)
	})
	if isUniqueViolation(err) {
		return ErrConflict
	}
	return err
}

// GetVersion fetches one version.
func (s *PG) GetVersion(ctx context.Context, toolID, version string) (*domain.ToolVersion, error) {
	var v *domain.ToolVersion
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		var err error
		v, err = scanVersion(tx.QueryRow(ctx, `SELECT `+versionCols+` FROM tool_versions WHERE tool_id=$1 AND version=$2`, toolID, version))
		return err
	})
	return v, err
}

// ListVersions returns all versions of a tool (newest-ish; small set).
func (s *PG) ListVersions(ctx context.Context, toolID string) ([]*domain.ToolVersion, error) {
	var out []*domain.ToolVersion
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT `+versionCols+` FROM tool_versions WHERE tool_id=$1 ORDER BY version`, toolID)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			v, err := scanVersion(rows)
			if err != nil {
				return err
			}
			out = append(out, v)
		}
		return rows.Err()
	})
	return out, err
}

// ListActiveVersions returns all published/deprecated versions (SLA sweep input).
func (s *PG) ListActiveVersions(ctx context.Context) ([]*domain.ToolVersion, error) {
	var out []*domain.ToolVersion
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT `+versionCols+` FROM tool_versions WHERE status IN ('published','deprecated')`)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			v, err := scanVersion(rows)
			if err != nil {
				return err
			}
			out = append(out, v)
		}
		return rows.Err()
	})
	return out, err
}

// GetPublishedVersion returns the currently-published (or a specific deprecated)
// version. When version=="" it resolves the single published version.
func (s *PG) GetPublishedVersion(ctx context.Context, toolID, version string) (*domain.ToolVersion, error) {
	var v *domain.ToolVersion
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		var err error
		if version == "" {
			v, err = scanVersion(tx.QueryRow(ctx, `SELECT `+versionCols+` FROM tool_versions WHERE tool_id=$1 AND status='published'`, toolID))
		} else {
			v, err = scanVersion(tx.QueryRow(ctx, `SELECT `+versionCols+` FROM tool_versions WHERE tool_id=$1 AND version=$2`, toolID, version))
		}
		return err
	})
	return v, err
}

// PublishVersion sets a draft to published with its computed embedding (AC-7:
// embedding row populated before discoverable). It fails if another version is
// already published (unique partial index) unless that is first deprecated.
func (s *PG) PublishVersion(ctx context.Context, toolID, version string, embedding []float32, modelVer string, envs []events.Envelope) error {
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		vec := vectorLiteral(embedding)
		ct, err := tx.Exec(ctx, `
			UPDATE tool_versions
			SET status='published', embedding=$3::vector, embedding_model_ver=$4, published_at=now(), updated_at=now()
			WHERE tool_id=$1 AND version=$2 AND status='draft'`,
			toolID, version, vec, modelVer)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return ErrNotFound
		}
		return insertOutboxTx(ctx, tx, envs)
	})
	if isUniqueViolation(err) {
		return ErrConflict
	}
	return err
}

// SetVersionStatus transitions a version's status (deprecate/retire/quarantine/
// restore) with an optional deprecation deadline.
func (s *PG) SetVersionStatus(ctx context.Context, toolID, version, status string, deprecationEndsAt *time.Time, envs []events.Envelope) error {
	return s.withPlatform(ctx, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `
			UPDATE tool_versions SET status=$3, deprecation_ends_at=COALESCE($4, deprecation_ends_at), updated_at=now()
			WHERE tool_id=$1 AND version=$2`,
			toolID, version, status, deprecationEndsAt)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return ErrNotFound
		}
		return insertOutboxTx(ctx, tx, envs)
	})
}

// DiscoveryHit is one semantic search result (TPL-FR-020).
type DiscoveryHit struct {
	Version *domain.ToolVersion
	Score   float64
}

// SearchByEmbedding returns tools ranked by pgvector cosine similarity to the
// query embedding, scoped to the tenant's enabled tools (caller-scoping is
// applied by the API layer for tier/kill). Only published/deprecated versions
// with a populated embedding participate (AC-6/AC-7). Real pgvector ANN search.
func (s *PG) SearchByEmbedding(ctx context.Context, tenant uuid.UUID, query []float32, topK int, tierFilter []string) ([]DiscoveryHit, error) {
	if topK <= 0 || topK > 20 {
		topK = 20
	}
	var hits []DiscoveryHit
	vec := vectorLiteral(query)
	// Runs under the tenant RLS session: catalog rows (platform-tenant) are
	// visible via the tenant_read SELECT policy and enablement rows via
	// tenant_isolation, so discovery is naturally caller-scoped.
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		// 1 - cosine distance = cosine similarity.
		q := `
			SELECT ` + prefixCols("tv") + `, 1 - (tv.embedding <=> $1::vector) AS score
			FROM tool_versions tv
			JOIN tenant_tool_settings ts ON ts.tool_id = tv.tool_id AND ts.tenant_id = $2 AND ts.enabled = true
			WHERE tv.status IN ('published','deprecated') AND tv.embedding IS NOT NULL`
		args := []any{vec, tenant}
		if len(tierFilter) > 0 {
			args = append(args, tierFilter)
			q += ` AND tv.permission_tier = ANY($3)`
		}
		args = append(args, topK)
		q += ` ORDER BY tv.embedding <=> $1::vector LIMIT $` + itoa(len(args))
		rows, err := tx.Query(ctx, q, args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var v domain.ToolVersion
			var inSchema, outSchema, sla, examples []byte
			var score float64
			if err := rows.Scan(&v.ToolID, &v.Version, &v.Status, &inSchema, &outSchema, &v.SemanticDescription,
				&v.PermissionTier, &v.CostWeight, &sla, &v.SideEffects, &examples, &v.EmbeddingModelVer,
				&v.DeprecationEndsAt, &v.PublishedAt, &v.CreatedAt, &v.UpdatedAt, &score); err != nil {
				return err
			}
			_ = json.Unmarshal(inSchema, &v.InputSchema)
			_ = json.Unmarshal(outSchema, &v.OutputSchema)
			_ = json.Unmarshal(sla, &v.DeclaredSLA)
			_ = json.Unmarshal(examples, &v.Examples)
			hits = append(hits, DiscoveryHit{Version: &v, Score: score})
		}
		return rows.Err()
	})
	return hits, err
}

func prefixCols(p string) string {
	cols := []string{"tool_id", "version", "status", "input_schema", "output_schema", "semantic_description",
		"permission_tier", "cost_weight", "declared_sla", "side_effects", "examples", "embedding_model_ver",
		"deprecation_ends_at", "published_at", "created_at", "updated_at"}
	out := ""
	for i, c := range cols {
		if i > 0 {
			out += ", "
		}
		out += p + "." + c
	}
	return out
}
