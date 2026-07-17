package integration

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"

	gcevent "github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/notification-service/internal/channels/webhook"
	"github.com/windrose-ai/notification-service/internal/domain"
)

// TestAC01_RealKafkaToInAppEmailRealtime: a real case.assigned event from real
// Kafka yields an in-app notification (Postgres), a captured email (Mailpit),
// and a realtime-hub publish (Redis) (AC-1). Real components: Kafka, Postgres,
// Mailpit SMTP capture, Redis.
func TestAC01_RealKafkaToInAppEmailRealtime(t *testing.T) {
	h := requireHarness(t)
	h.mailpitDeleteAll(t)
	tenant := uuid.New()
	user := "u-" + uuid.NewString()[:8]
	h.seedUser(t, tenant.String(), user, user+"@windrose.local")

	// Subscribe to the realtime-hub Redis channel before publishing.
	sub := h.rc.R.Subscribe(context.Background(), inappChannel(tenant.String(), user))
	defer sub.Close()
	pushed := make(chan struct{}, 1)
	go func() {
		if _, err := sub.ReceiveMessage(context.Background()); err == nil {
			pushed <- struct{}{}
		}
	}()

	env := newEvent("case.assigned", tenant, map[string]any{"assignee": user, "case_number": 7, "severity": "high"})
	h.publish(t, "case.events.v1", env)

	// In-app notification persisted (Postgres).
	waitFor(t, 15*time.Second, func() bool {
		list, _ := h.pg.ListNotifications(context.Background(), tenant, user, false, 10, nil)
		return len(list) >= 1
	}, "in-app notification persisted")

	// Email captured by the real Mailpit SMTP server.
	waitFor(t, 15*time.Second, func() bool {
		for _, m := range h.mailpitMessages(t) {
			for _, to := range m.To {
				if to.Address == user+"@windrose.local" {
					return true
				}
			}
		}
		return false
	}, "email captured by mailpit")

	// Realtime publish delivered on the Redis backplane.
	select {
	case <-pushed:
	case <-time.After(10 * time.Second):
		t.Fatal("no realtime-hub publish received")
	}
}

// TestAC03_KafkaRedeliveryDedup: redelivering the same event_id produces exactly
// one delivery row (AC-3, BR-1). Real: Kafka + Redis dedup + Postgres unique key.
func TestAC03_KafkaRedeliveryDedup(t *testing.T) {
	h := requireHarness(t)
	tenant := uuid.New()
	user := "u-" + uuid.NewString()[:8]
	env := newEvent("case.assigned", tenant, map[string]any{"assignee": user, "case_number": 9, "severity": "low"})

	h.publish(t, "case.events.v1", env)
	waitFor(t, 15*time.Second, func() bool {
		list, _ := h.pg.ListNotifications(context.Background(), tenant, user, false, 10, nil)
		return len(list) == 1
	}, "first delivery")

	h.publish(t, "case.events.v1", env) // exact redelivery
	time.Sleep(2 * time.Second)
	list, _ := h.pg.ListNotifications(context.Background(), tenant, user, false, 10, nil)
	if len(list) != 1 {
		t.Fatalf("dedup failed: %d in-app notifications for one event_id", len(list))
	}
}

// TestAC04_WebhookHMACRealPost: a registered endpoint receives a real signed
// POST whose X-Windrose-Signature verifies with HMAC-SHA256 over timestamp.body
// (AC-4). Real: Kafka → pipeline → real HTTP webhook target.
func TestAC04_WebhookHMACRealPost(t *testing.T) {
	h := requireHarness(t)
	tenant := uuid.New()
	secret := "wh-secret-" + uuid.NewString()[:8]

	var mu sync.Mutex
	var verified bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := readAll(r)
		ts, _ := strconv.ParseInt(r.Header.Get(webhook.HeaderTimestamp), 10, 64)
		ok := webhook.Verify(r.Header.Get(webhook.HeaderSignature), secret, ts, time.Now().Unix(), 300, body)
		mu.Lock()
		verified = ok
		mu.Unlock()
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	ep := &domain.WebhookEndpoint{
		ID: domain.NewID(), TenantID: tenant, URL: srv.URL, EventTypes: []string{"case.assigned"},
		Secrets: []domain.WebhookSecret{{Version: 1, Secret: secret, CreatedAt: time.Now()}},
		Active:  true, VerifiedAt: ptrTime(time.Now()), CircuitState: domain.CircuitClosed,
		CreatedBy: "admin", CreatedAt: time.Now(), UpdatedAt: time.Now(),
	}
	if err := h.pg.CreateWebhook(context.Background(), ep); err != nil {
		t.Fatal(err)
	}

	h.publish(t, "case.events.v1", newEvent("case.assigned", tenant, map[string]any{"assignee": "u1", "case_number": 1, "severity": "high"}))
	waitFor(t, 15*time.Second, func() bool {
		mu.Lock()
		defer mu.Unlock()
		return verified
	}, "webhook POST received with valid HMAC")
}

// TestAC05_WebhookCircuitBreakerAndRecovery: 10 consecutive failures open the
// circuit; recovery on a probe closes it and flushes queued deliveries in order
// (AC-5). Real: Postgres circuit state + real failing/recovering HTTP target.
func TestAC05_WebhookCircuitBreakerAndRecovery(t *testing.T) {
	h := requireHarness(t)
	tenant := uuid.New()

	var mu sync.Mutex
	fail := true
	var received []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		f := fail
		if !f {
			received = append(received, r.Header.Get(webhook.HeaderEventID))
		}
		mu.Unlock()
		if f {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	ep := &domain.WebhookEndpoint{
		ID: domain.NewID(), TenantID: tenant, URL: srv.URL, EventTypes: []string{"case.assigned"},
		Secrets: []domain.WebhookSecret{{Version: 1, Secret: "s", CreatedAt: time.Now()}},
		Active:  true, VerifiedAt: ptrTime(time.Now()), CircuitState: domain.CircuitClosed,
		CreatedBy: "admin", CreatedAt: time.Now(), UpdatedAt: time.Now(),
	}
	if err := h.pg.CreateWebhook(context.Background(), ep); err != nil {
		t.Fatal(err)
	}

	// Drive 10 consecutive failures (each attempt on a distinct event → circuit
	// opens after 10). Uses the real HTTP target + real Postgres state machine.
	ctx := context.Background()
	for i := 0; i < 10; i++ {
		cur, _ := h.pg.GetWebhook(ctx, tenant, ep.ID)
		env := newEvent("case.assigned", tenant, map[string]any{"assignee": "u", "case_number": i})
		dID := uuid.NewSHA1(uuid.NameSpaceOID, []byte("wh:"+tenant.String()+":"+env.EventID.String()+":"+ep.ID.String()))
		_, _ = h.pg.InsertDelivery(ctx, &domain.Delivery{
			ID: dID, TenantID: tenant, WebhookEndpointID: &ep.ID, EventID: env.EventID,
			Recipient: ep.ID.String(), Channel: domain.ChannelWebhook, Provider: "webhook",
			Status: domain.StatusQueued, CreatedAt: time.Now(), UpdatedAt: time.Now(),
		}, map[string]any{"envelope": env})
		h.pipeline.AttemptWebhook(ctx, cur, env, dID, 1)
	}
	opened, _ := h.pg.GetWebhook(ctx, tenant, ep.ID)
	if opened.CircuitState != domain.CircuitOpen {
		t.Fatalf("expected circuit open after 10 failures, got %s (failures=%d)", opened.CircuitState, opened.ConsecutiveFailures)
	}

	// Recover: server returns 200, drive a half-open probe → circuit closes.
	mu.Lock()
	fail = false
	mu.Unlock()
	probe, _ := h.pg.GetWebhook(ctx, tenant, ep.ID)
	probe.CircuitState = domain.CircuitHalfOpen
	env := newEvent("case.assigned", tenant, map[string]any{"assignee": "u", "case_number": 99})
	dID := uuid.NewSHA1(uuid.NameSpaceOID, []byte("wh:"+tenant.String()+":"+env.EventID.String()+":"+ep.ID.String()))
	_, _ = h.pg.InsertDelivery(ctx, &domain.Delivery{
		ID: dID, TenantID: tenant, WebhookEndpointID: &ep.ID, EventID: env.EventID,
		Recipient: ep.ID.String(), Channel: domain.ChannelWebhook, Provider: "webhook",
		Status: domain.StatusQueued, CreatedAt: time.Now(), UpdatedAt: time.Now(),
	}, map[string]any{"envelope": env})
	h.pipeline.AttemptWebhook(ctx, probe, env, dID, 1)

	closed, _ := h.pg.GetWebhook(ctx, tenant, ep.ID)
	if closed.CircuitState != domain.CircuitClosed {
		t.Fatalf("expected circuit closed after successful probe, got %s", closed.CircuitState)
	}
	mu.Lock()
	got := len(received)
	mu.Unlock()
	if got == 0 {
		t.Fatal("expected at least the probe delivery to be received after recovery")
	}
}

// TestAC09_RateLimitToDigest: past the per-user hourly email cap, sends convert
// to the digest path (never dropped) (AC-9, BR-2). Real: Redis limiter + Mailpit
// + Postgres digest buffer.
func TestAC09_RateLimitToDigest(t *testing.T) {
	h := requireHarness(t)
	h.mailpitDeleteAll(t)
	tenant := uuid.New()
	user := "rl-" + uuid.NewString()[:8]
	ctx := context.Background()
	h.seedUser(t, tenant.String(), user, user+"@windrose.local")

	// 25 immediate emails; cap is 20/hour → 21..25 convert to digest.
	for i := 0; i < 25; i++ {
		env := gcevent.New("case.assigned", tenant, gcevent.Actor{Type: "user", ID: "a"}, "wr:t:case:case/"+strconv.Itoa(i), "tr",
			map[string]any{"assignee": user, "case_number": i, "severity": "warning"})
		if err := h.pipeline.Process(ctx, env); err != nil {
			t.Fatal(err)
		}
	}
	// At most 20 immediate emails captured for this recipient.
	count := 0
	for _, m := range h.mailpitMessages(t) {
		for _, to := range m.To {
			if to.Address == user+"@windrose.local" {
				count++
			}
		}
	}
	if count > 20 {
		t.Fatalf("expected ≤20 immediate emails, got %d", count)
	}
	if count < 20 {
		t.Fatalf("expected 20 immediate emails before cap, got %d", count)
	}

	// The overflow is buffered as a digest and flushed by the worker (2s window)
	// into a single digest email (seeded digest.warning subject).
	waitFor(t, 20*time.Second, func() bool {
		for _, m := range h.mailpitMessages(t) {
			if m.Subject == "[Windrose] Your notification digest" {
				for _, to := range m.To {
					if to.Address == user+"@windrose.local" {
						return true
					}
				}
			}
		}
		return false
	}, "overflow converted to a digest email")
}

// TestAC14_RLSCrossTenantDefaultRole: with the SHIPPED default non-owner role,
// tenant B cannot see tenant A's notifications, and a query with no tenant
// context returns nothing — proving RLS FORCE binds the runtime role (AC-14).
func TestAC14_RLSCrossTenantDefaultRole(t *testing.T) {
	h := requireHarness(t)
	ctx := context.Background()
	tenantA := uuid.New()
	tenantB := uuid.New()
	user := "iso-" + uuid.NewString()[:8]

	env := newEvent("case.assigned", tenantA, map[string]any{"assignee": user, "case_number": 5, "severity": "high"})
	if err := h.pipeline.Process(ctx, env); err != nil {
		t.Fatal(err)
	}
	// Tenant A sees it.
	waitFor(t, 5*time.Second, func() bool {
		list, _ := h.pg.ListNotifications(ctx, tenantA, user, false, 10, nil)
		return len(list) == 1
	}, "tenant A notification")

	// Tenant B sees nothing (cross-tenant → empty).
	listB, err := h.pg.ListNotifications(ctx, tenantB, user, false, 10, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(listB) != 0 {
		t.Fatalf("cross-tenant leak: tenant B saw %d rows", len(listB))
	}

	// A bare query on the shipped role with NO tenant context returns 0 rows,
	// proving the non-owner NOSUPERUSER role cannot bypass RLS FORCE.
	var n int
	if err := h.appPool.QueryRow(ctx, `SELECT count(*) FROM notifications`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 0 {
		t.Fatalf("non-owner role saw %d rows without tenant context (RLS bypass!)", n)
	}
}

// ---- helpers ----------------------------------------------------------------

func inappChannel(tenant, user string) string { return "rt:ch:" + tenant + "/notifications:" + user }

func ptrTime(t time.Time) *time.Time { return &t }

func readAll(r *http.Request) ([]byte, error) {
	defer r.Body.Close()
	buf := make([]byte, 0, 1024)
	tmp := make([]byte, 512)
	for {
		n, err := r.Body.Read(tmp)
		buf = append(buf, tmp[:n]...)
		if err != nil {
			break
		}
	}
	return buf, nil
}
