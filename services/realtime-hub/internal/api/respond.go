// Package api is realtime-hub's HTTP/SSE/WebSocket surface (BRD 20 §5): the
// chi router, JWT/ticket connect auth, stream endpoints, incremental
// subscription and token-refresh side channels, the internal publish endpoint,
// admin, and health/metrics. Error and pagination contracts come from the
// shared go-common/httpx helpers.
package api

import (
	"context"
	"net/http"

	"github.com/google/uuid"

	"github.com/windrose-ai/go-common/httpx"
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

// writeErr writes the master error envelope (MASTER-FR-024).
func writeErr(w http.ResponseWriter, r *http.Request, status int, code, msg string, retryAfter int) {
	httpx.WriteError(w, status, code, msg, TraceID(r.Context()), nil, retryAfter)
}

// traceMiddleware propagates/creates X-Trace-Id (MASTER-FR-028).
func traceMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		trace := r.Header.Get("X-Trace-Id")
		if trace == "" {
			trace = uuid.NewString()
		}
		w.Header().Set("X-Trace-Id", trace)
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ctxKeyTrace, trace)))
	})
}
