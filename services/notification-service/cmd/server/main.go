// Command server runs notification-service: the platform's single fan-out point
// from events to humans and external systems — in-app, email (provider
// abstraction), and signed webhooks — governed by subscription rules, digests,
// versioned templates and per-recipient rate limits (BRD 19).
//
// Every adapter is REAL by default (no env flag): Postgres (RLS, non-owner
// role), Redpanda (Kafka consumer of all *.events.v1 + outbox producer),
// Redis (dedup, projection, rate limits, realtime-hub backplane, directory),
// OPA sidecar + JWKS (authz/authn), SMTP (email, exercised against a local
// SMTP capture), and real HTTP webhook delivery. SES/SendGrid/ACS drivers are
// real but credential-gated; a durable Postgres-backed worker flushes digests
// and drives webhook retries (Temporal-equivalent when Temporal is unwired).
package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"go.temporal.io/sdk/activity"
	temporalclient "go.temporal.io/sdk/client"
	temporalworker "go.temporal.io/sdk/worker"

	"github.com/datacern-ai/go-common/authjwt"
	gcevent "github.com/datacern-ai/go-common/event"
	gckafka "github.com/datacern-ai/go-common/kafka"
	gcoutbox "github.com/datacern-ai/go-common/outbox"
	"github.com/datacern-ai/go-common/otelx"
	"github.com/datacern-ai/go-common/redisx"

	"github.com/datacern-ai/notification-service/internal/api"
	"github.com/datacern-ai/notification-service/internal/authz"
	"github.com/datacern-ai/notification-service/internal/channels/email"
	"github.com/datacern-ai/notification-service/internal/channels/inapp"
	"github.com/datacern-ai/notification-service/internal/channels/webhook"
	"github.com/datacern-ai/notification-service/internal/events"
	"github.com/datacern-ai/notification-service/internal/pipeline"
	"github.com/datacern-ai/notification-service/internal/ratelimit"
	"github.com/datacern-ai/notification-service/internal/register"
	"github.com/datacern-ai/notification-service/internal/registry"
	"github.com/datacern-ai/notification-service/internal/reports"
	"github.com/datacern-ai/notification-service/internal/store"
	"github.com/datacern-ai/notification-service/internal/templates"
	"github.com/datacern-ai/notification-service/internal/worker"

	"github.com/prometheus/client_golang/prometheus"
)

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func main() {
	slog.SetDefault(slog.New(otelx.WrapLogHandler(slog.NewJSONHandler(os.Stdout, nil)))) // MASTER-FR-050

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// Distributed tracing (no-op unless datacern_OTEL_ENABLED / an OTLP endpoint
	// is configured) — installs the global TracerProvider + W3C propagator.
	otelShutdown := otelx.InitFromEnv(ctx, "notification-service")
	defer func() { _ = otelShutdown(context.Background()) }()

	// Migrations run under the schema-owner DSN (creates the non-owner runtime
	// role + RLS). The runtime pool connects as that non-owner role so RLS binds.
	migrateURL := env("MIGRATE_DATABASE_URL", "postgres://datacern:datacern_dev@localhost:5432/notification?sslmode=disable")
	if env("RUN_MIGRATIONS", "true") == "true" {
		if err := store.Migrate(migrateURL); err != nil {
			slog.Error("migrations failed", "err", err)
			os.Exit(1)
		}
	}
	dbURL := env("DATABASE_URL", "postgres://notif_app:notif_app_pw@localhost:5432/notification?sslmode=disable")
	poolCfg, err := pgxpool.ParseConfig(dbURL)
	if err != nil {
		slog.Error("db connect failed", "err", err)
		os.Exit(1)
	}
	if v := os.Getenv("DB_MAX_CONNS"); v != "" {
		if n, e := strconv.Atoi(v); e == nil && n > 0 {
			poolCfg.MaxConns = int32(n)
		}
	} else {
		poolCfg.MaxConns = 20 // explicit default, up from pgx's ~4
	}
	pool, err := pgxpool.NewWithConfig(ctx, poolCfg)
	if err != nil {
		slog.Error("db connect failed", "err", err)
		os.Exit(1)
	}
	defer pool.Close()
	st := store.NewPG(pool)

	// Seed platform-default templates (idempotent).
	if err := st.SeedPlatformTemplates(ctx, seedTemplates()); err != nil {
		slog.Warn("template seed failed", "err", err)
	}

	// Shared Redis client (dedup, projection, rate limits, realtime, directory).
	rc := redisx.NewFromEnv(env("REDIS_ADDR", "localhost:6379"), os.Getenv)

	// Real email provider chain: SMTP (default, local capture) + credential-gated
	// cloud drivers when configured (SES/SendGrid/ACS).
	emailSender := buildEmailSender()

	// Real webhook sender (https-only unless WEBHOOK_ALLOW_HTTP=true for dev/e2e).
	webhookSender := webhook.NewSender(env("WEBHOOK_ALLOW_HTTP", "false") == "true")

	// Kafka producer for the outbox relay + consumer DLQ.
	brokers := strings.Split(env("KAFKA_BROKERS", "localhost:9092"), ",")
	producer := gckafka.NewProducer(gckafka.Config{
		Brokers: brokers,
		SASL:    gckafka.SASLFromEnv(os.Getenv), TLS: gckafka.TLSFromEnv(os.Getenv),
	})
	defer func() { _ = producer.Close() }()

	reg := registry.Default()
	limiter := ratelimit.New(rc)

	pl := &pipeline.Pipeline{
		Store:    st,
		Registry: reg,
		Groups:   pipeline.NewRedisGroupResolver(rc),
		Dir:      pipeline.NewRedisUserDirectory(rc),
		Email:    emailSender,
		Webhook:  webhookSender,
		Realtime: inapp.NewRedisPublisher(rc),
		Limiter:  limiter,
		Metrics:  pipeline.NewMetrics(prometheus.DefaultRegisterer),
		Log:      slog.Default(),
	}
	pl.SetQueuedForEndpoint(func(ctx context.Context, tenant, endpoint uuid.UUID) ([]pipeline.QueuedDelivery, error) {
		rows, err := st.QueuedForEndpoint(ctx, tenant, endpoint)
		if err != nil {
			return nil, err
		}
		out := make([]pipeline.QueuedDelivery, len(rows))
		for i, r := range rows {
			out[i] = pipeline.QueuedDelivery{Delivery: r.Delivery, Envelope: r.Envelope}
		}
		return out, nil
	})

	// Real OPA + JWKS.
	az := authz.NewOPAClient(env("OPA_URL", "http://localhost:8281"), env("REDIS_ADDR", "localhost:6379"))
	verifier := authjwt.NewJWKS(
		env("JWKS_URL", "http://identity-service/api/v1/.well-known/jwks.json"),
		os.Getenv("JWT_ISSUER"), os.Getenv("JWT_AUDIENCE"))

	// registrationReady flips true once the action manifest is registered with
	// rbac (or registration is deliberately skipped in dev). /readyz gates on it
	// so a service whose actions never registered — which 403s every guarded
	// route incl. the inbox — is reported not-ready rather than silently broken.
	var registrationReady atomic.Bool

	// Real Temporal Schedules for scheduled dashboard report subscriptions
	// (NOTIF-FR-060) — the platform's durable-scheduling primitive, genuinely
	// wired to the live cluster at TEMPORAL_HOSTPORT (:7233 in dev/e2e), not an
	// in-process ticker. A connect failure degrades HONESTLY: report CRUD still
	// persists rows, but create/update returns a real error instead of silently
	// never scheduling anything.
	reportScheduler := setupReportScheduling(ctx, st, emailSender)

	srv := &api.Server{
		Store:          st,
		Authz:          az,
		Verifier:       verifier,
		Registry:       reg,
		WebhookSender:  webhookSender,
		EmailProviders: emailProviders(emailSender),
		Reports:        reportScheduler,
		Ready:          registrationReady.Load,
	}

	// Deploy-time action-catalog registration with rbac (RBC-FR-022). Retried
	// until it succeeds (or ctx is cancelled): guarded == registered, so we must
	// not serve as "ready" until the manifest is in rbac's catalog.
	go func() {
		cfg := register.Config{
			RBACURL:       os.Getenv("RBAC_URL"),
			SigningKeyPEM: os.Getenv("REGISTER_SIGNING_KEY_PEM"),
			SigningKID:    os.Getenv("REGISTER_SIGNING_KID"),
			Issuer:        os.Getenv("JWT_ISSUER"),
			Audience:      os.Getenv("JWT_AUDIENCE"),
			TenantID:      os.Getenv("REGISTER_TENANT_ID"),
		}
		for {
			if err := register.Register(ctx, cfg); err != nil {
				slog.Error("action catalog registration failed; retrying (service not ready)", "err", err)
				select {
				case <-ctx.Done():
					return
				case <-time.After(3 * time.Second):
					continue
				}
			}
			registrationReady.Store(true)
			slog.Info("action catalog registration complete; service ready")
			return
		}
	}()

	// Kafka consumer group: all platform topics → pipeline (NOTIF-FR-001).
	kafkaSASL, kafkaTLS := gckafka.SASLFromEnv(os.Getenv), gckafka.TLSFromEnv(os.Getenv)
	dlq := gckafka.NewProducer(gckafka.Config{Brokers: brokers, SASL: kafkaSASL, TLS: kafkaTLS})
	defer func() { _ = dlq.Close() }()
	consumer := gckafka.NewConsumerGroup(gckafka.ConsumerConfig{
		Brokers: brokers, GroupID: "notification-service", Topics: events.ConsumedTopics(),
		Handler: func(ctx context.Context, e gcevent.Envelope) error { return pl.Process(ctx, e) },
		Dedup:   rc, DLQ: dlq,
		SASL: kafkaSASL, TLS: kafkaTLS,
	})
	go consumer.Run(ctx)
	defer func() { _ = consumer.Close() }()

	// Outbox relay → notification.events.v1 (MASTER-FR-034).
	relay := gcoutbox.New(events.OutboxSource{St: st}, producer, events.Topic)
	go relay.Run(ctx)
	// B6 (BRD 58): published outbox rows are drained but never pruned; sweep
	// them past a retention window so the table doesn't grow unboundedly.
	go gcoutbox.NewPruner(pool, "outbox", "app.role", "platform").Run(ctx)

	// Durable digest-flush + webhook-retry worker.
	wk := worker.New(st, pl, emailSender)
	go wk.Run(ctx)

	addr := env("LISTEN_ADDR", ":8087")
	httpSrv := &http.Server{Addr: addr, Handler: otelx.WrapHandler(srv.Router(), "notification-service"), ReadHeaderTimeout: 10 * time.Second}
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(shutdownCtx)
	}()
	slog.Info("notification-service listening", "addr", addr,
		"email_providers", emailSender.Providers(), "consumed_topics", len(events.ConsumedTopics()))
	if err := httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		slog.Error("server failed", "err", err)
		os.Exit(1)
	}
}

// buildEmailSender assembles the provider chain: SMTP (real, default) first,
// then credential-gated cloud providers when configured.
func buildEmailSender() *email.Sender {
	var providers []email.Provider
	smtpAddr := env("SMTP_ADDR", "localhost:1025") // mailpit default
	providers = append(providers, email.NewSMTP(smtpAddr, os.Getenv("SMTP_USER"), os.Getenv("SMTP_PASS"), os.Getenv("SMTP_TLS") == "true"))
	if k := os.Getenv("SENDGRID_API_KEY"); k != "" {
		providers = append(providers, email.NewSendGrid(k, env("EMAIL_FROM", "notifications@datacern.local")))
	}
	if os.Getenv("AWS_ACCESS_KEY_ID") != "" {
		providers = append(providers, email.NewSES(env("AWS_REGION", "us-east-1"), os.Getenv("AWS_ACCESS_KEY_ID"), os.Getenv("AWS_SECRET_ACCESS_KEY"), env("EMAIL_FROM", "notifications@datacern.local")))
	}
	if os.Getenv("ACS_ENDPOINT") != "" {
		providers = append(providers, email.NewACS(os.Getenv("ACS_ENDPOINT"), os.Getenv("ACS_ACCESS_KEY"), env("EMAIL_FROM", "notifications@datacern.local")))
	}
	return email.NewSender(providers...)
}

// emailProviders exposes each configured provider by name for status callbacks.
func emailProviders(s *email.Sender) map[string]email.Provider {
	m := map[string]email.Provider{}
	// SMTP has no callback; register cloud providers if creds present.
	if k := os.Getenv("SENDGRID_API_KEY"); k != "" {
		m["sendgrid"] = email.NewSendGrid(k, env("EMAIL_FROM", "notifications@datacern.local"))
	}
	if os.Getenv("AWS_ACCESS_KEY_ID") != "" {
		m["ses"] = email.NewSES(env("AWS_REGION", "us-east-1"), os.Getenv("AWS_ACCESS_KEY_ID"), os.Getenv("AWS_SECRET_ACCESS_KEY"), env("EMAIL_FROM", "notifications@datacern.local"))
	}
	if os.Getenv("ACS_ENDPOINT") != "" {
		m["acs"] = email.NewACS(os.Getenv("ACS_ENDPOINT"), os.Getenv("ACS_ACCESS_KEY"), env("EMAIL_FROM", "notifications@datacern.local"))
	}
	return m
}

// setupReportScheduling connects to the real Temporal cluster, registers the
// ReportWorkflow + SendReportEmail activity on a dedicated task queue, and
// starts the worker. It returns nil (not a fatal error) if Temporal is
// unreachable, so the rest of notification-service still boots — report
// subscriptions are simply not schedulable until Temporal is back, which
// api.Server surfaces honestly on create/update rather than pretending to
// schedule.
func setupReportScheduling(ctx context.Context, st *store.PG, emailSender *email.Sender) *reports.Scheduler {
	hostPort := env("TEMPORAL_HOSTPORT", "localhost:7233")
	namespace := env("TEMPORAL_NAMESPACE", "default")
	tc, err := temporalclient.Dial(temporalclient.Options{HostPort: hostPort, Namespace: namespace})
	if err != nil {
		slog.Error("temporal connect failed; report subscriptions will not be schedulable", "err", err, "host_port", hostPort)
		return nil
	}

	chartClient := reports.NewChartClient(env("CHART_URL", "http://localhost:8320"))
	tokens := reports.NewTokenMinter(
		os.Getenv("REGISTER_SIGNING_KEY_PEM"), os.Getenv("REGISTER_SIGNING_KID"),
		os.Getenv("JWT_ISSUER"), os.Getenv("JWT_AUDIENCE"))
	activities := &reports.Activities{Store: st, Charts: chartClient, Tokens: tokens, Email: emailSender, Log: slog.Default()}

	w := temporalworker.New(tc, reports.TaskQueue, temporalworker.Options{})
	w.RegisterWorkflow(reports.ReportWorkflow)
	w.RegisterActivityWithOptions(activities.SendReportEmail, activity.RegisterOptions{Name: reports.ActivitySendReportEmail})
	go func() {
		if err := w.Run(temporalworker.InterruptCh()); err != nil {
			slog.Error("temporal report worker stopped", "err", err)
		}
	}()
	go func() {
		<-ctx.Done()
		tc.Close()
	}()

	slog.Info("temporal report scheduling wired", "host_port", hostPort, "namespace", namespace, "task_queue", reports.TaskQueue)
	return &reports.Scheduler{Client: tc}
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
