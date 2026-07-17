package api

import (
	"context"
	"log/slog"
	"net/http"

	"github.com/windrose-ai/chart-service/internal/authz"
	"github.com/windrose-ai/chart-service/internal/domain"
)

// traceMiddleware assigns a trace id and sets X-Trace-Id (MASTER-FR-028).
func traceMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		tid := r.Header.Get("traceparent")
		if tid == "" {
			tid = newTraceID()
		}
		w.Header().Set("X-Trace-Id", tid)
		ctx := context.WithValue(r.Context(), traceKey, tid)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// recoverer turns panics into 500s (never leaks stack to the client).
func recoverer(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if rec := recover(); rec != nil {
				slog.Error("panic in handler", "err", rec, "path", r.URL.Path)
				writeErr(w, r, domain.EInternal("internal error"))
			}
		}()
		next.ServeHTTP(w, r)
	})
}

// authMiddleware verifies the RS256 JWT via the shared verifier and stashes
// claims (MASTER-FR-010).
func (s *Server) authMiddleware(next http.Handler) http.Handler {
	return s.Verifier.Middleware(func(w http.ResponseWriter, r *http.Request, err error) {
		writeErr(w, r, domain.EUnauthenticated("invalid token"))
	})(next)
}

// authorize runs an OPA check for (action, resourceURN) and writes the response
// mapping a deny to 404 for read-scoped resources to avoid existence leaks
// (MASTER-FR-003) or 403 for writes. Returns true when allowed.
func (s *Server) authorize(w http.ResponseWriter, r *http.Request, action, resourceURN, workspaceID string) bool {
	c, tenant, ok := s.claims(w, r)
	if !ok {
		return false
	}
	in := authz.Input{
		Subject:     authz.Subject{ID: c.EffectiveUser(), Typ: c.Typ, OboSub: c.OboSub, Scopes: c.Scopes},
		Action:      action,
		ResourceURN: resourceURN,
		WorkspaceID: workspaceID,
		Tenant:      tenant.String(),
	}
	if !s.Authz.Allow(r.Context(), in) {
		writeErr(w, r, domain.EPermission("not permitted: "+action))
		return false
	}
	return true
}
