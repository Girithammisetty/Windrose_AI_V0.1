package api

import (
	"bytes"
	"context"
	"log/slog"
	"net/http"
	"strings"

	"github.com/google/uuid"

	"github.com/windrose-ai/rbac-service/internal/authz"
	"github.com/windrose-ai/rbac-service/internal/domain"
	"github.com/windrose-ai/rbac-service/internal/events"
	"github.com/windrose-ai/rbac-service/internal/store"
)

type ctxKey int

const (
	ctxKeyTraceID ctxKey = iota
	ctxKeyClaims
)

// TraceID returns the request trace id.
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
				writeError(w, r, http.StatusInternalServerError, "INTERNAL", "internal error", nil)
			}
		}()
		next.ServeHTTP(w, r)
	})
}

// AuthMiddleware verifies the bearer token and stashes claims. The tenant
// context comes exclusively from the verified JWT — tenant ids in request
// payloads are ignored for authorization (MASTER-FR-001/002).
func AuthMiddleware(v *Verifier) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			raw := r.Header.Get("Authorization")
			if !strings.HasPrefix(raw, "Bearer ") {
				writeError(w, r, http.StatusUnauthorized, "UNAUTHENTICATED", "missing bearer token", nil)
				return
			}
			claims, err := v.Verify(r.Context(), strings.TrimPrefix(raw, "Bearer "))
			if err != nil {
				writeError(w, r, http.StatusUnauthorized, "UNAUTHENTICATED", "invalid token", nil)
				return
			}
			next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ctxKeyClaims, claims)))
		})
	}
}

// opFrom builds the mutation context from verified claims.
func opFrom(r *http.Request) (store.Op, bool) {
	claims := ClaimsFrom(r.Context())
	if claims == nil {
		return store.Op{}, false
	}
	tenant, err := claims.Tenant()
	if err != nil {
		return store.Op{}, false
	}
	op := store.Op{Tenant: tenant, TraceID: TraceID(r.Context())}
	switch claims.Typ {
	case domain.TypAgentOBO:
		op.Actor = events.Actor{Type: "user", ID: claims.OboSub}
		op.ViaAgent = &events.ViaAgent{AgentID: claims.AgentID, Version: claims.AgentVersion}
	case domain.TypAgentAutonomous:
		op.Actor = events.Actor{Type: "agent", ID: claims.AgentID}
		op.ViaAgent = &events.ViaAgent{AgentID: claims.AgentID, Version: claims.AgentVersion}
	case domain.TypService:
		op.Actor = events.Actor{Type: "service", ID: claims.Sub}
	default:
		op.Actor = events.Actor{Type: "user", ID: claims.Sub}
	}
	return op, true
}

// RequireAction authorizes rbac's own endpoints against SQL ground truth via
// the shared Checker (identical semantics to the projection path; the admin
// flag bypasses per BR-7). Denials emit an audit event (MASTER-FR-040).
func (s *Server) RequireAction(action string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			claims := ClaimsFrom(r.Context())
			if claims == nil {
				writeError(w, r, http.StatusUnauthorized, "UNAUTHENTICATED", "missing claims", nil)
				return
			}
			in := authz.Input{
				Subject: authz.Subject{ID: claims.Sub, Typ: claims.Typ, OboSub: claims.OboSub, Scopes: claims.Scopes},
				Action:  action,
				Tenant:  claims.TenantID,
			}
			d, err := s.Checker.Check(r.Context(), in)
			if err != nil {
				writeError(w, r, http.StatusInternalServerError, "INTERNAL", "authorization check failed", nil)
				return
			}
			if !d.Allowed {
				s.auditDenial(r, claims, action, d.Reason)
				writeError(w, r, http.StatusForbidden, "PERMISSION_DENIED", "not allowed: "+action, map[string]string{"reason": d.Reason})
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// RequireSuperAdmin gates platform-operator endpoints.
func RequireSuperAdmin(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		claims := ClaimsFrom(r.Context())
		if claims == nil || !claims.HasScope(ScopeSuperAdmin) {
			writeError(w, r, http.StatusForbidden, "PERMISSION_DENIED", "super-admin scope required", nil)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// RequireServiceOrSuperAdmin gates the fallback check endpoint (services via
// SPIFFE mTLS in production; service-typed tokens here).
func RequireServiceOrSuperAdmin(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		claims := ClaimsFrom(r.Context())
		if claims == nil || (claims.Typ != domain.TypService && !claims.HasScope(ScopeSuperAdmin)) {
			writeError(w, r, http.StatusForbidden, "PERMISSION_DENIED", "service identity required", nil)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (s *Server) auditDenial(r *http.Request, claims *Claims, action, reason string) {
	op, ok := opFrom(r)
	if !ok {
		return
	}
	env := events.NewEnvelope(events.EvPermissionDenied, op.Tenant, op.Actor, "", op.TraceID,
		map[string]any{"action": action, "reason": reason, "path": r.URL.Path})
	env.ViaAgent = op.ViaAgent
	if err := s.Store.InsertAudit(r.Context(), env); err != nil {
		slog.Warn("audit denial emit failed", "err", err)
	}
	_ = claims
}

// IdempotencyMiddleware implements MASTER-FR-025 for POST endpoints:
// duplicate Idempotency-Key within 24h replays the original response with
// Idempotency-Replayed: true.
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
