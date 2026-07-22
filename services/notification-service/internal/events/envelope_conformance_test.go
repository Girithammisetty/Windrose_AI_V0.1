package events

import (
	"testing"

	"github.com/google/uuid"

	gcevent "github.com/datacern-ai/go-common/event"
	"github.com/datacern-ai/notification-service/internal/domain"
)

// TestNewEnvelopeConformsToMasterContract builds an envelope the same way
// notification-service's real deliverInApp path does (see
// internal/pipeline/inapp.go) and checks it against the shared
// MASTER-FR-031/041 conformance validator (BRD 58 WS5,
// libs/go-common/event/validate.go). Additive coverage only — does not
// change events.New/FromOp or any envelope-construction code.
func TestNewEnvelopeConformsToMasterContract(t *testing.T) {
	tenant := uuid.New()
	urn := "wr:" + tenant.String() + ":chart:dashboard/abc"

	ev := New(EvNotificationCreated, tenant,
		gcevent.Actor{Type: "service", ID: "notification-service"}, urn, "trace-abc",
		map[string]any{"notification_id": uuid.New().String(), "user_id": "user-123",
			"event_type": "chart.created", "severity": "info"})

	if err := gcevent.Validate(ev); err != nil {
		t.Fatalf("events.New envelope failed shared conformance validator: %v", err)
	}
}

// TestFromOpEnvelopeConformsToMasterContract covers the FromOp construction
// path used on the audit side (see internal/api/middleware.go).
func TestFromOpEnvelopeConformsToMasterContract(t *testing.T) {
	op := domain.Op{
		Tenant:  uuid.New(),
		Actor:   domain.Actor{Type: "user", ID: "user-123"},
		UserID:  "user-123",
		TraceID: "trace-abc",
	}

	ev := FromOp(EvPermissionDenied, op, "", map[string]any{"action": "case.read", "path": "/cases/1"})

	if err := gcevent.Validate(ev); err != nil {
		t.Fatalf("events.FromOp envelope failed shared conformance validator: %v", err)
	}
}
