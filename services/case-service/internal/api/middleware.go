package api

import (
	"bytes"
	"context"
	"log/slog"
	"net/http"
	"strings"

	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/authz"
	"github.com/windrose-ai/case-service/internal/domain"
	"github.com/windrose-ai/case-service/internal/events"
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
				writeErr(w, r, domain.EInternal("internal error"))
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
				writeErr(w, r, domain.EUnauthenticated("missing bearer token"))
				return
			}
			claims, err := v.Verify(r.Context(), strings.TrimPrefix(raw, "Bearer "))
			if err != nil {
				writeErr(w, r, domain.EUnauthenticated("invalid token"))
				return
			}
			next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ctxKeyClaims, claims)))
		})
	}
}

// opFrom builds the mutation context from verified claims only (MASTER-FR-041
// dual attribution for agents).
func opFrom(r *http.Request) (domain.Op, bool) {
	claims := ClaimsFrom(r.Context())
	if claims == nil {
		return domain.Op{}, false
	}
	tenant, err := claims.Tenant()
	if err != nil {
		return domain.Op{}, false
	}
	op := domain.Op{Tenant: tenant, TraceID: TraceID(r.Context()), UserID: claims.EffectiveUser()}
	if claims.WorkspaceID != "" {
		if ws, err := uuid.Parse(claims.WorkspaceID); err == nil {
			op.WorkspaceID = ws
		}
	}
	switch claims.Typ {
	case domain.TypAgentOBO:
		op.Actor = domain.Actor{Type: "user", ID: claims.OboSub}
		op.ViaAgent = &domain.ViaAgent{AgentID: claims.AgentID, Version: claims.AgentVersion}
	case domain.TypAgentAutonomous:
		op.Actor = domain.Actor{Type: "agent", ID: claims.AgentID}
		op.ViaAgent = &domain.ViaAgent{AgentID: claims.AgentID, Version: claims.AgentVersion}
	case domain.TypService:
		op.Actor = domain.Actor{Type: "service", ID: claims.Sub}
	default:
		op.Actor = domain.Actor{Type: "user", ID: claims.Sub}
	}
	return op, true
}

// RequireAction gates a route on an action (MASTER-FR-016) via the local OPA
// sidecar (MASTER-FR-012). Denials emit an audit event (MASTER-FR-040).
func (s *Server) RequireAction(action string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			claims := ClaimsFrom(r.Context())
			if claims == nil {
				writeErr(w, r, domain.EUnauthenticated("missing claims"))
				return
			}
			in := authz.Input{
				Subject:     authz.Subject{ID: claims.Sub, Typ: claims.Typ, OboSub: claims.OboSub, Scopes: claims.Scopes},
				Action:      action,
				WorkspaceID: claims.WorkspaceID,
				Tenant:      claims.TenantID,
			}
			if !s.Authz.Allow(r.Context(), in) {
				s.auditDenial(r, action)
				writeErr(w, r, domain.EPermissionDenied("not allowed: "+action))
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

func (s *Server) auditDenial(r *http.Request, action string) {
	op, ok := opFrom(r)
	if !ok {
		return
	}
	env := events.NewEnvelope(events.EvPermissionDenied, op, "", map[string]any{"action": action, "path": r.URL.Path})
	if err := s.Store.InsertAudit(r.Context(), env); err != nil {
		slog.Warn("audit denial emit failed", "err", err)
	}
}

// notFound writes the 404 envelope and audits cross-tenant access
// (MASTER-FR-003, AC-13).
func (s *Server) notFound(w http.ResponseWriter, r *http.Request) {
	op, ok := opFrom(r)
	if ok {
		env := events.NewEnvelope(events.EvCrossTenantDenied, op, "", map[string]any{"path": r.URL.Path, "method": r.Method})
		if err := s.Store.InsertAudit(r.Context(), env); err != nil {
			slog.Warn("audit cross-tenant emit failed", "err", err)
		}
	}
	writeErr(w, r, domain.ENotFound())
}

// IdempotencyMiddleware implements MASTER-FR-025 for POST endpoints.
func (s *Server) IdempotencyMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		key := r.Header.Get("Idempotency-Key")
		if r.Method != http.MethodPost || key == "" {
			next.ServeHTTP(w, r)
			return
		}
		claims := ClaimsFrom(r.Context())
		if claims == nil {
			next.ServeHTTP(w, r)
			return
		}
		tenant, err := claims.Tenant()
		if err != nil {
			next.ServeHTTP(w, r)
			return
		}
		if rec, err := s.Store.GetIdempotency(r.Context(), tenant, key); err == nil && rec != nil {
			w.Header().Set("Idempotency-Replayed", "true")
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(rec.Status)
			_, _ = w.Write(rec.Response)
			return
		}
		rec := &responseRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(rec, r)
		if rec.status < 500 {
			if err := s.Store.PutIdempotency(r.Context(), tenant, key, r.Method, r.URL.Path, rec.status, rec.buf.Bytes()); err != nil {
				slog.Warn("idempotency store failed", "err", err)
			}
		}
	})
}

type responseRecorder struct {
	http.ResponseWriter
	status int
	buf    bytes.Buffer
}

func (r *responseRecorder) WriteHeader(status int) {
	r.status = status
	r.ResponseWriter.WriteHeader(status)
}

func (r *responseRecorder) Write(b []byte) (int, error) {
	r.buf.Write(b)
	return r.ResponseWriter.Write(b)
}
