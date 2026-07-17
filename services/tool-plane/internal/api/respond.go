// Package api is the tool-plane HTTP layer: the tool-registry admin/catalog REST
// API and shared middleware (JWT verification via go-common/authjwt, error
// envelope + cursor pagination via go-common/httpx, trace + idempotency). The
// mcp-gateway data plane (/mcp) is served by internal/mcp wired to the enforce
// pipeline; both are assembled in cmd/{registry,gateway}.
package api

import (
	"context"
	"encoding/json"
	"net/http"

	"github.com/google/uuid"

	"github.com/windrose-ai/go-common/authjwt"
	"github.com/windrose-ai/go-common/httpx"
	"github.com/windrose-ai/tool-plane/internal/domain"
)

type ctxKey int

const ctxKeyTrace ctxKey = iota

// TraceID returns the request trace id (MASTER-FR-028).
func TraceID(ctx context.Context) string {
	if v, ok := ctx.Value(ctxKeyTrace).(string); ok {
		return v
	}
	return ""
}

func withTrace(ctx context.Context, trace string) context.Context {
	return context.WithValue(ctx, ctxKeyTrace, trace)
}

// claims returns verified JWT claims from the request context.
func claims(r *http.Request) *authjwt.Claims {
	c, _ := authjwt.FromContext(r.Context())
	return c
}

// tenantOf parses the caller's tenant from verified claims.
func tenantOf(r *http.Request) (uuid.UUID, bool) {
	c := claims(r)
	if c == nil {
		return uuid.Nil, false
	}
	t, err := c.Tenant()
	if err != nil {
		return uuid.Nil, false
	}
	return t, true
}

// writeErr renders a domain.Error (or generic 500) as the master envelope.
func writeErr(w http.ResponseWriter, r *http.Request, err error) {
	trace := TraceID(r.Context())
	if de, ok := err.(*domain.Error); ok {
		httpx.WriteError(w, de.HTTP, de.Code, de.Message, trace, de.Details, de.RetryAfter)
		return
	}
	httpx.WriteError(w, http.StatusInternalServerError, domain.CodeInternal, "internal error", trace, nil, 0)
}

// writeJSON writes v with status.
func writeJSON(w http.ResponseWriter, status int, v any) {
	httpx.WriteJSON(w, status, v)
}

// jsonString marshals s as a JSON string literal (readyz reason rendering).
func jsonString(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}

// decodeJSON reads a JSON body into v, returning a VALIDATION_FAILED on failure.
func decodeJSON(r *http.Request, v any) error {
	dec := json.NewDecoder(r.Body)
	if err := dec.Decode(v); err != nil {
		return domain.EValidation("malformed JSON body", nil)
	}
	return nil
}
