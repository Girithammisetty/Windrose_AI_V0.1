package api

import (
	"bytes"
	"context"
	"log/slog"
	"net/http"

	"github.com/google/uuid"

	"github.com/windrose-ai/go-common/authjwt"
	"github.com/windrose-ai/notification-service/internal/authz"
	"github.com/windrose-ai/notification-service/internal/domain"
	"github.com/windrose-ai/notification-service/internal/events"
)

type ctxKey int

const ctxKeyTrace ctxKey = iota

func traceID(ctx context.Context) string {
	if v, ok := ctx.Value(ctxKeyTrace).(string); ok {
		return v
	}
	return ""
}

// TraceMiddleware propagates/creates X-Trace-Id (MASTER-FR-028).
func TraceMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		trace := r.Header.Get("X-Trace-Id")
		if trace == "" {
			trace = uuid.NewString()
		}
		w.Header().Set("X-Trace-Id", trace)
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ctxKeyTrace, trace)))
	})
}

// RecoverMiddleware converts panics into 500 envelopes.
func RecoverMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if rec := recover(); rec != nil {
				slog.Error("panic in handler", "panic", rec, "path", r.URL.Path)
				writeErr(w, r, &domain.Error{Code: domain.CodeInternal, HTTP: http.StatusInternalServerError, Message: "internal error"})
			}
		}()
		next.ServeHTTP(w, r)
	})
}

// claims returns the verified JWT claims (from go-common authjwt middleware).
func claims(ctx context.Context) *authjwt.Claims {
	c, _ := authjwt.FromContext(ctx)
	return c
}

// op builds the authenticated mutation context from verified claims only
// (MASTER-FR-001/002). ok=false when unauthenticated.
func op(r *http.Request) (domain.Op, bool) {
	c := claims(r.Context())
	if c == nil {
		return domain.Op{}, false
	}
	tenant, err := c.Tenant()
	if err != nil {
		return domain.Op{}, false
	}
	o := domain.Op{Tenant: tenant, TraceID: traceID(r.Context()), UserID: c.EffectiveUser()}
	switch c.Typ {
	case authjwt.TypAgentOBO:
		o.Actor = domain.Actor{Type: "user", ID: c.OboSub}
		o.ViaAgent = &domain.ViaAgent{AgentID: c.AgentID, Version: c.AgentVersion}
	case authjwt.TypAgentAutonomous:
		o.Actor = domain.Actor{Type: "agent", ID: c.AgentID}
		o.ViaAgent = &domain.ViaAgent{AgentID: c.AgentID, Version: c.AgentVersion}
	case authjwt.TypService:
		o.Actor = domain.Actor{Type: "service", ID: c.Sub}
	default:
		o.Actor = domain.Actor{Type: "user", ID: c.Sub}
	}
	return o, true
}

// RequireAction gates a route on an action via the OPA sidecar (MASTER-FR-012).
// Denials emit an audit event (MASTER-FR-040).
func (s *Server) RequireAction(action string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			c := claims(r.Context())
			if c == nil {
				writeErr(w, r, domain.EUnauthenticated("missing claims"))
				return
			}
			in := authz.Input{
				Subject: authz.Subject{ID: c.Sub, Typ: c.Typ, OboSub: c.OboSub, Scopes: c.Scopes},
				Action:  action,
				Tenant:  c.TenantID,
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
	o, ok := op(r)
	if !ok {
		return
	}
	env := events.FromOp(events.EvPermissionDenied, o, "", map[string]any{"action": action, "path": r.URL.Path})
	_ = s.Store.EmitAudit(r.Context(), env)
}

// notFound writes an audited 404 (MASTER-FR-003): cross-tenant and nonexistent
// are indistinguishable by design; every id-addressed 404 emits the audit event.
func (s *Server) notFound(w http.ResponseWriter, r *http.Request) {
	if o, ok := op(r); ok {
		env := events.FromOp(events.EvCrossTenant, o, "", map[string]any{"path": r.URL.Path, "method": r.Method})
		_ = s.Store.EmitAudit(r.Context(), env)
	}
	writeErr(w, r, domain.ENotFound())
}

// IdempotencyMiddleware replays duplicate POSTs within 24h (MASTER-FR-025).
func (s *Server) IdempotencyMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		key := r.Header.Get("Idempotency-Key")
		if r.Method != http.MethodPost || key == "" {
			next.ServeHTTP(w, r)
			return
		}
		c := claims(r.Context())
		if c == nil {
			next.ServeHTTP(w, r)
			return
		}
		tenant, err := c.Tenant()
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
			_ = s.Store.PutIdempotency(r.Context(), tenant, key, r.Method, r.URL.Path, rec.status, rec.buf.Bytes())
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

// parsePage parses ?limit= & ?cursor= (uuid) for cursor pagination.
func parsePage(r *http.Request) (int, *uuid.UUID) {
	limit := 50
	if v := r.URL.Query().Get("limit"); v != "" {
		if n := atoiDefault(v, 50); n > 0 {
			limit = n
		}
	}
	if limit > 200 {
		limit = 200
	}
	var cursor *uuid.UUID
	if c := r.URL.Query().Get("cursor"); c != "" {
		if id, err := uuid.Parse(c); err == nil {
			cursor = &id
		}
	}
	return limit, cursor
}

func atoiDefault(s string, def int) int {
	n := 0
	for _, r := range s {
		if r < '0' || r > '9' {
			return def
		}
		n = n*10 + int(r-'0')
	}
	return n
}
