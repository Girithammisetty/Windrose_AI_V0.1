// Command server runs realtime-hub: the platform's single push channel to
// browsers over SSE (primary) and WebSocket (secondary), fanning in from real
// Kafka and fanning out sticky-less across pods over real Redis pub/sub
// (BRD 20). Every adapter is real: no in-memory fan-out in the runtime path.
package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"

	"github.com/windrose-ai/go-common/authjwt"
	"github.com/windrose-ai/go-common/otelx"
	"github.com/windrose-ai/go-common/redisx"

	"github.com/windrose-ai/realtime-hub/internal/api"
	"github.com/windrose-ai/realtime-hub/internal/authz"
	"github.com/windrose-ai/realtime-hub/internal/events"
	"github.com/windrose-ai/realtime-hub/internal/fanout"
	"github.com/windrose-ai/realtime-hub/internal/metrics"
	"github.com/windrose-ai/realtime-hub/internal/register"
	"github.com/windrose-ai/realtime-hub/internal/store"
	"github.com/windrose-ai/realtime-hub/internal/topics"
)

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// allowedOrigins reads the CORS allowlist for the public router's browser-
// facing endpoints (/api/v1/stream — the browser opens EventSource directly
// at this service, cross-origin from ui-web). CORS_ALLOWED_ORIGINS is a
// comma-separated list; defaults to the local dev UI origin so `up.sh` works
// out of the box, but a real deployment must set this to its actual UI
// origin(s) or browser SSE will silently fail (server-side clients like curl
// are unaffected, which is why this was invisible to prior server-side tests).
func allowedOrigins() []string {
	raw := env("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
	parts := strings.Split(raw, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil))) // MASTER-FR-050
	log := slog.Default()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	podID := env("POD_ID", uuid.NewString())

	// OTel tracing (MASTER-FR-050), best-effort.
	if shutdown, err := otelx.Init(ctx, otelx.Config{ServiceName: "realtime-hub",
		Endpoint: os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT"), Insecure: true}); err == nil {
		defer func() { _ = shutdown(context.Background()) }()
	} else {
		log.Warn("otel init failed; continuing without tracing", "err", err)
	}

	// Redis (replay Streams, pub/sub, tickets, counters, leases) — required.
	redisAddr := env("REDIS_ADDR", "localhost:6379")
	rc := redisx.NewFromEnv(redisAddr, os.Getenv)
	rdb := rc.R
	if err := rc.Ping(ctx); err != nil {
		log.Warn("redis ping failed at startup; will retry lazily", "addr", redisAddr, "err", err)
	}

	// Postgres (ticket audit + routing_rules config) — optional in dev.
	var st *store.PG
	if dbURL := os.Getenv("DATABASE_URL"); dbURL != "" {
		// Migrations run under a privileged role (MIGRATE_DATABASE_URL, default =
		// DATABASE_URL); the runtime pool connects as DATABASE_URL, which in a
		// hardened deploy is a NON-superuser app role (realtime_app) so FORCE
		// row-level security is actually enforced on stream_tickets.
		migrateURL := dbURL
		if m := os.Getenv("MIGRATE_DATABASE_URL"); m != "" {
			migrateURL = m
		}
		if err := store.Migrate(migrateURL); err != nil {
			log.Error("migrations failed", "err", err)
			os.Exit(1)
		}
		poolCfg, err := pgxpool.ParseConfig(dbURL)
		if err != nil {
			log.Error("db connect failed", "err", err)
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
			log.Error("db connect failed", "err", err)
			os.Exit(1)
		}
		defer pool.Close()
		st = store.NewPG(pool)
		if err := st.SeedRoutingRules(ctx, routingSeed()); err != nil {
			log.Warn("routing rule seed failed", "err", err)
		}
	} else {
		log.Warn("DATABASE_URL unset; ticket audit + routing_rules config disabled (Redis-only dev)")
	}

	// Routing table (code-seeded; ops-disabled rules loaded from Postgres).
	var disabled map[string]bool
	if st != nil {
		if d, err := st.LoadDisabledRules(ctx); err == nil {
			disabled = d
		}
	}
	router := topics.NewRouter(disabled)

	// Metrics registry (MASTER-FR-051).
	reg := prometheus.NewRegistry()
	reg.MustRegister(prometheus.NewGoCollector())
	m := metrics.New(reg)

	// Leader lease for Kafka replay writes + republish (RTH-FR-042).
	lease := fanout.NewLease(rdb, "kafka-fanout", podID)
	go lease.Run(ctx)

	replay := fanout.NewReplay(rdb)
	caps := fanout.NewCaps(rdb, intEnv("MAX_CONNS_PER_USER", 0), intEnv("MAX_CONNS_PER_TENANT", 0))

	hub := fanout.NewHub(fanout.HubConfig{
		PodID: podID, Replay: replay, Caps: caps, KafkaLeader: lease,
		Metrics: m, MaxPerPod: intEnv("MAX_CONNS_PER_POD", 0),
	})
	bus := fanout.NewRedisBus(rdb, log, hub.OnBusMessage)
	defer func() { _ = bus.Close() }()
	hub.SetBus(bus)

	// Real authorizer: OPA sidecar over the Redis permissions_flat projection
	// (MASTER-FR-012). No allow-all escape hatch in the runtime path.
	az := authz.NewOPAAuthorizer(env("OPA_URL", "http://localhost:8281"), redisAddr)

	// JWT verifier against the identity-service JWKS (MASTER-FR-010).
	verifier := authjwt.NewJWKS(
		env("JWKS_URL", "http://identity-service/api/v1/.well-known/jwks.json"),
		os.Getenv("JWT_ISSUER"), os.Getenv("JWT_AUDIENCE"))

	// Revocation re-evaluation (RTH-FR-013): on an rbac change, re-check each
	// affected subscription and terminate only those now denied (additive grants
	// stay live). Uses the same real OPA authorizer as the connect path.
	hub.SetReauthorizer(func(subject, typ string, scopes []string, tenant, rawTopic string) bool {
		t, err := topics.Parse(rawTopic)
		if err != nil {
			return false
		}
		return az.Authorize(context.Background(), authz.Request{
			Subject: authz.Subject{ID: subject, Typ: typ, Scopes: scopes},
			Tenant:  tenant, Topic: t,
		}).Allow
	})

	// Kafka fan-in (RTH-FR-020) + audit emitter — real Redpanda by default.
	var auditor events.Auditor = events.NoopAuditor{}
	brokers := env("KAFKA_BROKERS", "localhost:9092")
	var consumer *events.Consumer
	if brokers != "false" {
		ka := events.NewKafkaAuditor(strings.Split(brokers, ","), log)
		defer func() { _ = ka.Close() }()
		auditor = ka
		consumer = &events.Consumer{Router: router, Sink: hub, Skips: skipMetric{m}, Log: log}
		consumer.Start(ctx, strings.Split(brokers, ","), podID)
		defer consumer.Close()
		log.Info("kafka fan-in started (broadcast mode)", "brokers", brokers, "pod", podID)
	} else {
		log.Warn("KAFKA_BROKERS=false; Kafka fan-in disabled (internal-publish path only)")
	}

	// Deploy-time action-catalog registration (RBC-FR-022): push realtime-hub's
	// action manifest to rbac so OPA's catalog knows each action (`action_known`).
	// Without it every OPA-decided subscribe (run-status/proposal) is denied as
	// unknown_action, so failure is LOUD: /readyz reports 503 until it succeeds
	// (M1 hardening). Dev mode (RBAC_URL / signing key unset) skips the call and
	// leaves readiness ungated.
	var regGate *api.RegGate
	if os.Getenv("RBAC_URL") == "" || os.Getenv("REGISTER_SIGNING_KEY_PEM") == "" {
		log.Warn("action registration skipped (RBAC_URL or REGISTER_SIGNING_KEY_PEM unset); dev mode, /readyz ungated")
	} else {
		regGate = api.NewRegGate()
		regCfg := register.Config{
			RBACURL:       os.Getenv("RBAC_URL"),
			SigningKeyPEM: os.Getenv("REGISTER_SIGNING_KEY_PEM"),
			SigningKID:    os.Getenv("REGISTER_SIGNING_KID"),
			Issuer:        os.Getenv("JWT_ISSUER"),
			Audience:      os.Getenv("JWT_AUDIENCE"),
			TenantID:      os.Getenv("REGISTER_TENANT_ID"),
		}
		go func(gate *api.RegGate) {
			for {
				err := register.Register(ctx, regCfg)
				if err == nil {
					gate.Succeed()
					return
				}
				log.Error("action catalog registration failed; /readyz degraded until it succeeds", "err", err)
				gate.Fail(err.Error())
				select {
				case <-ctx.Done():
					return
				case <-time.After(30 * time.Second):
				}
			}
		}(regGate)
	}

	srv := &api.Server{
		Hub: hub, Authz: az, Verifier: verifier, Redis: rc, Store: st,
		Caps: caps, Auditor: auditor, Metrics: m, Registry: reg, Log: log,
		RegGate: regGate, AllowedOrigins: allowedOrigins(),
	}

	// Ticket-audit purge job (§4).
	if st != nil {
		go purgeLoop(ctx, st, log)
	}

	// Internal producer API on a SEPARATE listener (RTH-FR-021) so the service
	// mesh can front it with mTLS; publishes are additionally authenticated at
	// the app layer (service/agent JWT + realtime.publish scope). It never
	// shares the public port with /api/v1/stream.
	internalAddr := env("INTERNAL_LISTEN_ADDR", ":8090")
	internalSrv := &http.Server{Addr: internalAddr, Handler: srv.InternalRouter(), ReadHeaderTimeout: 10 * time.Second}
	go func() {
		log.Info("realtime-hub internal publish listening", "addr", internalAddr)
		if err := internalSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Error("internal server failed", "err", err)
		}
	}()

	addr := env("LISTEN_ADDR", ":8080")
	httpSrv := &http.Server{Addr: addr, Handler: srv.Router(), ReadHeaderTimeout: 10 * time.Second}
	go func() {
		<-ctx.Done()
		// Graceful drain (RTH-FR-033 / AC-9): tell clients to reconnect, then
		// close after the grace window so they resume on another pod with zero
		// in-window loss.
		log.Info("draining connections")
		hub.Drain(0, 5*time.Second)
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(shutdownCtx)
		_ = internalSrv.Shutdown(shutdownCtx)
	}()
	log.Info("realtime-hub listening", "addr", addr, "pod", podID)
	if err := httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Error("server failed", "err", err)
		os.Exit(1)
	}
}

func intEnv(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

// routingSeed is the code routing table as an event_type→template map for the
// routing_rules config seed (RTH-FR-020).
func routingSeed() map[string]string {
	out := map[string]string{}
	for _, r := range topics.Rules {
		out[r.Name] = r.Template
	}
	return out
}

func purgeLoop(ctx context.Context, st *store.PG, log *slog.Logger) {
	t := time.NewTicker(time.Hour)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			if n, err := st.PurgeExpiredTickets(ctx); err != nil {
				log.Warn("ticket purge failed", "err", err)
			} else if n > 0 {
				log.Info("purged expired stream tickets", "rows", n)
			}
		}
	}
}

// skipMetric adapts the metrics dropped-event counter to the consumer's
// SkipCounter (unroutable/oversize events, RTH-FR-020).
type skipMetric struct{ m *metrics.Metrics }

func (s skipMetric) Skipped(reason string) { s.m.DroppedEvents.WithLabelValues("skip_" + reason).Inc() }
