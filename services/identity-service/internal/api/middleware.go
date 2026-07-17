package api

import (
	"context"
	"net/http"
	"strings"

	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/domain"
)

// traceMiddleware assigns/propagates X-Trace-Id (MASTER-FR-028).
func traceMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get("X-Trace-Id")
		if id == "" {
			id = uuid.NewString()
		}
		w.Header().Set("X-Trace-Id", id)
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ctxTraceID, id)))
	})
}

// recoverMiddleware converts panics into the error envelope.
func recoverMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if rec := recover(); rec != nil {
				writeErr(w, r, &domain.Error{Code: "INTERNAL", HTTP: http.StatusInternalServerError, Message: "internal error"})
			}
		}()
		next.ServeHTTP(w, r)
	})
}

// authMiddleware verifies the bearer JWT (MASTER-FR-010; RS256-only, so
// alg=none fails here — AC-13) and stores claims in the context.
func (s *Server) authMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := r.Header.Get("Authorization")
		if !strings.HasPrefix(h, "Bearer ") {
			writeErr(w, r, domain.EUnauthenticated("missing bearer token"))
			return
		}
		claims, err := s.Verifier.Verify(strings.TrimPrefix(h, "Bearer "))
		if err != nil {
			writeErr(w, r, domain.EUnauthenticated("invalid token"))
			return
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ctxClaims, claims)))
	})
}

// spiffeMiddleware extracts the mesh-provided SPIFFE identity header.
// In production the sidecar/mesh terminates mTLS and injects the verified
// identity (MASTER-FR-014); this middleware trusts only the configured header
// name and the TrustedSpiffeIDs allowlist (documented adapter, see README).
//
// F-2: a bare inbound header is spoofable, so it is honored ONLY when
// TrustSpiffeHeader is explicitly enabled (default false). When disabled the
// header is dropped entirely — downstream sees an empty SPIFFE id and
// agent-autonomous token minting is refused. Enable only where an upstream
// proxy strips any client-supplied X-Spiffe-Id and re-injects the verified one.
func (s *Server) spiffeMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := ""
		if s.TrustSpiffeHeader {
			id = r.Header.Get("X-Spiffe-Id")
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ctxSpiffeID, id)))
	})
}

// isAdminIssuableTyp is the F-5 typ allowlist for identity's own admin
// endpoints: only human (user) and workload (service) tokens may drive tenant
// administration. An agent_obo / agent_autonomous token is rejected up front
// so that a scope string like "identity.user.admin" carried by an agent
// cannot pass ScopeAuthorizer for user/tenant mutations until OPA intersection
// (MASTER-FR-015) is wired. Defense-in-depth, not the primary control.
func isAdminIssuableTyp(typ string) bool {
	return typ == domain.TypUser || typ == domain.TypService
}

// requireScope gates a route on an action scope (MASTER-FR-016) via the
// Authorizer port. Super-admin (platform.admin) always passes (IDN-FR-025).
func (s *Server) requireScope(action string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			claims := ClaimsFrom(r.Context())
			if claims == nil {
				writeErr(w, r, domain.EUnauthenticated("authentication required"))
				return
			}
			if !isAdminIssuableTyp(claims.Typ) { // F-5
				writeErr(w, r, domain.EPermissionDenied("token type "+claims.Typ+" may not perform identity administration"))
				return
			}
			if !s.Authz.Allow(r.Context(), claims, action, "") {
				writeErr(w, r, domain.EPermissionDenied("missing required scope "+action))
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// requireSuperAdmin gates platform endpoints (IDN-FR-025).
func (s *Server) requireSuperAdmin(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		claims := ClaimsFrom(r.Context())
		if claims == nil {
			writeErr(w, r, domain.EUnauthenticated("authentication required"))
			return
		}
		if !isAdminIssuableTyp(claims.Typ) { // F-5
			writeErr(w, r, domain.EPermissionDenied("token type "+claims.Typ+" may not perform identity administration"))
			return
		}
		if !claims.IsSuperAdmin() {
			writeErr(w, r, domain.EPermissionDenied("super-admin only"))
			return
		}
		next.ServeHTTP(w, r)
	})
}

// actorFrom builds the audit actor from the verified claims.
func actorFrom(claims *domain.Claims) domain.Actor {
	a := domain.Actor{Type: "user", ID: claims.Subject}
	switch claims.Typ {
	case domain.TypService:
		a.Type = "service"
	case domain.TypAgentOBO, domain.TypAgentAutonomous:
		a.Type = "agent"
	}
	if claims.IsSuperAdmin() {
		a.Scope = "platform" // IDN-FR-025 audit attribution
	}
	return a
}
