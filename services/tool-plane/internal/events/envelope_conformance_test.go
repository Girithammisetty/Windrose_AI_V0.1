package events

import (
	"testing"

	"github.com/google/uuid"

	gcevent "github.com/datacern-ai/go-common/event"
	"github.com/datacern-ai/tool-plane/internal/domain"
)

// TestEnvelopeConformance builds an envelope the same way a real tenant-scoped
// call site does (see internal/api/handlers_admin.go handleEnablement, which
// emits tool.events.v1 EvTenantToolEnabled/Disabled) and checks that, once
// converted to the shared platform envelope via toMaster, it satisfies the
// go-common conformance validator (MASTER-FR-031/041).
func TestEnvelopeConformance(t *testing.T) {
	tenant := uuid.New()
	actor := domain.Actor{Type: "user", ID: "user-123"}
	env := NewEnvelope(TopicToolEvents, EvTenantToolEnabled, tenant, actor, nil,
		domain.ToolURN(tenant.String(), "case-lookup", ""), "trace-abc",
		map[string]any{"tool_id": "case-lookup", "enabled": true})

	if err := gcevent.Validate(toMaster(env)); err != nil {
		t.Fatalf("envelope failed conformance validation: %v", err)
	}
}

// TestEnvelopeConformance_PlatformScoped builds an envelope the same way a
// platform-scoped call site does (see internal/api/handlers_tools.go
// handleRegisterTool, which emits tool.events.v1 EvToolRegistered with
// domain.PlatformTenant) and checks it satisfies the shared conformance
// validator. Regression test for domain.PlatformTenant having been uuid.Nil:
// every platform-scoped tool.events.v1 lifecycle event (registered,
// version_published, deprecated, retired, killed, unkilled, sla_breached,
// quarantined) rode a nil tenant_id, which gcevent.Validate (and
// audit-service's consumption-side ValidateEnvelope) rejects — those events
// would DLQ as ENVELOPE_INVALID.
func TestEnvelopeConformance_PlatformScoped(t *testing.T) {
	if domain.PlatformTenant == uuid.Nil {
		t.Fatal("domain.PlatformTenant must not be uuid.Nil: event.Validate rejects a nil tenant_id")
	}

	actor := domain.Actor{Type: "user", ID: "admin-42"}
	env := NewEnvelope(TopicToolEvents, EvToolRegistered, domain.PlatformTenant, actor, nil,
		domain.ToolURN("platform", "case.assign", ""), "trace-ghi",
		map[string]any{"tool_id": "case.assign", "owner_service": "case-service"})

	if err := gcevent.Validate(toMaster(env)); err != nil {
		t.Fatalf("platform-scoped envelope failed conformance validation: %v", err)
	}
}

// TestEnvelopeConformance_WithViaAgent covers the dual-attribution
// (MASTER-FR-041) OBO path, mirroring internal/enforce/audit.go's
// EvToolInvoked emission on ai.tool_invoked.v1.
func TestEnvelopeConformance_WithViaAgent(t *testing.T) {
	tenant := uuid.New()
	actor := domain.Actor{Type: "agent", ID: "agent-7"}
	via := &domain.ViaAgent{AgentID: "agent-7", Version: "v3"}
	env := NewEnvelope(TopicToolInvoked, EvToolInvoked, tenant, actor, via,
		domain.ToolURN(tenant.String(), "case-lookup", "1"), "trace-def",
		map[string]any{"decision": DecisionAllowed})

	if err := gcevent.Validate(toMaster(env)); err != nil {
		t.Fatalf("envelope failed conformance validation: %v", err)
	}
}
