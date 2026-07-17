// Package httpx holds the shared HTTP contract helpers required of every
// Windrose service by the master BRD: the error envelope (MASTER-FR-024) and
// cursor pagination (MASTER-FR-022). The logic here is lifted verbatim (same
// field names, same codes, same shapes) from the copies identity-service and
// rbac-service vendored in wave 1, so behavior is unchanged after extraction.
package httpx

import (
	"encoding/json"
	"net/http"
	"strconv"
)

// Stable machine-readable error codes (MASTER-FR-024).
const (
	CodeValidation      = "VALIDATION_FAILED"
	CodeNotFound        = "NOT_FOUND"
	CodePermission      = "PERMISSION_DENIED"
	CodeUnauthenticated = "UNAUTHENTICATED"
	CodeConflict        = "CONFLICT"
	CodeRateLimited     = "RATE_LIMITED"
	CodeBudget          = "BUDGET_EXHAUSTED"
	CodeNotImplemented  = "NOT_IMPLEMENTED"
	CodeInternal        = "INTERNAL"
)

// FieldError is one per-field validation problem (MASTER-FR-024 details).
type FieldError struct {
	Field   string `json:"field"`
	Message string `json:"message"`
}

// ErrorBody is the master error envelope (MASTER-FR-024).
type ErrorBody struct {
	Error ErrorInner `json:"error"`
}

// ErrorInner is the inner error object.
type ErrorInner struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	Details any    `json:"details,omitempty"`
	TraceID string `json:"trace_id"`
}

// WriteJSON writes v as JSON with the given status.
func WriteJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// WriteError writes the master error envelope. retryAfter (seconds), when > 0,
// sets the Retry-After header (RATE_LIMITED responses).
func WriteError(w http.ResponseWriter, status int, code, message, traceID string, details any, retryAfter int) {
	if retryAfter > 0 {
		w.Header().Set("Retry-After", strconv.Itoa(retryAfter))
	}
	WriteJSON(w, status, ErrorBody{Error: ErrorInner{
		Code: code, Message: message, Details: details, TraceID: traceID,
	}})
}
