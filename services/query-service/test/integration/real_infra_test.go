package integration

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	gcevent "github.com/windrose-ai/go-common/event"
	gckafka "github.com/windrose-ai/go-common/kafka"
	"github.com/windrose-ai/go-common/opaclient"
	"github.com/windrose-ai/go-common/redisx"

	"github.com/windrose-ai/query-service/internal/api"
	"github.com/windrose-ai/query-service/internal/authz"
	"github.com/windrose-ai/query-service/internal/domain"
	"github.com/windrose-ai/query-service/internal/events"
)

// These tests exercise the REAL runtime adapters against the compose infra
// (deploy/docker-compose.dev.yml): Redpanda (Kafka), Redis, and the OPA sidecar
// loading the windrose.authz_input Rego bundle. They auto-skip with a clear
// message when the required service is unreachable (CONVENTIONS testing tiers).

func kafkaBrokers() string {
	if v := os.Getenv("KAFKA_BROKERS"); v != "" && v != "false" {
		return v
	}
	return "localhost:9092"
}

func redisAddr() string {
	if v := os.Getenv("REDIS_ADDR"); v != "" {
		return v
	}
	return "localhost:6379"
}

func opaURL() string {
	if v := os.Getenv("OPA_URL"); v != "" {
		return v
	}
	return "http://localhost:8281"
}

func requireRedpanda(t *testing.T) {
	t.Helper()
	c, err := net.DialTimeout("tcp", kafkaBrokers(), 2*time.Second)
	if err != nil {
		t.Skipf("Redpanda/Kafka not reachable on %s: %v (bring up deploy/docker-compose.dev.yml)", kafkaBrokers(), err)
	}
	_ = c.Close()
}

func requireRedis(t *testing.T) {
	t.Helper()
	rc := redisx.New(redisAddr())
	defer func() { _ = rc.Close() }()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := rc.Ping(ctx); err != nil {
		t.Skipf("Redis not reachable on %s: %v (bring up deploy/docker-compose.dev.yml)", redisAddr(), err)
	}
}

func requireOPA(t *testing.T) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, opaURL()+"/health", nil)
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Skipf("OPA not reachable on %s: %v (bring up deploy/docker-compose.dev.yml)", opaURL(), err)
	}
	_ = res.Body.Close()
}

// TestRealKafkaPublishAndConsume proves a query-execution event is published to
// REAL Kafka (Redpanda) by the shared go-common producer draining the
// transactional outbox, and consumed back by the shared go-common consumer
// group (MASTER-FR-030/031/034). No in-memory publisher is in the path.
func TestRealKafkaPublishAndConsume(t *testing.T) {
	h := requireHarness(t)
	requireRedpanda(t)
	ctx := context.Background()

	tenant := h.newTenant()
	tok := h.token(t, tenant, domain.TypUser, "kafka-user", nil)

	// Run a real query -> commits execution.started + execution.succeeded to the
	// PG outbox.
	r := h.do(t, "POST", "/api/v1/sql/run", tok,
		map[string]any{"sql": "SELECT id FROM {{dataset('Orders')}}", "cache": false}, nil)
	require.Equal(t, http.StatusAccepted, r.status, "%v", r.body)
	execID := data(r)["execution_id"].(string)
	h.waitStatus(t, tok, execID, domain.StatusSucceeded)

	// Drain the committed outbox to REAL Kafka via the shared producer.
	kp := events.NewKafkaPublisher(ctx, []string{kafkaBrokers()}, os.Getenv("SCHEMA_REGISTRY_URL"))
	defer func() { _ = kp.Close() }()
	relay := &events.Relay{Source: h.pg, Publisher: kp, Batch: 1000}
	require.NoError(t, relay.Drain(ctx), "publish outbox batch to real Kafka")

	// Consume back from the real topic via the shared consumer group.
	got := make(chan gcevent.Envelope, 256)
	cctx, cancel := context.WithCancel(ctx)
	defer cancel()
	cg := gckafka.NewConsumerGroup(gckafka.ConsumerConfig{
		Brokers: []string{kafkaBrokers()},
		GroupID: "query-it-" + uuid.NewString(),
		Topics:  []string{events.Topic},
		Handler: func(_ context.Context, env gcevent.Envelope) error {
			if env.TenantID == tenant {
				select {
				case got <- env:
				default:
				}
			}
			return nil
		},
	})
	go cg.Run(cctx)
	defer func() { _ = cg.Close() }()

	seen := map[string]bool{}
	deadline := time.After(45 * time.Second)
	for len(seen) < 2 {
		select {
		case env := <-got:
			require.Equal(t, tenant, env.TenantID)
			require.NotEqual(t, uuid.Nil, env.EventID)
			require.NotEmpty(t, env.TraceID, "trace id propagated onto the wire")
			seen[env.EventType] = true
		case <-deadline:
			t.Fatalf("did not observe both execution events on real Kafka; saw %v", seen)
		}
	}
	require.True(t, seen[events.EvExecutionStarted], "execution.started on real Kafka")
	require.True(t, seen[events.EvExecutionSucceeded], "execution.succeeded on real Kafka")
}

// TestRealOPAAuthorizationDecision proves runtime authorization goes through the
// REAL OPA container: the shared opaclient loads the caller's permissions_flat
// projection from REAL Redis (rbac key scheme) and evaluates it against the OPA
// sidecar's windrose.authz_input bundle (MASTER-FR-012). Verified both directly
// and end-to-end through the RequireAction HTTP middleware.
func TestRealOPAAuthorizationDecision(t *testing.T) {
	h := requireHarness(t)
	requireRedis(t)
	requireOPA(t)
	ctx := context.Background()

	rc := redisx.New(redisAddr())
	defer func() { _ = rc.Close() }()

	tenant := uuid.New()
	allowUser := "opa-allow-" + uuid.NewString()
	action := authz.ActionExecRead // query.execution.read (tenant-scoped)

	// Seed the projection slice the policy evaluates (rbac-service key scheme).
	// The catalog key is a shared global on the dev-stack Redis — merge, never
	// replace (setJSON's 10m TTL would expire the whole platform catalog).
	require.NoError(t, opaclient.SeedCatalogActions(ctx, rc, map[string]bool{action: false}))
	setJSON(t, rc, fmt.Sprintf("perm:%s:%s:flags", tenant, allowUser), map[string]any{"admin": false, "ws_admin": []string{}})
	setJSON(t, rc, fmt.Sprintf("perm:%s:%s:actions", tenant, allowUser), map[string]any{"actions": []string{action}})

	az := authz.NewOPAClient(opaURL(), redisAddr())

	// Direct decision: real Redis projection load + real OPA bundle eval.
	require.True(t, az.Allow(ctx, authz.Input{
		Subject: authz.Subject{ID: allowUser, Typ: domain.TypUser},
		Action:  action, Tenant: tenant.String(),
	}), "seeded user must be allowed by real OPA")

	denyUser := "opa-deny-" + uuid.NewString() // no projection -> projection_miss
	require.False(t, az.Allow(ctx, authz.Input{
		Subject: authz.Subject{ID: denyUser, Typ: domain.TypUser},
		Action:  action, Tenant: tenant.String(),
	}), "user with no projection must be denied by real OPA")

	// End-to-end through the runtime HTTP middleware (RequireAction -> OPAClient).
	server := &api.Server{
		Store: h.pg, Broker: h.broker, Results: h.broker.Results,
		Authz:        az,
		Verifier:     api.NewVerifierStatic(&h.key.PublicKey, "windrose-test", "windrose"),
		ExportSecret: []byte("integration-secret"),
	}
	httpSrv := httptest.NewServer(server.Router())
	defer httpSrv.Close()

	require.Equal(t, http.StatusOK, getStatus(t, httpSrv.URL+"/api/v1/executions",
		h.token(t, tenant, domain.TypUser, allowUser, nil)), "allowed user reaches the handler")
	require.Equal(t, http.StatusForbidden, getStatus(t, httpSrv.URL+"/api/v1/executions",
		h.token(t, tenant, domain.TypUser, denyUser, nil)), "denied user gets 403 from real OPA")
}

func setJSON(t *testing.T, rc *redisx.Client, key string, val any) {
	t.Helper()
	raw, err := json.Marshal(val)
	require.NoError(t, err)
	require.NoError(t, rc.Set(context.Background(), key, string(raw), 10*time.Minute))
}

func getStatus(t *testing.T, url, token string) int {
	t.Helper()
	req, err := http.NewRequest(http.MethodGet, url, nil)
	require.NoError(t, err)
	req.Header.Set("Authorization", "Bearer "+token)
	res, err := http.DefaultClient.Do(req)
	require.NoError(t, err)
	_ = res.Body.Close()
	return res.StatusCode
}
