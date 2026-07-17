// Package domain holds query-service's core types: saved queries, typed
// variable declarations and strict value coercion (QRY-FR-002/003, BR-3),
// the execution state machine (BRD §4.2), enforced cost ceilings
// (QRY-FR-042) and the stable error catalog (BRD §4.4, MASTER-FR-024).
package domain

import (
	"errors"
	"net/http"
)

// Error is the service error model. It maps 1:1 onto the master error
// envelope {error: {code, message, details?, trace_id}} (MASTER-FR-024).
type Error struct {
	Code    string
	HTTP    int
	Message string
	Details any
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }

// AsError unwraps a *domain.Error if err carries one.
func AsError(err error) (*Error, bool) {
	var de *Error
	if errors.As(err, &de) {
		return de, true
	}
	return nil, false
}

// Stable error codes (BRD 05 §4.4).
const (
	CodeValidationFailed    = "VALIDATION_FAILED"
	CodeVariableInvalid     = "VARIABLE_INVALID"
	CodeCostCeilingExceeded = "COST_CEILING_EXCEEDED"
	CodeStatementNotAllowed = "STATEMENT_NOT_ALLOWED"
	CodeDatasetNotFound     = "DATASET_NOT_FOUND"
	CodeUseAsync            = "USE_ASYNC"
	CodeNotFound            = "NOT_FOUND"
	CodeConflict            = "CONFLICT"
	CodeGone                = "GONE"
	CodeRateLimited         = "RATE_LIMITED"
	CodeEngineUnavailable   = "ENGINE_UNAVAILABLE"
	CodePermissionDenied    = "PERMISSION_DENIED"
	CodeUnauthenticated     = "UNAUTHENTICATED"
	CodeNotImplemented      = "NOT_IMPLEMENTED"
	CodeInternal            = "INTERNAL"
)

// EValidation is a 422 semantic validation failure.
func EValidation(msg string) *Error {
	return &Error{Code: CodeValidationFailed, HTTP: http.StatusUnprocessableEntity, Message: msg}
}

// EValidationDetails carries per-field problems in details (MASTER-FR-024).
func EValidationDetails(msg string, details any) *Error {
	return &Error{Code: CodeValidationFailed, HTTP: http.StatusUnprocessableEntity, Message: msg, Details: details}
}

// VariableProblem is one per-variable validation failure (QRY-FR-004, AC-4).
type VariableProblem struct {
	Name    string `json:"name"`
	Problem string `json:"problem"`
}

// EVariableInvalid is the 422 for typed-variable failures (QRY-FR-002/004).
func EVariableInvalid(problems []VariableProblem) *Error {
	return &Error{Code: CodeVariableInvalid, HTTP: http.StatusUnprocessableEntity,
		Message: "one or more variables failed validation", Details: problems}
}

// ECostCeiling is the plan-time 422 for ceiling breaches (QRY-FR-042).
func ECostCeiling(msg string, details any) *Error {
	return &Error{Code: CodeCostCeilingExceeded, HTTP: http.StatusUnprocessableEntity, Message: msg, Details: details}
}

// EStatementNotAllowed is the 403 from AST classification (QRY-FR-020/021).
func EStatementNotAllowed(msg string) *Error {
	return &Error{Code: CodeStatementNotAllowed, HTTP: http.StatusForbidden, Message: msg}
}

// EDatasetNotFound is the 422 for unresolved dataset refs (QRY-FR-005, BR-4).
func EDatasetNotFound(msg string) *Error {
	return &Error{Code: CodeDatasetNotFound, HTTP: http.StatusUnprocessableEntity, Message: msg}
}

// EUseAsync is the 409 sync refusal (QRY-FR-043, BR-5).
func EUseAsync(msg string) *Error {
	return &Error{Code: CodeUseAsync, HTTP: http.StatusConflict, Message: msg}
}

// ENotFound covers both nonexistent and cross-tenant resources
// (MASTER-FR-003: 404, never 403, to avoid existence leaks).
func ENotFound() *Error {
	return &Error{Code: CodeNotFound, HTTP: http.StatusNotFound, Message: "resource not found"}
}

// EConflict is a 409 with the generic CONFLICT code.
func EConflict(msg string) *Error {
	return &Error{Code: CodeConflict, HTTP: http.StatusConflict, Message: msg}
}

// EGone is the 410 for expired results (QRY-FR-062, BR-9). Details carries a
// re_run_hint.
func EGone(msg string, details any) *Error {
	return &Error{Code: CodeGone, HTTP: http.StatusGone, Message: msg, Details: details}
}

// ERateLimited is the 429 for queue overflow (QRY-FR-044).
func ERateLimited(msg string) *Error {
	return &Error{Code: CodeRateLimited, HTTP: http.StatusTooManyRequests, Message: msg}
}

// EEngineUnavailable is the 503 when no engine can serve the plan (BRD §4.4).
func EEngineUnavailable(msg string) *Error {
	return &Error{Code: CodeEngineUnavailable, HTTP: http.StatusServiceUnavailable, Message: msg}
}

// EPermissionDenied is a 403 authz denial.
func EPermissionDenied(msg string) *Error {
	return &Error{Code: CodePermissionDenied, HTTP: http.StatusForbidden, Message: msg}
}

// EUnauthenticated is a 401.
func EUnauthenticated(msg string) *Error {
	return &Error{Code: CodeUnauthenticated, HTTP: http.StatusUnauthorized, Message: msg}
}

// ENotImplemented marks Should-tier stubs (CONVENTIONS: definition of done).
func ENotImplemented(msg string) *Error {
	return &Error{Code: CodeNotImplemented, HTTP: http.StatusNotImplemented, Message: msg}
}
