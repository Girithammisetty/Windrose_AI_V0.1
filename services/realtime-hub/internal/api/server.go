package api

import (
	"context"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/windrose-ai/go-common/authjwt"
	"github.com/windrose-ai/go-common/metricsx"
	"github.com/windrose-ai/go-common/redisx"

	"github.com/windrose-ai/realtime-hub/internal/authz"
	"github.com/windrose-ai/realtime-hub/internal/events"
	"github.com/windrose-ai/realtime-hub/internal/fanout"
	"github.com/windrose-ai/realtime-hub/internal/metrics"
	"github.com/windrose-ai/realtime-hub/internal/store"
)

// Server is the HTTP/SSE/WS surface.
type Server struct {
	Hub      *fanout.Hub
	Authz    authz.Authorizer
	Verifier *authjwt.Verifier
	Redis    *redisx.Client
	Store    *store.PG // may be nil (Redis-only dev)
	Caps     *fanout.Caps
	Auditor  events.Auditor
	Metrics  *metrics.Metrics
	// Metricsx carries the shared HTTP RED middleware + /metrics registry (the
	// hub's domain collectors are registered onto the same registry in main).
	// May be nil (unit tests) — Router() then builds a local one so /metrics and
	// per-request RED metrics still work.
	Metricsx *metricsx.Registry
	Log      *slog.Logger

	// RegGate gates /readyz on action-catalog registration (RBC-FR-022 /
	// M1 hardening). Nil = registration intentionally skipped (dev mode).
	RegGate *RegGate

	// MaxTopicsPerConn is the per-connection topic cap (RTH-FR-001, default 20).
	MaxTopicsPerConn int

	// AllowedOrigins is the CORS allowlist for the public router (RTH-FR-001):
	// the browser opens EventSource DIRECTLY at this service (cross-origin from
	// ui-web), and EventSource cannot set custom headers, so ticket auth rides
	// in the query string — but the browser still enforces CORS on the response
	// before it will deliver stream data to page JS. Without an explicit
	// Access-Control-Allow-Origin match, every browser SSE connection here
	// fails silently (visible only as a failed network request in devtools,
	// never as a server-side error — curl/non-browser clients are unaffected
	// since they don't enforce CORS at all). Empty = no origin is allowed.
	AllowedOrigins []string
}

// Router builds the chi router with all routes (BRD 20 §5).
func (s *Server) Router() http.Handler {
	if s.Log == nil {
		s.Log = slog.Default()
	}
	if s.MaxTopicsPerConn <= 0 {
		s.MaxTopicsPerConn = 20
	}
	// RED metrics (MASTER-FR-050): real /metrics + per-request rate/errors/
	// duration via the shared middleware. In production Metricsx is injected (and
	// carries the hub's domain collectors too); in unit tests it is nil, so build
	// a local registry rather than degrade to a runtime-only stub.
	metricsReg := s.Metricsx
	if metricsReg == nil {
		metricsReg = metricsx.New("realtime-hub")
	}

	r := chi.NewRouter()
	r.Use(traceMiddleware)
	r.Use(s.corsMiddleware)
	r.Use(metricsReg.Middleware(chiRoutePattern))

	// Health & metrics (MASTER-FR-051).
	r.Get("/healthz", s.handleHealthz)
	r.Get("/readyz", s.handleReadyz)
	r.Handle("/metrics", metricsReg.Handler())

	r.Route("/api/v1", func(r chi.Router) {
		// Connect endpoints authenticate inline (ticket or bearer) because SSE
		// EventSource cannot set headers (RTH-FR-001/011).
		r.Get("/stream", s.handleSSE)
		r.Get("/ws", s.handleWS)

		// JWT-authed REST side channels.
		r.Group(func(r chi.Router) {
			r.Use(s.bearerAuth)
			r.Post("/stream-tickets", s.handleMintTicket)
			r.Post("/stream/{conn_id}/topics", s.handleTopics)
			r.Post("/stream/{conn_id}/token", s.handleRefreshToken)
		})
	})

	// Admin (RTH-FR-044).
	r.Group(func(r chi.Router) {
		r.Use(s.bearerAuth)
		r.Get("/admin/connections", s.handleAdminList)
		r.Delete("/admin/connections/{conn_id}", s.handleAdminKill)
	})

	return r
}

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

// InternalRouter is the producer-facing surface (RTH-FR-021), served on a
// SEPARATE listener from the public Router so the service mesh can apply mTLS at
// the network layer. In addition, every publish is authenticated at the app
// layer: the caller must present a trusted service/agent JWT carrying the
// realtime.publish scope (see handleInternalPublish). This closes the
// cross-tenant event-forgery hole — an unauthenticated publish is rejected 401.
func (s *Server) InternalRouter() http.Handler {
	if s.Log == nil {
		s.Log = slog.Default()
	}
	r := chi.NewRouter()
	r.Use(traceMiddleware)
	r.Get("/healthz", s.handleHealthz)
	r.Post("/internal/v1/publish", s.handleInternalPublish)
	return r
}

// corsMiddleware allows the configured UI origin(s) to read responses from
// the public router — required for the browser's direct cross-origin
// EventSource to /api/v1/stream (see AllowedOrigins doc). Exact-match only
// (no wildcard subdomain support); an Origin not in the allowlist gets no
// CORS headers at all, so the browser rejects the response as today, but a
// legitimate mismatch is now debuggable (no silent server-side success that
// masks a client-side failure).
func (s *Server) corsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		origin := r.Header.Get("Origin")
		if origin != "" {
			for _, allowed := range s.AllowedOrigins {
				if origin == allowed {
					w.Header().Set("Access-Control-Allow-Origin", origin)
					w.Header().Set("Vary", "Origin")
					w.Header().Set("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
					w.Header().Set("Access-Control-Allow-Headers", "Authorization, Content-Type")
					break
				}
			}
		}
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// connIdentity is the authenticated caller + requested topics for a connect.
type connIdentity struct {
	Subject string
	Tenant  string
	Typ     string
	Scopes  []string
	Topics  []string
	Exp     time.Time
	IPHash  string
}

// authenticateConnect resolves a connect request via a one-time stream ticket
// (RTH-FR-011) or an Authorization bearer header (RTH-FR-001/010).
func (s *Server) authenticateConnect(r *http.Request) (*connIdentity, error) {
	ipHash := hashIP(clientIP(r))
	if tk := r.URL.Query().Get("ticket"); tk != "" {
		return s.consumeTicket(r.Context(), tk, ipHash)
	}
	raw := r.Header.Get("Authorization")
	if !strings.HasPrefix(raw, "Bearer ") {
		return nil, errUnauthenticated
	}
	claims, err := s.Verifier.Verify(r.Context(), strings.TrimPrefix(raw, "Bearer "))
	if err != nil {
		return nil, errUnauthenticated
	}
	id := &connIdentity{
		Subject: claims.EffectiveUser(),
		Tenant:  claims.TenantID,
		Typ:     claims.Typ,
		Scopes:  claims.Scopes,
		Topics:  splitTopics(r.URL.Query().Get("topics")),
		IPHash:  ipHash,
	}
	if claims.ExpiresAt != nil {
		id.Exp = claims.ExpiresAt.Time
	}
	return id, nil
}

func splitTopics(s string) []string {
	if s == "" {
		return nil
	}
	parts := strings.Split(s, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

// bearerAuth verifies the JWT and stashes claims for REST side channels.
func (s *Server) bearerAuth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw := r.Header.Get("Authorization")
		if !strings.HasPrefix(raw, "Bearer ") {
			writeErr(w, r, http.StatusUnauthorized, "UNAUTHENTICATED", "missing bearer token", 0)
			return
		}
		claims, err := s.Verifier.Verify(r.Context(), strings.TrimPrefix(raw, "Bearer "))
		if err != nil {
			writeErr(w, r, http.StatusUnauthorized, "UNAUTHENTICATED", "invalid token", 0)
			return
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ctxKeyClaims, claims)))
	})
}

type claimsKey int

const ctxKeyClaims claimsKey = iota

func claimsFrom(ctx context.Context) *authjwt.Claims {
	if v, ok := ctx.Value(ctxKeyClaims).(*authjwt.Claims); ok {
		return v
	}
	return nil
}
