package events

import (
	"testing"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	gcevent "github.com/datacern-ai/go-common/event"
	"github.com/datacern-ai/usage-service/internal/domain"
)

// TestEnvelopeConformance builds an envelope the same way a real call site
// does (see internal/store/budgets.go CreateBudget) and checks that, once
// converted to the shared platform envelope via toMaster, it satisfies the
// go-common conformance validator (MASTER-FR-031/041).
func TestEnvelopeConformance(t *testing.T) {
	tenant := uuid.New()
	budgetID := uuid.New()
	op := domain.Op{
		Tenant:  tenant,
		Actor:   domain.Actor{Type: "service", ID: "usage-service"},
		TraceID: "trace-123",
	}
	env := NewEnvelope(EvBudgetCreated, op, domain.BudgetURN(tenant, budgetID), map[string]any{
		"meter_key": "storage.gb_month",
		"window":    "monthly",
	})

	err := gcevent.Validate(toMaster(env))

	require.NoError(t, err)
}

// TestEnvelopeConformance_WithViaAgent covers the dual-attribution
// (MASTER-FR-041) OBO path where op.ViaAgent is populated.
func TestEnvelopeConformance_WithViaAgent(t *testing.T) {
	tenant := uuid.New()
	op := domain.Op{
		Tenant:   tenant,
		Actor:    domain.Actor{Type: "agent", ID: "agent-42"},
		ViaAgent: &domain.ViaAgent{AgentID: "agent-42", Version: "v1"},
		TraceID:  "trace-456",
	}
	env := NewEnvelope(EvCrossTenantDenied, op, "", map[string]any{
		"attempted_tenant": tenant.String(),
	})

	err := gcevent.Validate(toMaster(env))

	require.NoError(t, err)
}
