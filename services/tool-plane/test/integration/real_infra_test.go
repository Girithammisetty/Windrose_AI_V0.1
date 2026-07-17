package integration

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	segkafka "github.com/segmentio/kafka-go"

	"github.com/windrose-ai/tool-plane/internal/enforce"
	"github.com/windrose-ai/tool-plane/internal/events"
)

// waitAudit polls the outbox for an ai.tool_invoked.v1 event with the wanted
// decision (the relay would ship it to Kafka; here we assert the atomic write).
func waitAudit(t *testing.T, h *harness, tenant uuid.UUID, wantDecision string) {
	t.Helper()
	ctx := context.Background()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		envs, err := h.store.OutboxByType(ctx, tenant, events.EvToolInvoked)
		if err != nil {
			t.Fatalf("outbox read: %v", err)
		}
		for _, e := range envs {
			if e.Payload["decision"] == wantDecision {
				if e.Payload["args_digest"] == "" || e.Payload["args_digest"] == nil {
					t.Fatal("ai.tool_invoked.v1 must carry an args digest (BR-3)")
				}
				return
			}
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatalf("no ai.tool_invoked.v1{decision=%s} audit event within timeout", wantDecision)
}

// enforceNewKill builds a second KillRegistry sharing Redis (simulates another
// gateway replica) so AC-5 can prove pub/sub kill propagation across replicas.
func enforceNewKill(h *harness) *enforce.KillRegistry {
	kr := enforce.NewKillRegistry(h.rc)
	_ = kr.SyncFromStore(context.Background(), h.store)
	return kr
}

func waitKilled(t *testing.T, kr *enforce.KillRegistry, tenant uuid.UUID, tool, version string, want bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second) // TPL-FR-052 ≤5s propagation SLO
	for time.Now().Before(deadline) {
		if killed, _ := kr.IsKilled(tenant, tool, version); killed == want {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatalf("kill state %v not observed on replica within 5s", want)
}

// TestKafkaRoundTrip_ToolInvoked runs a real enforced call, drains the outbox
// through the REAL go-common Kafka producer to Redpanda, and consumes the
// ai.tool_invoked.v1 event off the topic — proving the audit event round-trips
// through real Kafka end to end.
func TestKafkaRoundTrip_ToolInvoked(t *testing.T) {
	h := mustHarness(t)
	ctx := context.Background()
	tenant := newTenant()
	be := echoBackend()
	defer be.Close()
	h.publishTool(t, "case.get", "Fetch case detail. Use when you need a case's current assignee.", "read", "none", caseGetSchema())
	h.registerBackend(t, "case-service", be.URL)
	h.enableTool(t, tenant, "case.get", nil, "", nil)
	h.seedGrant(ctx, tenant.String(), "user:u1", "wr:"+tenant.String()+":case:case/c1")

	token := h.agentToken(tenant.String(), "case-triage", "3", "user:u1", []string{"case.get"})
	if _, rerr := h.callMCP(t, token, "case.get", map[string]any{"case_id": "c1"}, nil); rerr != nil {
		t.Fatalf("call failed: %+v", rerr)
	}

	// Drain the outbox to REAL Kafka (Redpanda) via the shared go-common producer.
	pub := events.NewKafkaPublisher(ctx, []string{kafkaBroker}, "")
	defer func() { _ = pub.Close() }()
	relay := &events.Relay{Source: h.store, Publisher: pub}
	if err := relay.Drain(ctx); err != nil {
		t.Fatalf("relay drain to kafka: %v", err)
	}

	// Consume ai.tool_invoked.v1 and find our tenant's event.
	reader := segkafka.NewReader(segkafka.ReaderConfig{
		Brokers: []string{kafkaBroker}, Topic: events.TopicToolInvoked,
		GroupID: "tp-it-" + uuid.NewString(), StartOffset: segkafka.FirstOffset,
	})
	defer func() { _ = reader.Close() }()
	rctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	for {
		msg, err := reader.ReadMessage(rctx)
		if err != nil {
			t.Fatalf("no ai.tool_invoked.v1 for tenant %s off real Kafka: %v", tenant, err)
		}
		if string(msg.Key) == tenant.String() {
			return // round-tripped through real Kafka
		}
	}
}

// TestRLSIsolation_NonSuperuser proves Postgres RLS blocks cross-tenant reads
// when the app connects as a NOSUPERUSER/NOBYPASSRLS role (the whole suite does).
func TestRLSIsolation_NonSuperuser(t *testing.T) {
	h := mustHarness(t)
	ctx := context.Background()
	tenantA := newTenant()
	tenantB := newTenant()

	// Write an enablement row for tenant A under A's RLS session.
	h.enableTool(t, tenantA, "case.get", nil, "", nil)

	// Tenant A sees it.
	if s, err := h.store.GetTenantSettings(ctx, tenantA, "case.get"); err != nil || s == nil {
		t.Fatalf("tenant A must see its own row: %v", err)
	}
	// Tenant B must NOT (RLS hides it — cross-tenant reads as absent).
	if s, err := h.store.GetTenantSettings(ctx, tenantB, "case.get"); err != nil {
		t.Fatalf("unexpected error: %v", err)
	} else if s != nil {
		t.Fatal("RLS breach: tenant B read tenant A's enablement row")
	}

	// Direct SQL proof under the non-superuser role: count under B's GUC is 0.
	err := pgx.BeginFunc(ctx, h.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `SELECT set_config('app.tenant_id', $1, true)`, tenantB.String()); err != nil {
			return err
		}
		var n int
		if err := tx.QueryRow(ctx, `SELECT count(*) FROM tenant_tool_settings WHERE tool_id='case.get' AND tenant_id=$1`, tenantA).Scan(&n); err != nil {
			return err
		}
		if n != 0 {
			t.Fatalf("RLS breach: tenant B counted %d of tenant A's rows", n)
		}
		return nil
	})
	if err != nil {
		t.Fatalf("rls check tx: %v", err)
	}
}
