package events

import (
	"testing"

	"github.com/stretchr/testify/require"

	gcevent "github.com/datacern-ai/go-common/event"

	"github.com/datacern-ai/query-service/internal/domain"
)

// TestNewEnvelope_ConformsToMasterContract exercises a real emitting call
// site (handleSaveQuery's query.saved envelope, internal/api/handlers_queries.go)
// through NewEnvelope and the existing toMaster conversion used by
// KafkaPublisher.Publish, asserting the result satisfies the shared
// event-envelope conformance validator (MASTER-FR-031/041, BRD 58 WS5).
func TestNewEnvelope_ConformsToMasterContract(t *testing.T) {
	op := domain.Op{
		Tenant:  domain.NewID(),
		Actor:   domain.Actor{Type: "user", ID: "u-1"},
		TraceID: "trace-1",
		Caller:  domain.CallerUser,
		UserID:  "u-1",
	}
	queryID := domain.NewID()

	env := NewEnvelope(EvQuerySaved, op, QueryURN(op.Tenant, queryID),
		map[string]any{"name": "top_disputes", "version_no": 1})

	require.NoError(t, gcevent.Validate(toMaster(env)))
}

// TestNewEnvelope_ViaAgentConformsToMasterContract covers the OBO
// (on-behalf-of) attribution path (MASTER-FR-041): op.ViaAgent set and
// actor.type "agent", another real shape NewEnvelope must produce (see
// exec/broker.go execution envelopes emitted for agent-initiated queries).
func TestNewEnvelope_ViaAgentConformsToMasterContract(t *testing.T) {
	op := domain.Op{
		Tenant:   domain.NewID(),
		Actor:    domain.Actor{Type: "agent", ID: "ml-engineer"},
		ViaAgent: &domain.ViaAgent{AgentID: "ml-engineer", Version: "1"},
		TraceID:  "trace-2",
		Caller:   domain.CallerAgent,
		UserID:   "u-1",
	}
	queryID := domain.NewID()

	env := NewEnvelope(EvQuerySaved, op, QueryURN(op.Tenant, queryID),
		map[string]any{"name": "top_disputes", "version_no": 1})

	require.NoError(t, gcevent.Validate(toMaster(env)))
}
