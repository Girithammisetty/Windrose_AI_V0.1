package domain

import "net/http"

// Platform + tool-plane error codes (MASTER-FR-024, BRD §3 error-code map).
const (
	CodeValidation      = "VALIDATION_FAILED"
	CodeNotFound        = "NOT_FOUND"
	CodePermission      = "PERMISSION_DENIED"
	CodeUnauthenticated = "UNAUTHENTICATED"
	CodeConflict        = "CONFLICT"
	CodeRateLimited     = "RATE_LIMITED"
	CodeInternal        = "INTERNAL"

	// Tool-plane specific (BRD §3 mapping table).
	CodeToolKilled       = "TOOL_KILLED"
	CodeToolRetired      = "TOOL_RETIRED"
	CodeToolDisabled     = "TOOL_DISABLED"
	CodeProposalRequired = "PROPOSAL_REQUIRED"
	CodeToolOutputInvalid = "TOOL_OUTPUT_INVALID"
	CodeToolBackendTimeout = "TOOL_BACKEND_TIMEOUT"
	CodeToolBackendError = "TOOL_BACKEND_ERROR"
	CodePolicyUnavailable = "POLICY_UNAVAILABLE"
	CodeTokenInvalid     = "TOKEN_INVALID"
)

// Error is a coded error carrying an HTTP analog and optional per-field details.
type Error struct {
	Code    string
	HTTP    int
	Message string
	Details any
	// RetryAfter (seconds) sets Retry-After on RATE_LIMITED.
	RetryAfter int
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }

// Constructors for the common shapes.

func EValidation(msg string, details any) *Error {
	return &Error{Code: CodeValidation, HTTP: http.StatusUnprocessableEntity, Message: msg, Details: details}
}

func ENotFound() *Error {
	return &Error{Code: CodeNotFound, HTTP: http.StatusNotFound, Message: "not found"}
}

func EConflict(msg string) *Error {
	return &Error{Code: CodeConflict, HTTP: http.StatusConflict, Message: msg}
}

func EPermission(msg string) *Error {
	return &Error{Code: CodePermission, HTTP: http.StatusForbidden, Message: msg}
}

func EUnauthenticated(msg string) *Error {
	return &Error{Code: CodeUnauthenticated, HTTP: http.StatusUnauthorized, Message: msg}
}

func EInternal(msg string) *Error {
	return &Error{Code: CodeInternal, HTTP: http.StatusInternalServerError, Message: msg}
}
