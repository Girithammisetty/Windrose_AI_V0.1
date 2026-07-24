package events

import (
	"context"
	"encoding/json"

	"github.com/google/uuid"

	gcevent "github.com/datacern-ai/go-common/event"

	"github.com/datacern-ai/case-service/internal/domain"
)

// Indexer is the search-projection port (satisfied by *search.Projector).
type Indexer interface {
	ProjectCase(ctx context.Context, tenant, id uuid.UUID) error
	EnsureTenantIndex(ctx context.Context, tenant uuid.UUID) error
}

// SearchIndexHandler returns a go-common Kafka handler that reprojects a case
// into OpenSearch on every case event (CASE-FR-041). Postgres is re-read as the
// source of truth, so the handler is idempotent and safe to replay.
func SearchIndexHandler(idx Indexer) func(ctx context.Context, env gcevent.Envelope) error {
	return func(ctx context.Context, env gcevent.Envelope) error {
		tenant, id, ok := ParseCaseURN(env.ResourceURN)
		if !ok {
			return nil // not a case-scoped event (e.g. security.*); skip
		}
		return idx.ProjectCase(ctx, tenant, id)
	}
}

// CaseCreator is the slice of the service used to auto-create cases from
// inference results (satisfied by a small adapter in main).
type CaseCreator interface {
	AutoCreateFromInference(ctx context.Context, tenant uuid.UUID, payload map[string]any) error
	UnassignUserCases(ctx context.Context, tenant, userID uuid.UUID) error
}

// InferenceHandler consumes inference.completed and, when auto_case=true,
// creates cases from output rows over threshold (CASE-FR-003). Idempotent by
// event_id (consumer dedup) + dedup_key.
func InferenceHandler(c CaseCreator) func(ctx context.Context, env gcevent.Envelope) error {
	return func(ctx context.Context, env gcevent.Envelope) error {
		if env.EventType != "inference.completed" {
			return nil
		}
		autoCase, _ := env.Payload["auto_case"].(bool)
		if !autoCase {
			return nil
		}
		return c.AutoCreateFromInference(ctx, env.TenantID, env.Payload)
	}
}

// IdentityHandler consumes user.deactivated / workspace.member.removed and
// unassigns the user's open cases (CASE-FR §6). Idempotent.
func IdentityHandler(c CaseCreator) func(ctx context.Context, env gcevent.Envelope) error {
	return func(ctx context.Context, env gcevent.Envelope) error {
		switch env.EventType {
		case "user.deactivated", "workspace.member.removed":
		default:
			return nil
		}
		userRaw, _ := env.Payload["user_id"].(string)
		userID, err := uuid.Parse(userRaw)
		if err != nil {
			return nil
		}
		return c.UnassignUserCases(ctx, env.TenantID, userID)
	}
}

// TenantHandler consumes identity-service's tenant.provisioned and creates the
// tenant's OpenSearch case index up front, mirroring rbac-service's
// tenant.provisioned projection seed. Without this, a freshly provisioned
// tenant has no cases-<tenant> index until its first case is written (or an
// operator runs POST /admin/reindex / doctor.sh's heal) and the Cases page
// 503s on search in the meantime. Idempotent (EnsureIndex no-ops if present).
func TenantHandler(idx Indexer) func(ctx context.Context, env gcevent.Envelope) error {
	return func(ctx context.Context, env gcevent.Envelope) error {
		if env.EventType != "tenant.provisioned" {
			return nil
		}
		return idx.EnsureTenantIndex(ctx, env.TenantID)
	}
}

// systemOp builds the attribution for consumer-initiated changes. The master
// envelope (MASTER-FR-031) constrains actor.type to {user,service,agent,platform},
// so a background consumer emits actor={service,case-service} — aligned with the
// other Go services (identity/rbac) so audit-service accepts the envelope.
func systemOp(tenant uuid.UUID) domain.Op {
	return domain.Op{Tenant: tenant, Actor: domain.Actor{Type: "service", ID: "case-service"}}
}

var _ = json.Marshal

// TriggerApplier is the slice of internal/triggers used by the ingestion
// consumer (realtime-decisioning INC-1).
type TriggerApplier interface {
	ApplyIngestionCompleted(ctx context.Context, tenant uuid.UUID, payload map[string]any) error
}

// IngestionTriggerHandler consumes ingestion.completed and applies the
// tenant's enabled case triggers. Idempotent: consumer dedup (event_id) plus
// per-case DedupKey(dataset_urn, row_pk) inside CreateCases.
func IngestionTriggerHandler(a TriggerApplier) func(ctx context.Context, env gcevent.Envelope) error {
	return func(ctx context.Context, env gcevent.Envelope) error {
		if env.EventType != "ingestion.completed" {
			return nil
		}
		return a.ApplyIngestionCompleted(ctx, env.TenantID, env.Payload)
	}
}
