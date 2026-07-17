//go:build integration

// Package integration is realtime-hub's Docker-backed tier. It runs against the
// REAL dev stack (deploy/docker-compose.dev.yml): Redis 7 (pub/sub + Streams
// replay + tickets + counters + leases), Redpanda (Kafka fan-in), and the OPA
// sidecar (per-topic authorization over the Redis permissions_flat projection).
// It auto-skips with a clear message when infra is unavailable.
//
// Bring the stack up first:
//
//	docker compose -f ../../deploy/docker-compose.dev.yml up -d redis redpanda opa postgres
package integration

import (
	"bufio"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"github.com/prometheus/client_golang/prometheus"

	"github.com/windrose-ai/go-common/authjwt"
	gcevent "github.com/windrose-ai/go-common/event"
	gckafka "github.com/windrose-ai/go-common/kafka"
	"github.com/windrose-ai/go-common/opaclient"
	"github.com/windrose-ai/go-common/redisx"

	"github.com/windrose-ai/realtime-hub/internal/api"
	"github.com/windrose-ai/realtime-hub/internal/authz"
	"github.com/windrose-ai/realtime-hub/internal/events"
	"github.com/windrose-ai/realtime-hub/internal/fanout"
	"github.com/windrose-ai/realtime-hub/internal/metrics"
	"github.com/windrose-ai/realtime-hub/internal/topics"
)

const (
	issuer   = "windrose-test"
	audience = "windrose"
)

var (
	redisAddr string
	opaURL    string
	brokers   []string
	kafkaUp   bool
	opaUp     bool
	signKey   *rsa.PrivateKey
)

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func TestMain(m *testing.M) {
	redisAddr = envOr("REDIS_ADDR", "localhost:6379")
	opaURL = envOr("OPA_URL", "http://localhost:8281")
	brokers = strings.Split(envOr("KAFKA_BROKERS", "localhost:9092"), ",")

	// Redis is mandatory for this tier.
	rc := redisx.New(redisAddr)
	if err := rc.Ping(context.Background()); err != nil {
		fmt.Printf("integration tests skipped: Redis unavailable at %s (%v)\n", redisAddr, err)
		os.Exit(0)
	}
	_ = rc.Close()

	opaUp = tcpReachable(strings.TrimPrefix(strings.TrimPrefix(opaURL, "http://"), "https://"))
	kafkaUp = tcpReachable(brokers[0])

	var err error
	signKey, err = rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		panic(err)
	}
	os.Exit(m.Run())
}

func newRawRedis(t *testing.T) *redisx.Client {
	t.Helper()
	return redisx.New(redisAddr)
}

func tcpReachable(hostport string) bool {
	c, err := net.DialTimeout("tcp", hostport, 2*time.Second)
	if err != nil {
		return false
	}
	_ = c.Close()
	return true
}

// instance is one simulated hub pod: its own podID, hub, Redis bus, and HTTP
// server, all sharing the real Redis so any pod serves any client.
type instance struct {
	podID       string
	hub         *fanout.Hub
	bus         *fanout.RedisBus
	rc          *redisx.Client
	srv         *api.Server
	httpSrv     *httptest.Server
	internalSrv *httptest.Server
	consumer    *events.Consumer
	metrics     *metrics.Metrics
	cancel      context.CancelFunc
}

func (in *instance) URL() string         { return in.httpSrv.URL }
func (in *instance) InternalURL() string { return in.internalSrv.URL }

func (in *instance) close() {
	in.httpSrv.CloseClientConnections()
	in.httpSrv.Close()
	in.internalSrv.Close()
	if in.cancel != nil {
		in.cancel()
	}
	if in.consumer != nil {
		in.consumer.Close()
	}
	_ = in.bus.Close()
	_ = in.rc.Close()
}

// newInstance builds a hub pod. When withLeader is nil the hub always publishes
// Kafka events (single ingest node); pass a *fanout.Lease to gate on leadership.
func newInstance(t *testing.T, withKafka bool, lease *fanout.Lease) *instance {
	t.Helper()
	podID := "pod-" + uuid.NewString()[:8]
	reg := prometheus.NewRegistry()
	m := metrics.New(reg)
	rc := redisx.New(redisAddr)
	rdb := rc.R

	replay := fanout.NewReplay(rdb)
	caps := fanout.NewCaps(rdb, 10, 2000)
	cfg := fanout.HubConfig{PodID: podID, Replay: replay, Caps: caps, Metrics: m}
	if lease != nil {
		cfg.KafkaLeader = lease
	}
	hub := fanout.NewHub(cfg)
	bus := fanout.NewRedisBus(rdb, slog.Default(), hub.OnBusMessage)
	hub.SetBus(bus)

	az := authz.NewOPAAuthorizer(opaURL, redisAddr)
	verifier := authjwt.NewStatic(&signKey.PublicKey, issuer, audience)

	// Revocation re-evaluation over the real OPA authorizer (RTH-FR-013).
	hub.SetReauthorizer(func(subject, typ string, scopes []string, tenant, rawTopic string) bool {
		tp, err := topics.Parse(rawTopic)
		if err != nil {
			return false
		}
		return az.Authorize(context.Background(), authz.Request{
			Subject: authz.Subject{ID: subject, Typ: typ, Scopes: scopes},
			Tenant:  tenant, Topic: tp,
		}).Allow
	})

	srv := &api.Server{
		Hub: hub, Authz: az, Verifier: verifier, Redis: rc,
		Caps: caps, Auditor: events.NoopAuditor{}, Metrics: m, Registry: reg,
	}
	ctx, cancel := context.WithCancel(context.Background())
	in := &instance{podID: podID, hub: hub, bus: bus, rc: rc, srv: srv,
		httpSrv:     httptest.NewServer(srv.Router()),
		internalSrv: httptest.NewServer(srv.InternalRouter()),
		metrics:     m, cancel: cancel}

	if withKafka {
		in.consumer = &events.Consumer{Router: topics.NewRouter(nil), Sink: hub, Log: slog.Default()}
		in.consumer.Start(ctx, brokers, podID)
	}
	return in
}

// token mints a signed RS256 JWT (real verifier path, static key).
func token(t *testing.T, tenant, sub, typ string, ttl time.Duration, scopes []string) string {
	t.Helper()
	claims := jwt.MapClaims{
		"sub": sub, "tenant_id": tenant, "typ": typ,
		"iss": issuer, "aud": audience,
		"exp": time.Now().Add(ttl).Unix(),
	}
	if len(scopes) > 0 {
		claims["scopes"] = scopes
	}
	s, err := jwt.NewWithClaims(jwt.SigningMethodRS256, claims).SignedString(signKey)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// serviceToken mints a service principal JWT with the realtime.publish scope,
// as agent-runtime/producers present to the internal publish API (RTH-FR-021).
func serviceToken(t *testing.T) string {
	t.Helper()
	// tenant_id is a required claim; a producer's own tenant is irrelevant to
	// the publish target (it names the tenant in the request body).
	return token(t, uuid.NewString(), "svc-producer", "service", 5*time.Minute, []string{api.ScopePublish})
}

// --- OPA projection seeding (rbac permissions_flat key scheme, RBC-FR-040) ---

// seedActionCatalog marks the realtime actions as known + tenant/resource-scoped
// (not workspace-scoped) so the OPA input projection resolves action_known.
// The catalog key is a shared global on the dev-stack Redis — merge additively,
// never replace, and never with a TTL (a TTL expires the whole platform
// catalog and 403s every guarded route in every service).
func seedActionCatalog(t *testing.T, rc *redisx.Client) {
	t.Helper()
	err := opaclient.SeedCatalogActions(context.Background(), rc, map[string]bool{
		authz.ActionRunStatusRead: false,
		authz.ActionProposalRead:  false,
	})
	if err != nil {
		t.Fatal(err)
	}
}

// opaHash is the resource-grant key suffix (matches rbac + opaclient).
func opaHash(urn string) string { return opaclient.URNHash(urn) }

// seedResourceGrant gives (tenant,user) a viewer grant on urn (read allowed).
func seedResourceGrant(t *testing.T, rc *redisx.Client, tenant, user, urn, level string) {
	t.Helper()
	h := opaclient.URNHash(urn)
	raw, _ := json.Marshal(map[string]any{"level": level})
	key := fmt.Sprintf("perm:%s:%s:res:%s", tenant, user, h)
	if err := rc.Set(context.Background(), key, raw, time.Hour); err != nil {
		t.Fatal(err)
	}
}

// seedSession registers chat-session ownership (agent-runtime projection).
func seedSession(t *testing.T, rc *redisx.Client, tenant, sessionID, owner string) {
	t.Helper()
	if err := rc.Set(context.Background(), fmt.Sprintf("rt:session:%s/%s", tenant, sessionID), owner, time.Hour); err != nil {
		t.Fatal(err)
	}
}

// --- Kafka producer helper ---

func publishKafka(t *testing.T, topic, eventType, tenant, urn string, payload map[string]any) uuid.UUID {
	t.Helper()
	prod := gckafka.NewProducer(gckafka.Config{Brokers: brokers})
	defer prod.Close()
	tid := uuid.MustParse(tenant)
	env := gcevent.New(eventType, tid, gcevent.Actor{Type: "service", ID: "producer"}, urn, "", payload)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := prod.Publish(ctx, topic, env); err != nil {
		t.Fatalf("kafka publish: %v", err)
	}
	return env.EventID
}

// --- SSE client ---

type sseEvent struct {
	ID    string
	Event string
	Data  string
}

type sseClient struct {
	events chan sseEvent
	cancel context.CancelFunc
	errc   chan error
	hb     atomicInt // heartbeat (": hb") comment count (RTH-FR-033 / AC-9)
}

// atomicInt is a tiny int64 counter safe for concurrent read/write.
type atomicInt struct{ v int64 }

func (a *atomicInt) inc()       { atomic.AddInt64(&a.v, 1) }
func (a *atomicInt) load() int64 { return atomic.LoadInt64(&a.v) }

// dialSSE opens an SSE connection and streams parsed events on a channel.
func dialSSE(t *testing.T, base, tkn string, topicList []string, lastEventID string) *sseClient {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	q := ""
	if len(topicList) > 0 {
		q = "?topics=" + strings.Join(topicList, ",")
	}
	req, err := http.NewRequestWithContext(ctx, "GET", base+"/api/v1/stream"+q, nil)
	if err != nil {
		cancel()
		t.Fatal(err)
	}
	req.Header.Set("Accept", "text/event-stream")
	if tkn != "" {
		req.Header.Set("Authorization", "Bearer "+tkn)
	}
	if lastEventID != "" {
		req.Header.Set("Last-Event-ID", lastEventID)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		cancel()
		t.Fatal(err)
	}
	if resp.StatusCode != http.StatusOK {
		body := make([]byte, 512)
		n, _ := resp.Body.Read(body)
		resp.Body.Close()
		cancel()
		t.Fatalf("SSE connect status %d: %s", resp.StatusCode, body[:n])
	}
	sc := &sseClient{events: make(chan sseEvent, 4096), cancel: cancel, errc: make(chan error, 1)}
	go func() {
		defer resp.Body.Close()
		defer close(sc.events)
		br := bufio.NewReader(resp.Body)
		var cur sseEvent
		for {
			line, err := br.ReadString('\n')
			if err != nil {
				sc.errc <- err
				return
			}
			line = strings.TrimRight(line, "\n")
			switch {
			case line == "":
				if cur.Event != "" || cur.Data != "" {
					select {
					case sc.events <- cur:
					case <-ctx.Done():
						return
					}
				}
				cur = sseEvent{}
			case strings.HasPrefix(line, ":"):
				sc.hb.inc() // heartbeat comment (": hb")
			case strings.HasPrefix(line, "id: "):
				cur.ID = strings.TrimPrefix(line, "id: ")
			case strings.HasPrefix(line, "event: "):
				cur.Event = strings.TrimPrefix(line, "event: ")
			case strings.HasPrefix(line, "data: "):
				cur.Data = strings.TrimPrefix(line, "data: ")
			}
		}
	}()
	return sc
}

func (c *sseClient) close() { c.cancel() }

// waitEvent waits for the next event matching pred, or fails after d.
func (c *sseClient) waitEvent(t *testing.T, d time.Duration, pred func(sseEvent) bool) sseEvent {
	t.Helper()
	deadline := time.After(d)
	for {
		select {
		case ev, ok := <-c.events:
			if !ok {
				t.Fatal("SSE stream closed before matching event")
			}
			if pred(ev) {
				return ev
			}
		case <-deadline:
			t.Fatal("timed out waiting for SSE event")
		}
	}
}

// collectControl returns the first control event whose type matches.
func (c *sseClient) waitControl(t *testing.T, d time.Duration, ctrlType string) map[string]any {
	ev := c.waitEvent(t, d, func(e sseEvent) bool {
		if e.Event != "control" {
			return false
		}
		var m map[string]any
		return json.Unmarshal([]byte(e.Data), &m) == nil && m["type"] == ctrlType
	})
	var m map[string]any
	_ = json.Unmarshal([]byte(ev.Data), &m)
	return m
}

// requireOPA / requireKafka skip a test when the dependency is down.
func requireOPA(t *testing.T) {
	if !opaUp {
		t.Skip("infra-blocked: OPA sidecar not reachable at " + opaURL)
	}
}
func requireKafka(t *testing.T) {
	if !kafkaUp {
		t.Skip("infra-blocked: Kafka/Redpanda not reachable at " + brokers[0])
	}
}
