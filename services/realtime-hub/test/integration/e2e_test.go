//go:build integration

package integration

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/gorilla/websocket"

	"github.com/windrose-ai/realtime-hub/internal/fanout"
)

// postInternal calls the authenticated internal publish API on the instance's
// SEPARATE internal listener (RTH-FR-021), presenting a service producer token.
func postInternal(t *testing.T, in *instance, tenant, topic, eventID string, payload map[string]any, ttl int) {
	t.Helper()
	resp := rawPublish(t, in.InternalURL(), serviceToken(t), tenant, topic, eventID, payload, ttl)
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("internal publish status %d", resp.StatusCode)
	}
}

// rawPublish issues a publish with an explicit (possibly empty) bearer token so
// tests can assert the auth boundary.
func rawPublish(t *testing.T, internalURL, tkn, tenant, topic, eventID string, payload map[string]any, ttl int) *http.Response {
	t.Helper()
	pj, _ := json.Marshal(payload)
	body, _ := json.Marshal(map[string]any{
		"tenant_id": tenant, "topic": topic, "event_id": eventID,
		"payload_json": json.RawMessage(pj), "ttl_seconds": ttl,
	})
	req, _ := http.NewRequest("POST", internalURL+"/internal/v1/publish", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	if tkn != "" {
		req.Header.Set("Authorization", "Bearer "+tkn)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("internal publish: %v", err)
	}
	return resp
}

func connectStatus(t *testing.T, base, tkn string, topicList []string, hdrs map[string]string) (int, context.CancelFunc) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	q := "?topics=" + join(topicList)
	req, _ := http.NewRequestWithContext(ctx, "GET", base+"/api/v1/stream"+q, nil)
	req.Header.Set("Accept", "text/event-stream")
	req.Header.Set("Authorization", "Bearer "+tkn)
	for k, v := range hdrs {
		req.Header.Set(k, v)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		cancel()
		t.Fatal(err)
	}
	if resp.StatusCode != http.StatusOK {
		resp.Body.Close()
		cancel()
		return resp.StatusCode, func() {}
	}
	// Keep the body open (connection stays live) until cancel.
	go func() { <-ctx.Done(); resp.Body.Close() }()
	return resp.StatusCode, cancel
}

func join(s []string) string {
	out := ""
	for i, x := range s {
		if i > 0 {
			out += ","
		}
		out += x
	}
	return out
}

// TestAC01_SSEStreamsTopicsWithProducerIDs: a valid JWT + topics
// run-status:<urn>,notifications:<me> stream, and each event's id equals the
// producer event_id (RTH-FR-004 / AC-1).
func TestAC01_SSEStreamsTopicsWithProducerIDs(t *testing.T) {
	requireOPA(t)
	in := newInstance(t, false, nil)
	defer in.close()
	seedActionCatalog(t, in.rc)

	tenant := uuid.NewString()
	user := "u-" + uuid.NewString()[:8]
	urn := fmt.Sprintf("wr:%s:pipeline:run/pr-1", tenant)
	seedResourceGrant(t, in.rc, tenant, user, urn, "viewer")

	runTopic := "run-status:" + urn
	notifTopic := "notifications:" + user
	cl := dialSSE(t, in.URL(), token(t, tenant, user, "user", 5*time.Minute, nil),
		[]string{runTopic, notifTopic}, "")
	defer cl.close()
	cl.waitControl(t, 5*time.Second, "subscribed")
	time.Sleep(200 * time.Millisecond)

	runID := uuid.NewString()
	postInternal(t, in, tenant, runTopic, runID, map[string]any{"event_type": "pipeline.run.status_changed", "payload": map[string]any{"status": "Running"}}, 600)
	notifID := uuid.NewString()
	postInternal(t, in, tenant, notifTopic, notifID, map[string]any{"event_type": "notification.created"}, 600)

	got := map[string]string{}
	for i := 0; i < 2; i++ {
		ev := cl.waitEvent(t, 5*time.Second, func(e sseEvent) bool { return e.Event == runTopic || e.Event == notifTopic })
		got[ev.Event] = ev.ID
	}
	if got[runTopic] != runID {
		t.Fatalf("run-status id=%q want %q (RTH-FR-004)", got[runTopic], runID)
	}
	if got[notifTopic] != notifID {
		t.Fatalf("notifications id=%q want %q", got[notifTopic], notifID)
	}
}

// TestAC02_KafkaFansOutAcrossInstancesThroughRedis is the mandated end-to-end:
// an event published to REAL Kafka is routed on ingest instance A, republished
// to REAL Redis pub/sub, and delivered to an SSE client on a DIFFERENT instance
// B (RTH-FR-020/041 / AC-2). Real component hit: Redpanda + Redis + OPA.
func TestAC02_KafkaFansOutAcrossInstancesThroughRedis(t *testing.T) {
	requireKafka(t)
	requireOPA(t)
	ingest := newInstance(t, true, nil) // A: Kafka consumer, always publishes
	defer ingest.close()
	edge := newInstance(t, false, nil) // B: serves the client
	defer edge.close()
	seedActionCatalog(t, edge.rc)

	tenant := uuid.NewString()
	user := "u-" + uuid.NewString()[:8]
	urn := fmt.Sprintf("wr:%s:pipeline:run/pr-%s", tenant, uuid.NewString()[:6])
	seedResourceGrant(t, edge.rc, tenant, user, urn, "viewer")

	topic := "run-status:" + urn
	cl := dialSSE(t, edge.URL(), token(t, tenant, user, "user", 5*time.Minute, nil), []string{topic}, "")
	defer cl.close()
	cl.waitControl(t, 5*time.Second, "subscribed")
	time.Sleep(500 * time.Millisecond) // ensure B's Redis SUBSCRIBE landed

	eventID := publishKafka(t, "pipeline.events.v1", "pipeline.run.status_changed", tenant, urn,
		map[string]any{"status": "Running", "step": "train"})

	ev := cl.waitEvent(t, 20*time.Second, func(e sseEvent) bool { return e.Event == topic })
	if ev.ID != eventID.String() {
		t.Fatalf("delivered id=%q want producer event_id %q", ev.ID, eventID)
	}
	t.Logf("Kafka(Redpanda) -> instance %s -> Redis pub/sub -> instance %s -> SSE client OK", ingest.podID, edge.podID)
}

// TestAC08_InternalPublishCrossPodInOrder: 100 batches published via the
// internal API to instance A arrive in order at a subscriber on instance B,
// proving the Redis pub/sub path (RTH-FR-021 / AC-8). Real component: Redis.
func TestAC08_InternalPublishCrossPodInOrder(t *testing.T) {
	producer := newInstance(t, false, nil)
	defer producer.close()
	edge := newInstance(t, false, nil)
	defer edge.close()

	tenant := uuid.NewString()
	user := "u-" + uuid.NewString()[:8]
	sess := "sess-" + uuid.NewString()[:8]
	seedSession(t, edge.rc, tenant, sess, user) // chat session ownership
	topic := "chat:" + sess

	cl := dialSSE(t, edge.URL(), token(t, tenant, user, "user", 5*time.Minute, nil), []string{topic}, "")
	defer cl.close()
	cl.waitControl(t, 5*time.Second, "subscribed")
	time.Sleep(500 * time.Millisecond)

	const n = 100
	ids := make([]string, n)
	start := time.Now()
	for i := 0; i < n; i++ {
		// uuidv7 keeps producer order stable end-to-end (RTH-FR-004).
		id, _ := uuid.NewV7()
		ids[i] = id.String()
		postInternal(t, producer, tenant, topic, ids[i], map[string]any{"tok": i}, 600)
	}

	for i := 0; i < n; i++ {
		ev := cl.waitEvent(t, 10*time.Second, func(e sseEvent) bool { return e.Event == topic })
		if ev.ID != ids[i] {
			t.Fatalf("batch %d out of order: got %q want %q", i, ev.ID, ids[i])
		}
	}
	t.Logf("100 internal-published batches crossed pods via Redis in order in %v", time.Since(start))
}

// TestAC05_PerTopicOPADeny: a subscribe to another resource's URN with no grant
// yields a per-topic TOPIC_FORBIDDEN control error while the granted topic keeps
// streaming; the connection is not torn down (RTH-FR-012 / AC-5). Real: OPA.
func TestAC05_PerTopicOPADeny(t *testing.T) {
	requireOPA(t)
	in := newInstance(t, false, nil)
	defer in.close()
	seedActionCatalog(t, in.rc)

	tenant := uuid.NewString()
	user := "u-" + uuid.NewString()[:8]
	allowedURN := fmt.Sprintf("wr:%s:pipeline:run/allowed", tenant)
	deniedURN := fmt.Sprintf("wr:%s:pipeline:run/denied", tenant)
	seedResourceGrant(t, in.rc, tenant, user, allowedURN, "viewer") // no grant on denied

	allowed := "run-status:" + allowedURN
	denied := "run-status:" + deniedURN
	cl := dialSSE(t, in.URL(), token(t, tenant, user, "user", 5*time.Minute, nil), []string{allowed, denied}, "")
	defer cl.close()

	// The denied topic must surface a per-topic TOPIC_FORBIDDEN error.
	errCtl := cl.waitEvent(t, 5*time.Second, func(e sseEvent) bool {
		if e.Event != "control" {
			return false
		}
		var m map[string]any
		return json.Unmarshal([]byte(e.Data), &m) == nil && m["code"] == "TOPIC_FORBIDDEN" && m["topic"] == denied
	})
	_ = errCtl

	// The allowed topic still delivers.
	time.Sleep(300 * time.Millisecond)
	id := uuid.NewString()
	postInternal(t, in, tenant, allowed, id, map[string]any{"status": "Running"}, 600)
	ev := cl.waitEvent(t, 5*time.Second, func(e sseEvent) bool { return e.Event == allowed })
	if ev.ID != id {
		t.Fatalf("allowed topic id=%q want %q", ev.ID, id)
	}
}

// TestAC03_ResumeAfterLastEventID: reconnect with Last-Event-ID replays the
// events after it, in order, with no duplicates, from the real Redis Streams
// buffer (RTH-FR-031 / AC-3). Real: Redis Streams + OPA.
func TestAC03_ResumeAfterLastEventID(t *testing.T) {
	requireOPA(t)
	in := newInstance(t, false, nil)
	defer in.close()
	seedActionCatalog(t, in.rc)

	tenant := uuid.NewString()
	user := "u-" + uuid.NewString()[:8]
	urn := fmt.Sprintf("wr:%s:pipeline:run/resume", tenant)
	seedResourceGrant(t, in.rc, tenant, user, urn, "viewer")
	topic := "run-status:" + urn

	// Pre-populate the replay buffer with 5 ordered events (no subscriber yet).
	ids := make([]string, 5)
	for i := 0; i < 5; i++ {
		id, _ := uuid.NewV7()
		ids[i] = id.String()
		postInternal(t, in, tenant, topic, ids[i], map[string]any{"seq": i}, 600)
	}
	time.Sleep(300 * time.Millisecond)

	// Reconnect from after ids[1] → expect ids[2], ids[3], ids[4] in order.
	cl := dialSSE(t, in.URL(), token(t, tenant, user, "user", 5*time.Minute, nil), []string{topic}, ids[1])
	defer cl.close()

	for i := 2; i < 5; i++ {
		ev := cl.waitEvent(t, 5*time.Second, func(e sseEvent) bool { return e.Event == topic })
		if ev.ID != ids[i] {
			t.Fatalf("resume order: pos %d got %q want %q", i, ev.ID, ids[i])
		}
	}
}

// TestAC04_ResetWhenAgedOut: reconnecting with an id older than the replay
// window yields a `reset` control and live-tail only (RTH-FR-031 / AC-4).
func TestAC04_ResetWhenAgedOut(t *testing.T) {
	requireOPA(t)
	in := newInstance(t, false, nil)
	defer in.close()
	seedActionCatalog(t, in.rc)

	tenant := uuid.NewString()
	user := "u-" + uuid.NewString()[:8]
	urn := fmt.Sprintf("wr:%s:pipeline:run/reset", tenant)
	seedResourceGrant(t, in.rc, tenant, user, urn, "viewer")
	topic := "run-status:" + urn

	// One recent event in the buffer.
	recent, _ := uuid.NewV7()
	postInternal(t, in, tenant, topic, recent.String(), map[string]any{"seq": 1}, 600)
	time.Sleep(200 * time.Millisecond)

	// Reconnect from an id far below the oldest retained (aged out) → reset.
	old := "00000000-0000-7000-8000-000000000000"
	cl := dialSSE(t, in.URL(), token(t, tenant, user, "user", 5*time.Minute, nil), []string{topic}, old)
	defer cl.close()
	m := cl.waitControl(t, 5*time.Second, "reset")
	if m["topic"] != topic {
		t.Fatalf("reset control topic=%v want %q", m["topic"], topic)
	}
}

// TestAC10_PerUserConnectionCap: the 11th concurrent connection for a user is
// refused 429, and X-Replace-Oldest evicts the oldest instead (RTH-FR-040 /
// AC-10). Real: Redis counters.
func TestAC10_PerUserConnectionCap(t *testing.T) {
	requireOPA(t)
	in := newInstance(t, false, nil)
	defer in.close()
	seedActionCatalog(t, in.rc)

	tenant := uuid.NewString()
	user := "u-" + uuid.NewString()[:8]
	urn := fmt.Sprintf("wr:%s:pipeline:run/cap", tenant)
	seedResourceGrant(t, in.rc, tenant, user, urn, "viewer")
	topic := "run-status:" + urn
	tkn := token(t, tenant, user, "user", 5*time.Minute, nil)

	var cancels []context.CancelFunc
	defer func() {
		for _, c := range cancels {
			c()
		}
	}()
	for i := 0; i < fanout.DefaultPerUser; i++ {
		st, cancel := connectStatus(t, in.URL(), tkn, []string{topic}, nil)
		if st != http.StatusOK {
			t.Fatalf("connection %d refused unexpectedly: %d", i, st)
		}
		cancels = append(cancels, cancel)
	}
	// 11th is refused.
	st, cancel := connectStatus(t, in.URL(), tkn, []string{topic}, nil)
	cancel()
	if st != http.StatusTooManyRequests {
		t.Fatalf("11th connection status=%d want 429", st)
	}
	// With X-Replace-Oldest it succeeds (oldest evicted).
	st2, cancel2 := connectStatus(t, in.URL(), tkn, []string{topic}, map[string]string{"X-Replace-Oldest": "true"})
	defer cancel2()
	if st2 != http.StatusOK {
		t.Fatalf("X-Replace-Oldest connection status=%d want 200", st2)
	}
}

// TestWebSocketSubscribeReceive exercises the secondary transport (RTH-FR-002):
// a WS client subscribes via a frame and receives an internally-published event.
func TestWebSocketSubscribeReceive(t *testing.T) {
	in := newInstance(t, false, nil)
	defer in.close()

	tenant := uuid.NewString()
	user := "u-" + uuid.NewString()[:8]
	sess := "sess-" + uuid.NewString()[:8]
	seedSession(t, in.rc, tenant, sess, user)
	topic := "chat:" + sess

	wsURL := "ws" + strings.TrimPrefix(in.URL(), "http") + "/api/v1/ws"
	hdr := http.Header{}
	hdr.Set("Authorization", "Bearer "+token(t, tenant, user, "user", 5*time.Minute, nil))
	conn, _, err := websocket.DefaultDialer.Dial(wsURL, hdr)
	if err != nil {
		t.Fatalf("ws dial: %v", err)
	}
	defer conn.Close()

	if err := conn.WriteJSON(map[string]any{"type": "subscribe", "topics": []string{topic}}); err != nil {
		t.Fatal(err)
	}
	// Wait for the subscribed ack.
	waitFrame(t, conn, 5*time.Second, func(m map[string]any) bool {
		return m["type"] == "subscribed" && m["topic"] == topic
	})
	time.Sleep(300 * time.Millisecond)

	id := uuid.NewString()
	postInternal(t, in, tenant, topic, id, map[string]any{"tok": "hi"}, 600)
	ev := waitFrame(t, conn, 5*time.Second, func(m map[string]any) bool {
		return m["type"] == "event" && m["topic"] == topic
	})
	if ev["id"] != id {
		t.Fatalf("ws event id=%v want %q", ev["id"], id)
	}
}

func waitFrame(t *testing.T, conn *websocket.Conn, d time.Duration, pred func(map[string]any) bool) map[string]any {
	t.Helper()
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		_ = conn.SetReadDeadline(deadline)
		var m map[string]any
		if err := conn.ReadJSON(&m); err != nil {
			t.Fatalf("ws read: %v", err)
		}
		if pred(m) {
			return m
		}
	}
	t.Fatal("timed out waiting for ws frame")
	return nil
}

// TestLeaderLease_SingleWriter: two leases contend on the same Redis key; at
// most one is leader at a time (RTH-FR-042). Real: Redis.
func TestLeaderLease_SingleWriter(t *testing.T) {
	rc := newRawRedis(t)
	defer rc.Close()
	res := "test-lease-" + uuid.NewString()[:8]
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	a := fanout.NewLease(rc.R, res, "A")
	b := fanout.NewLease(rc.R, res, "B")
	go a.Run(ctx)
	go b.Run(ctx)
	time.Sleep(1 * time.Second)
	if a.IsLeader() == b.IsLeader() {
		t.Fatalf("exactly one lease must be leader: A=%v B=%v", a.IsLeader(), b.IsLeader())
	}
}

// TestSecurity_InternalPublishRequiresProducerAuth proves the HIGH fix: an
// unauthenticated publish (and a user-token publish) is REJECTED 401, while a
// service producer token with realtime.publish succeeds. Without this a browser
// could forge events into any tenant/user (cross-tenant forgery).
func TestSecurity_InternalPublishRequiresProducerAuth(t *testing.T) {
	in := newInstance(t, false, nil)
	defer in.close()
	tenant := uuid.NewString()
	topic := "notifications:victim-user"

	// (a) no token → 401
	r1 := rawPublish(t, in.InternalURL(), "", tenant, topic, uuid.NewString(), map[string]any{"x": 1}, 600)
	r1.Body.Close()
	if r1.StatusCode != http.StatusUnauthorized {
		t.Fatalf("unauthenticated publish status=%d want 401", r1.StatusCode)
	}
	// (b) a user token (no producer rights) → 401
	userTok := token(t, tenant, "attacker", "user", 5*time.Minute, []string{fanout.CtrlError /* junk scope */})
	r2 := rawPublish(t, in.InternalURL(), userTok, tenant, topic, uuid.NewString(), map[string]any{"x": 2}, 600)
	r2.Body.Close()
	if r2.StatusCode != http.StatusUnauthorized {
		t.Fatalf("user-token publish status=%d want 401", r2.StatusCode)
	}
	// (c) a service producer token with realtime.publish → 200
	r3 := rawPublish(t, in.InternalURL(), serviceToken(t), tenant, topic, uuid.NewString(), map[string]any{"x": 3}, 600)
	r3.Body.Close()
	if r3.StatusCode != http.StatusOK {
		t.Fatalf("authenticated producer publish status=%d want 200", r3.StatusCode)
	}
	// The public port must NOT expose the internal publish route at all.
	pub := rawPublish(t, in.URL(), serviceToken(t), tenant, topic, uuid.NewString(), map[string]any{"x": 4}, 600)
	pub.Body.Close()
	if pub.StatusCode == http.StatusOK {
		t.Fatal("internal publish must not be reachable on the public listener")
	}
}

// TestAC06_RevocationViaRbacEvent proves the real rbac.events.v1 → Revoke wiring
// end-to-end: after the grant projection is removed and an rbac change event is
// published to REAL Kafka, the affected subscription is terminated with a
// `revoked` control within the SLA (RTH-FR-013 / AC-6). Real: Redpanda + OPA.
func TestAC06_RevocationViaRbacEvent(t *testing.T) {
	requireKafka(t)
	requireOPA(t)
	in := newInstance(t, true, nil) // consumes rbac.events.v1
	defer in.close()
	seedActionCatalog(t, in.rc)

	tenant := uuid.NewString()
	user := "u-" + uuid.NewString()[:8]
	urn := fmt.Sprintf("wr:%s:pipeline:run/rev-%s", tenant, uuid.NewString()[:6])
	seedResourceGrant(t, in.rc, tenant, user, urn, "viewer")
	topic := "run-status:" + urn

	cl := dialSSE(t, in.URL(), token(t, tenant, user, "user", 5*time.Minute, nil), []string{topic}, "")
	defer cl.close()
	cl.waitControl(t, 5*time.Second, "subscribed")

	// Simulate rbac revoking the grant: remove the projection, then emit the
	// change event. Re-evaluation now denies, so the sub is terminated.
	h := opaHash(urn)
	if err := in.rc.Del(context.Background(), fmt.Sprintf("perm:%s:%s:res:%s", tenant, user, h)); err != nil {
		t.Fatal(err)
	}
	publishKafka(t, "rbac.events.v1", "grant.revoked", tenant, urn, map[string]any{"subject": user})

	m := cl.waitControl(t, 20*time.Second, "revoked")
	if m["topic"] != topic {
		t.Fatalf("revoked control topic=%v want %q", m["topic"], topic)
	}
}

// TestAC06_AdditiveGrantDoesNotRevoke: an rbac event on a URN the subject is
// STILL authorized for must not terminate the subscription (re-evaluate, not
// blanket-terminate — fixes the over-aggressive LOW finding).
func TestAC06_AdditiveGrantDoesNotRevoke(t *testing.T) {
	requireKafka(t)
	requireOPA(t)
	in := newInstance(t, true, nil)
	defer in.close()
	seedActionCatalog(t, in.rc)

	tenant := uuid.NewString()
	user := "u-" + uuid.NewString()[:8]
	urn := fmt.Sprintf("wr:%s:pipeline:run/add-%s", tenant, uuid.NewString()[:6])
	seedResourceGrant(t, in.rc, tenant, user, urn, "viewer")
	topic := "run-status:" + urn

	cl := dialSSE(t, in.URL(), token(t, tenant, user, "user", 5*time.Minute, nil), []string{topic}, "")
	defer cl.close()
	cl.waitControl(t, 5*time.Second, "subscribed")

	// Grant remains in place; emit an rbac change (e.g. an additive grant add).
	publishKafka(t, "rbac.events.v1", "grant.created", tenant, urn, map[string]any{"subject": user})

	// The subscription must survive: a subsequent publish still reaches the client.
	time.Sleep(3 * time.Second)
	id := uuid.NewString()
	postInternal(t, in, tenant, topic, id, map[string]any{"status": "Running"}, 600)
	cl.waitEvent(t, 5*time.Second, func(e sseEvent) bool { return e.Event == topic && e.ID == id })
}

// TestAC09_HeartbeatEmitted asserts the periodic keepalive is actually sent
// (RTH-FR-033 / AC-9). The interval is shrunk for the test.
func TestAC09_HeartbeatEmitted(t *testing.T) {
	old := fanout.HeartbeatInterval
	fanout.HeartbeatInterval = 150 * time.Millisecond
	defer func() { fanout.HeartbeatInterval = old }()

	in := newInstance(t, false, nil)
	defer in.close()
	tenant := uuid.NewString()
	user := "u-" + uuid.NewString()[:8]
	// notifications:<me> needs no OPA (owner-only rule).
	cl := dialSSE(t, in.URL(), token(t, tenant, user, "user", 5*time.Minute, nil), []string{"notifications:" + user}, "")
	defer cl.close()

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if cl.hb.load() >= 2 {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatalf("expected ≥2 heartbeats, got %d", cl.hb.load())
}

// TestAC11_TokenRefreshAndExpiry: a connection near token expiry receives a
// token_refresh control, and if not refreshed closes 4401 within the grace
// window (RTH-FR-010 / AC-11). Windows are shrunk for the test.
func TestAC11_TokenRefreshAndExpiry(t *testing.T) {
	oldW, oldG := fanout.TokenWarnBefore, fanout.TokenGraceAfter
	fanout.TokenWarnBefore = 800 * time.Millisecond
	fanout.TokenGraceAfter = 1200 * time.Millisecond
	defer func() { fanout.TokenWarnBefore, fanout.TokenGraceAfter = oldW, oldG }()

	in := newInstance(t, false, nil)
	defer in.close()
	tenant := uuid.NewString()
	user := "u-" + uuid.NewString()[:8]
	// Token expires in ~2s → warn ~1.2s, hard-close ~3.2s.
	cl := dialSSE(t, in.URL(), token(t, tenant, user, "user", 2*time.Second, nil), []string{"notifications:" + user}, "")
	defer cl.close()

	cl.waitControl(t, 4*time.Second, "token_refresh")
	closeCtl := cl.waitControl(t, 5*time.Second, "close")
	if fmt.Sprint(closeCtl["code"]) != "4401" {
		t.Fatalf("expiry close code=%v want 4401", closeCtl["code"])
	}
}

// TestAC12_TicketSingleUse: a minted ticket authenticates exactly one connect
// and is rejected on reuse (RTH-FR-011 / AC-12). The raw JWT never appears in
// the URL — the ticket is an opaque uuid.
func TestAC12_TicketSingleUse(t *testing.T) {
	in := newInstance(t, false, nil)
	defer in.close()
	tenant := uuid.NewString()
	user := "u-" + uuid.NewString()[:8]
	topic := "notifications:" + user
	tkn := token(t, tenant, user, "user", 5*time.Minute, nil)

	ticket := mintTicket(t, in.URL(), tkn, []string{topic})
	if ticket == tkn || strings.Contains(ticket, ".") {
		t.Fatalf("ticket must be an opaque id, not the JWT: %q", ticket)
	}

	// First connect with the ticket succeeds and consumes it.
	st1, cancel1 := connectTicketStatus(t, in.URL(), ticket)
	if st1 != http.StatusOK {
		t.Fatalf("first ticket connect status=%d want 200", st1)
	}
	cancel1()
	// Reuse is rejected (GETDEL already removed it).
	st2, cancel2 := connectTicketStatus(t, in.URL(), ticket)
	cancel2()
	if st2 != http.StatusUnauthorized {
		t.Fatalf("ticket reuse status=%d want 401", st2)
	}
}

// mintTicket calls POST /api/v1/stream-tickets and returns the opaque ticket.
func mintTicket(t *testing.T, base, tkn string, topicList []string) string {
	t.Helper()
	body, _ := json.Marshal(map[string]any{"topics": topicList})
	req, _ := http.NewRequest("POST", base+"/api/v1/stream-tickets", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+tkn)
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("mint ticket status=%d", resp.StatusCode)
	}
	var out struct {
		Data struct {
			Ticket string `json:"ticket"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatal(err)
	}
	return out.Data.Ticket
}

// connectTicketStatus connects to SSE using only a ticket (no bearer header).
func connectTicketStatus(t *testing.T, base, ticket string) (int, context.CancelFunc) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	req, _ := http.NewRequestWithContext(ctx, "GET", base+"/api/v1/stream?ticket="+ticket, nil)
	req.Header.Set("Accept", "text/event-stream")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		cancel()
		t.Fatal(err)
	}
	if resp.StatusCode != http.StatusOK {
		resp.Body.Close()
		cancel()
		return resp.StatusCode, func() {}
	}
	go func() { <-ctx.Done(); resp.Body.Close() }()
	return resp.StatusCode, cancel
}
