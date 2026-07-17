package api

import (
	"context"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/windrose-ai/audit-service/internal/authz"
	"github.com/windrose-ai/audit-service/internal/chstore"
	"github.com/windrose-ai/audit-service/internal/compliance"
	"github.com/windrose-ai/audit-service/internal/meta"
	"github.com/windrose-ai/audit-service/internal/pgstore"
	"github.com/windrose-ai/audit-service/internal/worm"
	"github.com/windrose-ai/go-common/metricsx"
)

// chiRoutePattern resolves the matched chi route template for a bounded metrics
// route label (evaluated after routing), falling back to "other".
func chiRoutePattern(r *http.Request) string {
	if rc := chi.RouteContext(r.Context()); rc != nil {
		if p := rc.RoutePattern(); p != "" {
			return p
		}
	}
	return "other"
}

// Redriver re-processes a DLQ topic (satisfied by *ingest.Consumer).
type Redriver interface {
	Redrive(ctx context.Context, dlqTopic string, max int) (int, error)
}

// Server holds the admin API dependencies (all real adapters in cmd/server).
type Server struct {
	CH         *chstore.Store
	PG         *pgstore.Store
	WORM       *worm.Client
	Compliance *compliance.Builder
	Redriver   Redriver
	Meta       *meta.Emitter
	Authz      authz.Authorizer
	Verifier   *Verifier

	// IngestGroup is the ingest consumer group id used to derive DLQ topic names.
	IngestGroup string
	// PresignTTL is the signed-URL lifetime for exports/packs.
	PresignTTL time.Duration
}

// Router builds the chi router with the full middleware chain.
func (s *Server) Router() http.Handler {
	r := chi.NewRouter()
	// RED metrics (MASTER-FR-050): real /metrics + per-request rate/errors/duration.
	metrics := metricsx.New("audit-service")
	r.Use(TraceMiddleware, RecoverMiddleware, metrics.Middleware(chiRoutePattern))

	r.Get("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	r.Get("/readyz", s.handleReady)
	r.Handle("/metrics", metrics.Handler())

	r.Route("/api/v1", func(r chi.Router) {
		r.Use(s.AuthMiddleware)

		r.With(s.RequireAction(authz.ActionEventRead)).Get("/audit/search", s.handleSearch)
		r.With(s.RequireAction(authz.ActionEventExport)).Get("/audit/export", s.handleExport)
		r.With(s.RequireAction(authz.ActionEventRead)).Get("/audit/agent-activity", s.handleAgentActivity)
		r.With(s.RequireAction(authz.ActionEventRead)).Get("/audit/events/{event_id}", s.handleGetEvent)
		r.With(s.RequireAction(authz.ActionChainVerify)).Post("/audit/verify", s.handleVerify)
		r.With(s.RequireAction(authz.ActionExportRead)).Get("/exports", s.handleListExports)
		r.With(s.RequireAction(authz.ActionComplianceRead)).Post("/compliance/soc2-pack", s.handleSOC2Pack)
		r.With(s.RequireAction(authz.ActionComplianceRead)).Post("/compliance/ai-decision-log", s.handleAIDecisionLog)
		r.With(s.RequireAction(authz.ActionComplianceRead)).Get("/operations/{id}", s.handleGetOperation)
		r.With(s.RequireAction(authz.ActionDLQRedrive)).Post("/admin/dlq/redrive", s.handleRedrive)
	})
	return r
}

func (s *Server) handleReady(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	if err := s.CH.Ping(ctx); err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"status": "clickhouse_down"})
		return
	}
	if err := s.PG.Ping(ctx); err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"status": "postgres_down"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok"})
}
