package api

import (
	"context"
	"log/slog"
	"net/http"
	"strings"

	"github.com/google/uuid"

	"github.com/windrose-ai/usage-service/internal/authz"
	"github.com/windrose-ai/usage-service/internal/domain"
	"github.com/windrose-ai/usage-service/internal/events"
)

type ctxKey int

const (
	ctxKeyTraceID ctxKey = iota
	ctxKeyClaims
)

// TraceID returns the request trace id (MASTER-FR-028).
func TraceID(ctx context.Context) string {
	if v, ok := ctx.Value(ctxKeyTraceID).(string); ok {
		return v
	}
	return ""
}

// ClaimsFrom returns the verified JWT claims.
func ClaimsFrom(ctx context.Context) *Claims {
	if v, ok := ctx.Value(ctxKeyClaims).(*Claims); ok {
		return v
	}
	return nil
}

// TraceMiddleware propagates/creates X-Trace-Id (MASTER-FR-028).
func TraceMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		trace := r.Header.Get("X-Trace-Id")
		if trace == "" {
			trace = uuid.NewString()
		}
		w.Header().Set("X-Trace-Id", trace)
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ctxKeyTraceID, trace)))
	})
}

// RecoverMiddleware converts panics into 500 envelopes.
func RecoverMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if rec := recover(); rec != nil {
				slog.Error("panic in handler", "panic", rec, "path", r.URL.Path)
				writeErrCode(w, r, http.StatusInternalServerError, "INTERNAL", "internal error", nil)
			}
		}()
		next.ServeHTTP(w, r)
	})
}

// AuthMiddleware verifies the bearer token and stashes claims. Tenant context
// comes exclusively from the verified JWT (MASTER-FR-001/002).
func AuthMiddleware(v *Verifier) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			raw := r.Header.Get("Authorization")
			if !strings.HasPrefix(raw, "Bearer ") {
				writeErrCode(w, r, http.StatusUnauthorized, "UNAUTHENTICATED", "missing bearer token", nil)
				return
			}
			claims, err := v.Verify(r.Context(), strings.TrimPrefix(raw, "Bearer "))
			if err != nil {
				writeErrCode(w, r, http.StatusUnauthorized, "UNAUTHENTICATED", "invalid token", nil)
				return
			}
			next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ctxKeyClaims, claims)))
		})
	}
}

// opFrom builds the request context from verified claims only.
func opFrom(r *http.Request) (domain.Op, bool) {
	claims := ClaimsFrom(r.Context())
	if claims == nil {
		return domain.Op{}, false
	}
	tenant, err := claims.Tenant()
	if err != nil {
		return domain.Op{}, false
	}
	op := domain.Op{Tenant: tenant, TraceID: TraceID(r.Context()), Platform: claims.IsPlatform()}
	switch claims.Typ {
	case "agent_obo":
		op.Actor = domain.Actor{Type: "user", ID: claims.OboSub}
		op.ViaAgent = &domain.ViaAgent{AgentID: claims.AgentID, Version: claims.AgentVersion}
	case "agent_autonomous":
		op.Actor = domain.Actor{Type: "agent", ID: claims.AgentID}
		op.ViaAgent = &domain.ViaAgent{AgentID: claims.AgentID, Version: claims.AgentVersion}
	case "service":
		op.Actor = domain.Actor{Type: "service", ID: claims.Sub}
	default:
		op.Actor = domain.Actor{Type: "user", ID: claims.Sub}
	}
	return op, true
}

// RequireAction gates a route on an action (MASTER-FR-016) via the OPA sidecar
// port (MASTER-FR-012). Denials emit an audit event (MASTER-FR-040). Platform-
// only actions additionally require the caller to be a platform operator.
func (s *Server) RequireAction(action string) func(http.Handler) http.Handler {
	s.guarded = append(s.guarded, action) // record for the catalog drift test
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			claims := ClaimsFrom(r.Context())
			if claims == nil {
				writeErrCode(w, r, http.StatusUnauthorized, "UNAUTHENTICATED", "missing claims", nil)
				return
			}
			if authz.PlatformOnly(action) && !claims.IsPlatform() {
				s.auditDenial(r, action)
				writeErrCode(w, r, http.StatusForbidden, "PERMISSION_DENIED", "platform operator only: "+action, nil)
				return
			}
			in := authz.Input{
				Subject: authz.Subject{ID: claims.Sub, Typ: claims.Typ, OboSub: claims.OboSub, Scopes: claims.Scopes},
				Action:  action,
				Tenant:  claims.TenantID,
			}
			if !s.Authz.Allow(r.Context(), in) {
				s.auditDenial(r, action)
				writeErrCode(w, r, http.StatusForbidden, "PERMISSION_DENIED", "not allowed: "+action, nil)
				return
			}
			next.ServeHTTP(w, r.WithContext(r.Context()))
		})
	}
}

func (s *Server) auditDenial(r *http.Request, action string) {
	op, ok := opFrom(r)
	if !ok {
		return
	}
	env := events.NewEnvelope(events.EvPermissionDenied, op, "",
		map[string]any{"action": action, "path": r.URL.Path})
	if err := s.Store.EmitEvent(r.Context(), env); err != nil {
		slog.Warn("audit denial emit failed", "err", err)
	}
}

// auditCrossTenant emits security.cross_tenant_denied on 404s for id-addressed
// resources (MASTER-FR-003). RLS makes cross-tenant and nonexistent
// indistinguishable, so every such 404 is audited.
func (s *Server) auditCrossTenant(r *http.Request, resourceURN string) {
	op, ok := opFrom(r)
	if !ok {
		return
	}
	env := events.NewEnvelope(events.EvCrossTenantDenied, op, resourceURN,
		map[string]any{"path": r.URL.Path})
	if err := s.Store.EmitEvent(r.Context(), env); err != nil {
		slog.Warn("cross-tenant audit emit failed", "err", err)
	}
}
