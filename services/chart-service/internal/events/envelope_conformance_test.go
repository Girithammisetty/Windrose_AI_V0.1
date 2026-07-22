package events

import (
	"testing"

	"github.com/google/uuid"

	"github.com/datacern-ai/go-common/event"
)

// TestNewEnvelopeConformsToMasterContract builds an envelope the same way a
// real chart-service handler does (see handlers_charts.go's handleCreateChart)
// and checks it against the shared MASTER-FR-031/041 conformance validator
// (BRD 58 WS5, libs/go-common/event/validate.go). This is additive coverage
// only — it does not change events.New or any envelope-construction code.
func TestNewEnvelopeConformsToMasterContract(t *testing.T) {
	tenant := uuid.New()
	chartID := uuid.New()
	urn := URN(tenant, "chart", chartID.String())

	ev := New(ChartCreated, tenant, "user", "user-123", urn, "trace-abc",
		map[string]any{"chart_id": chartID.String(), "dashboard_id": uuid.New().String(),
			"chart_type": "bar", "chart_version": 1})

	if err := event.Validate(ev); err != nil {
		t.Fatalf("events.New envelope failed shared conformance validator: %v", err)
	}
}
