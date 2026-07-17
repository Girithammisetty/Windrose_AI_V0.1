package api

import (
	"bytes"
	"log/slog"
	"net/http"

	"github.com/google/uuid"

	"github.com/windrose-ai/go-common/authjwt"
	"github.com/windrose-ai/tool-plane/internal/authz"
	"github.com/windrose-ai/tool-plane/internal/domain"
)

// traceMiddleware propagates/creates X-Trace-Id (MASTER-FR-028).
func traceMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		trace := r.Header.Get("X-Trace-Id")
		if trace == "" {
			trace = uuid.NewString()
		}
		w.Header().Set("X-Trace-Id", trace)
		next.ServeHTTP(w, r.WithContext(withTrace(r.Context(), trace)))
	})
}

// recoverMiddleware turns panics into 500 envelopes.
func recoverMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if rec := recover(); rec != nil {
				slog.Error("panic in handler", "panic", rec, "path", r.URL.Path)
				writeErr(w, r, nil)
			}
		}()
		next.ServeHTTP(w, r)
	})
}

// isPlatformOperator reports whether verified claims identify the platform
// operator (superadmin). The platform marks a superadmin by the `super_admin`
// scope on a user/service token (mirrors rbac-service api.ScopeSuperAdmin and
// its RequireSuperAdmin gate; the e2e harness superadmin token carries it with
// typ=service and the reserved NIL tenant). HasScope treats "*" as the
// unrestricted wildcard — the same semantics rbac applies. Agent tokens
// (agent_obo/agent_autonomous) can NEVER be platform operators, even with a
// wildcard scope: agents are always authorized via OPA against the permissions
// of their principal.
func isPlatformOperator(c *authjwt.Claims) bool {
	if c == nil {
		return false
	}
	if c.Typ != authjwt.TypUser && c.Typ != authjwt.TypService {
		return false
	}
	return c.HasScope(authz.ScopeSuperAdmin)
}

// requireAction gates an admin route on an action (MASTER-FR-016) via the
// local OPA sidecar reading the rbac permissions_flat projection
// (MASTER-FR-012), the same pattern as chart-service's authorize and
// case-service's RequireAction. Fail-closed: a nil authorizer or any OPA/Redis
// error denies. Two verified-token fast paths sit in front of OPA:
//
//   - platform operator (super_admin scope on a user/service token) — the
//     platform's cross-tenant identity (see isPlatformOperator);
//   - a SERVICE token whose scopes carry the exact action (or the "*"
//     wildcard) — service-to-service tokens are minted by the platform IdP
//     with explicit scopes and have no per-user rbac projection; rbac itself
//     trusts typ=service the same way for /actions/register.
//
// Users and agents always go through OPA (agent_obo is authorized as its OBO
// principal via the projection loader).
func (s *RegistryServer) requireAction(action string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			c := claims(r)
			if c == nil {
				writeErr(w, r, domain.EUnauthenticated("missing claims"))
				return
			}
			if isPlatformOperator(c) || (c.Typ == authjwt.TypService && c.HasScope(action)) {
				next.ServeHTTP(w, r)
				return
			}
			if s.Authz == nil {
				// No authorizer wired → fail closed (BR-1).
				writeErr(w, r, domain.EPermission("not permitted: "+action))
				return
			}
			in := authz.AdminInput{
				Subject: authz.AdminSubject{ID: c.Sub, Typ: c.Typ, OboSub: c.OboSub, Scopes: c.Scopes},
				Action:  action,
				Tenant:  c.TenantID,
			}
			if !s.Authz.Allow(r.Context(), in) {
				slog.Warn("admin authorize denied", "action", action, "sub", c.Sub, "typ", c.Typ, "tenant", c.TenantID, "path", r.URL.Path)
				writeErr(w, r, domain.EPermission("not permitted: "+action))
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// idempotencyMiddleware implements MASTER-FR-025 for POST endpoints: a duplicate
// Idempotency-Key within 24h replays the original response.
func (s *RegistryServer) idempotencyMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		key := r.Header.Get("Idempotency-Key")
		if r.Method != http.MethodPost || key == "" {
			next.ServeHTTP(w, r)
			return
		}
		tenant, ok := tenantOf(r)
		if !ok {
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
