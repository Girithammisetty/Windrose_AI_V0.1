package authjwt

import (
	"context"
	"net/http"
	"strings"
)

type ctxKey int

const claimsKey ctxKey = iota

// FromContext returns the verified claims populated by Middleware.
func FromContext(ctx context.Context) (*Claims, bool) {
	c, ok := ctx.Value(claimsKey).(*Claims)
	return c, ok
}

// WithClaims stashes claims on a context (used by Middleware and tests).
func WithClaims(ctx context.Context, c *Claims) context.Context {
	return context.WithValue(ctx, claimsKey, c)
}

// Middleware verifies the Bearer token on every request and stashes the claims
// on the request context (MASTER-FR-010). Unauthenticated requests get a 401
// error envelope. onError lets the service render its own envelope; when nil a
// minimal JSON 401 is written.
func (v *Verifier) Middleware(onError func(w http.ResponseWriter, r *http.Request, err error)) func(http.Handler) http.Handler {
	if onError == nil {
		onError = func(w http.ResponseWriter, _ *http.Request, _ error) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusUnauthorized)
			_, _ = w.Write([]byte(`{"error":{"code":"UNAUTHENTICATED","message":"invalid or missing token"}}`))
		}
	}
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			tok := bearer(r)
			if tok == "" {
				onError(w, r, ErrNoToken)
				return
			}
			claims, err := v.Verify(r.Context(), tok)
			if err != nil {
				onError(w, r, err)
				return
			}
			next.ServeHTTP(w, r.WithContext(WithClaims(r.Context(), claims)))
		})
	}
}

// ErrNoToken is returned when the Authorization header is absent/malformed.
var ErrNoToken = errNoToken{}

type errNoToken struct{}

func (errNoToken) Error() string { return "missing bearer token" }

func bearer(r *http.Request) string {
	h := r.Header.Get("Authorization")
	const p = "Bearer "
	if len(h) > len(p) && strings.EqualFold(h[:len(p)], p) {
		return strings.TrimSpace(h[len(p):])
	}
	return ""
}
