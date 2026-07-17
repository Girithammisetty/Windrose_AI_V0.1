// Package integration runs notification-service against REAL infrastructure via
// Testcontainers (Postgres, Redis, Redpanda, Mailpit) plus a real HTTP webhook
// target and — where reachable — the compose OPA sidecar. It boots the same
// wiring cmd/server uses (real adapters), publishes real Kafka events, and
// asserts real notifications: in-app rows (Postgres), captured email (Mailpit),
// signed webhook POSTs (real HTTP), retries/circuit-breaker, rate-limit→digest,
// RLS cross-tenant via the shipped non-owner role, and OPA authz.
//
// Auto-skips with a clear message when Docker is unavailable.
package integration

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/testcontainers/testcontainers-go"
	tcpostgres "github.com/testcontainers/testcontainers-go/modules/postgres"
	tcredis "github.com/testcontainers/testcontainers-go/modules/redis"
	tcredpanda "github.com/testcontainers/testcontainers-go/modules/redpanda"
	"github.com/testcontainers/testcontainers-go/wait"

	"github.com/windrose-ai/go-common/authjwt"
	gcevent "github.com/windrose-ai/go-common/event"
	gckafka "github.com/windrose-ai/go-common/kafka"
	gcoutbox "github.com/windrose-ai/go-common/outbox"
	"github.com/windrose-ai/go-common/redisx"

	"github.com/windrose-ai/notification-service/internal/api"
	"github.com/windrose-ai/notification-service/internal/authz"
	"github.com/windrose-ai/notification-service/internal/channels/email"
	"github.com/windrose-ai/notification-service/internal/channels/inapp"
	"github.com/windrose-ai/notification-service/internal/channels/webhook"
	"github.com/windrose-ai/notification-service/internal/events"
	"github.com/windrose-ai/notification-service/internal/pipeline"
	"github.com/windrose-ai/notification-service/internal/ratelimit"
	"github.com/windrose-ai/notification-service/internal/registry"
	"github.com/windrose-ai/notification-service/internal/store"
	"github.com/windrose-ai/notification-service/internal/templates"
	"github.com/windrose-ai/notification-service/internal/worker"
)

type harness struct {
	pg         *store.PG
	appPool    *pgxpool.Pool
	rc         *redisx.Client
	producer   *gckafka.Producer
	pipeline   *pipeline.Pipeline
	worker     *worker.Worker
	server     *api.Server
	mailpitAPI string
	key        *rsa.PrivateKey
	brokers    []string
	redisAddr  string
}

var (
	h          *harness
	skipReason string
)

func TestMain(m *testing.M) {
	flag.Parse()
	if testing.Short() {
		skipReason = "-short mode (unit tier)"
		os.Exit(m.Run())
	}
	ctx := context.Background()

	pgc, err := tcpostgres.Run(ctx, "postgres:16-alpine",
		tcpostgres.WithDatabase("notification"),
		tcpostgres.WithUsername("windrose"),
		tcpostgres.WithPassword("windrose_dev"),
		tcpostgres.BasicWaitStrategies(),
	)
	if err != nil {
		skipReason = "Docker unavailable (" + err.Error() + ")"
		os.Exit(m.Run())
	}
	defer func() { _ = pgc.Terminate(ctx) }()
	ownerDSN, _ := pgc.ConnectionString(ctx, "sslmode=disable")

	// Migrations run under the owner DSN (creates notif_app + RLS).
	if err := store.Migrate(ownerDSN); err != nil {
		log.Fatalf("migrations: %v", err)
	}
	// Runtime pool connects as the shipped NON-owner role notif_app (RLS binds).
	appDSN := withUser(ownerDSN, "notif_app", "notif_app_pw")
	appPool, err := pgxpool.New(ctx, appDSN)
	if err != nil {
		log.Fatalf("app pool: %v", err)
	}
	pg := store.NewPG(appPool)

	rdc, err := tcredis.Run(ctx, "redis:7-alpine")
	if err != nil {
		skipReason = "redis container: " + err.Error()
		os.Exit(m.Run())
	}
	defer func() { _ = rdc.Terminate(ctx) }()
	redisAddr := mustHostPort(ctx, rdc.Container, "6379")
	rc := redisx.New(redisAddr)

	rpc, err := tcredpanda.Run(ctx, "docker.redpanda.com/redpandadata/redpanda:v24.2.4",
		tcredpanda.WithAutoCreateTopics())
	if err != nil {
		skipReason = "redpanda container: " + err.Error()
		os.Exit(m.Run())
	}
	defer func() { _ = rpc.Terminate(ctx) }()
	broker, err := rpc.KafkaSeedBroker(ctx)
	if err != nil {
		log.Fatalf("kafka broker: %v", err)
	}
	brokers := []string{broker}

	// Mailpit: real SMTP capture (SMTP 1025, HTTP API 8025).
	mpc, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: testcontainers.ContainerRequest{
			Image:        "axllent/mailpit:latest",
			ExposedPorts: []string{"1025/tcp", "8025/tcp"},
			WaitingFor:   wait.ForListeningPort("8025/tcp").WithStartupTimeout(60 * time.Second),
		},
		Started: true,
	})
	if err != nil {
		skipReason = "mailpit container: " + err.Error()
		os.Exit(m.Run())
	}
	defer func() { _ = mpc.Terminate(ctx) }()
	smtpAddr := mustHostPort(ctx, mpc, "1025")
	mailpitAPI := "http://" + mustHostPort(ctx, mpc, "8025")

	if err := pg.SeedPlatformTemplates(ctx, seedTemplates()); err != nil {
		log.Fatalf("seed templates: %v", err)
	}

	emailSender := email.NewSender(email.NewSMTP(smtpAddr, "", "", false))
	webhookSender := webhook.NewSender(true) // allow http for httptest targets
	reg := registry.Default()

	pl := &pipeline.Pipeline{
		Store: pg, Registry: reg, Groups: pipeline.NewRedisGroupResolver(rc),
		Dir: pipeline.NewRedisUserDirectory(rc), Email: emailSender, Webhook: webhookSender,
		Realtime: inapp.NewRedisPublisher(rc), Limiter: ratelimit.New(rc),
		DigestWindow: 2 * time.Second, InAppDailyCap: 500,
	}
	pl.SetQueuedForEndpoint(func(ctx context.Context, tenant, endpoint uuid.UUID) ([]pipeline.QueuedDelivery, error) {
		rows, err := pg.QueuedForEndpoint(ctx, tenant, endpoint)
		if err != nil {
			return nil, err
		}
		out := make([]pipeline.QueuedDelivery, len(rows))
		for i, r := range rows {
			out[i] = pipeline.QueuedDelivery{Delivery: r.Delivery, Envelope: r.Envelope}
		}
		return out, nil
	})

	producer := gckafka.NewProducer(gckafka.Config{Brokers: brokers})

	// Real consumer group over all platform topics → pipeline.
	consumer := gckafka.NewConsumerGroup(gckafka.ConsumerConfig{
		Brokers: brokers, GroupID: "notification-service", Topics: events.ConsumedTopics(),
		Handler: func(ctx context.Context, e gcevent.Envelope) error { return pl.Process(ctx, e) },
		Dedup:   rc, DLQ: producer,
	})
	runCtx, cancel := context.WithCancel(context.Background())
	go consumer.Run(runCtx)

	// Outbox relay + worker.
	relay := gcoutbox.New(events.OutboxSource{St: pg}, producer, events.Topic)
	go relay.Run(runCtx)
	wk := worker.New(pg, pl, emailSender)
	wk.Interval = 500 * time.Millisecond
	go wk.Run(runCtx)

	key, _ := rsa.GenerateKey(rand.Reader, 2048)
	srv := &api.Server{
		Store: pg, Authz: authz.AllowAll{}, Registry: reg, WebhookSender: webhookSender,
		Verifier: authjwt.NewStatic(&key.PublicKey, "windrose-test", "windrose"),
		// SES driver registered for its real status-callback parser (AC-10);
		// credentials are unset (Send is credential-gated) but ParseStatusCallback
		// needs none.
		EmailProviders: map[string]email.Provider{"ses": email.NewSES("us-east-1", "", "", "notifications@windrose.local")},
	}

	h = &harness{pg: pg, appPool: appPool, rc: rc, producer: producer, pipeline: pl, worker: wk,
		server: srv, mailpitAPI: mailpitAPI, key: key, brokers: brokers, redisAddr: redisAddr}

	code := m.Run()
	cancel()
	_ = consumer.Close()
	_ = producer.Close()
	appPool.Close()
	os.Exit(code)
}

func requireHarness(t *testing.T) *harness {
	t.Helper()
	if h == nil {
		t.Skipf("integration harness unavailable: %s", skipReason)
	}
	return h
}

func withUser(dsn, user, pass string) string {
	u, err := url.Parse(dsn)
	if err != nil {
		return dsn
	}
	u.User = url.UserPassword(user, pass)
	return u.String()
}

func mustHostPort(ctx context.Context, c testcontainers.Container, port string) string {
	host, err := c.Host(ctx)
	if err != nil {
		log.Fatalf("container host: %v", err)
	}
	mapped, err := c.MappedPort(ctx, port+"/tcp")
	if err != nil {
		log.Fatalf("mapped port %s: %v", port, err)
	}
	return fmt.Sprintf("%s:%s", host, mapped.Port())
}

// token mints an RS256 user JWT signed with the harness key.
func (h *harness) token(t *testing.T, tenant uuid.UUID, sub string) string {
	t.Helper()
	claims := jwt.MapClaims{
		"sub": sub, "tenant_id": tenant.String(), "typ": "user",
		"iss": "windrose-test", "aud": "windrose",
		"iat": time.Now().Unix(), "exp": time.Now().Add(5 * time.Minute).Unix(),
	}
	tok := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	s, err := tok.SignedString(h.key)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// publish sends an envelope to a topic on the real Kafka broker.
func (h *harness) publish(t *testing.T, topic string, env gcevent.Envelope) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := h.producer.Publish(ctx, topic, env); err != nil {
		t.Fatalf("kafka publish: %v", err)
	}
}

// mailpitMessages queries the real Mailpit HTTP API.
func (h *harness) mailpitMessages(t *testing.T) []mailMsg {
	t.Helper()
	resp, err := http.Get(h.mailpitAPI + "/api/v1/messages?limit=200")
	if err != nil {
		t.Fatalf("mailpit query: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	var out struct {
		Messages []mailMsg `json:"messages"`
	}
	_ = json.Unmarshal(body, &out)
	return out.Messages
}

type mailMsg struct {
	ID      string     `json:"ID"`
	Subject string     `json:"Subject"`
	To      []mailAddr `json:"To"`
}
type mailAddr struct {
	Address string `json:"Address"`
}

func (h *harness) mailpitDeleteAll(t *testing.T) {
	t.Helper()
	req, _ := http.NewRequest(http.MethodDelete, h.mailpitAPI+"/api/v1/messages", nil)
	resp, err := http.DefaultClient.Do(req)
	if err == nil {
		_ = resp.Body.Close()
	}
}

// seedUser populates the directory projection (notif:user:<tenant>:<user>) with
// a real email so the email channel resolves a genuine recipient. The runtime
// directory no longer fabricates addresses on a miss (it fails/skips instead),
// so every email-expecting test seeds the recipient's contact record first —
// mirroring identity-service populating the projection in production.
func (h *harness) seedUser(t *testing.T, tenant, userID, emailAddr string) {
	t.Helper()
	key := fmt.Sprintf("notif:user:%s:%s", tenant, userID)
	val, err := json.Marshal(map[string]string{"Email": emailAddr, "Locale": "en", "TZ": "UTC"})
	if err != nil {
		t.Fatal(err)
	}
	if err := h.rc.Set(context.Background(), key, string(val), time.Hour); err != nil {
		t.Fatalf("seed directory user: %v", err)
	}
}

// waitFor polls cond until true or timeout.
func waitFor(t *testing.T, d time.Duration, cond func() bool, msg string) {
	t.Helper()
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(150 * time.Millisecond)
	}
	t.Fatalf("timeout waiting for: %s", msg)
}

func seedTemplates() []store.SeedTemplate {
	var out []store.SeedTemplate
	for _, d := range templates.Defaults() {
		out = append(out,
			store.SeedTemplate{Key: d.Key, Channel: "email", Locale: "en", Subject: d.Subject, HTML: d.HTML, Text: d.Text},
			store.SeedTemplate{Key: d.Key, Channel: "in_app", Locale: "en", Subject: d.Subject, HTML: d.HTML, Text: d.Text},
		)
	}
	return out
}

func newEvent(eventType string, tenant uuid.UUID, payload map[string]any) gcevent.Envelope {
	return gcevent.New(eventType, tenant, gcevent.Actor{Type: "user", ID: "u-actor"}, "wr:t:case:case/1", "trace", payload)
}
