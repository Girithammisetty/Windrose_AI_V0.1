// Package events defines chart-service's emitted event types, the outbox
// envelope builders (MASTER-FR-031), and the real Kafka invalidation consumers
// (CHART-FR-031). Emitted events flow through the transactional outbox
// (MASTER-FR-034) and the shared go-common relay to Redpanda.
package events

import (
	"github.com/google/uuid"

	"github.com/windrose-ai/go-common/event"
)

// Topic chart-service publishes to (MASTER-FR-030).
const Topic = "chart.events.v1"

// Consumed topics (CHART-FR-031 / §6).
const (
	TopicSemantic = "semantic.events.v1"
	TopicQuery    = "query.events.v1"
	TopicDataset  = "dataset.events.v1"
	TopicRBAC     = "rbac.events.v1"
)

// Emitted event types.
const (
	DashboardCreated  = "dashboard.created"
	DashboardUpdated  = "dashboard.updated"
	DashboardArchived = "dashboard.archived"
	DashboardRestored = "dashboard.restored"
	DashboardDeleted  = "dashboard.deleted"
	ChartCreated      = "chart.created"
	ChartUpdated      = "chart.updated"
	ChartDeleted      = "chart.deleted"
	ChartLinkCreated  = "chart.link.created"
	ChartLinkRemoved  = "chart.link.removed"
	ExportCompleted   = "chart.export.completed"
	ExportFailed      = "chart.export.failed"
)

// URN builds a chart-service resource URN (MASTER-FR-013).
func URN(tenant uuid.UUID, resourceType, id string) string {
	return "wr:" + tenant.String() + ":chart:" + resourceType + "/" + id
}

// New builds an envelope for the outbox (MASTER-FR-031).
func New(eventType string, tenant uuid.UUID, actorType, actorID, resourceURN, traceID string, payload map[string]any) event.Envelope {
	return event.New(eventType, tenant, event.Actor{Type: actorType, ID: actorID}, resourceURN, traceID, payload)
}
