// Package domain holds the core business logic of identity-service:
// entities, state machines, validation rules, token issuance rules, the
// provisioning engine, and the repository interfaces the stores implement.
//
// The package depends only on the standard library plus uuid/argon2 so the
// full unit-test tier runs with no external dependencies (CONVENTIONS.md tier 1).
package domain

import (
	"errors"
	"fmt"
)

// Stable machine-readable error codes (MASTER-FR-024).
const (
	CodeValidationFailed  = "VALIDATION_FAILED"
	CodeNotFound          = "NOT_FOUND"
	CodeConflict          = "CONFLICT"
	CodePermissionDenied  = "PERMISSION_DENIED"
	CodeRateLimited       = "RATE_LIMITED"
	CodeUnauthenticated   = "UNAUTHENTICATED"
	CodeInternal          = "INTERNAL"
	CodeTenantSuspended   = "TENANT_SUSPENDED"
	CodeAgentDisabled     = "AGENT_DISABLED"
	CodeInvitationExpired = "INVITATION_EXPIRED"
	CodeCellCapacity      = "CELL_CAPACITY"
	CodeNotImplemented    = "NOT_IMPLEMENTED"
)

// Error is the domain error type. The API layer maps it onto the master BRD
// error envelope {error:{code,message,details,trace_id}} (MASTER-FR-024).
type Error struct {
	Code    string
	HTTP    int
	Message string
	Details any
	// RetryAfterSeconds is set for RATE_LIMITED errors (AC-14).
	RetryAfterSeconds int
}

func (e *Error) Error() string { return fmt.Sprintf("%s: %s", e.Code, e.Message) }

// FieldError is one entry of a VALIDATION_FAILED details list (MASTER-FR-024).
type FieldError struct {
	Field   string `json:"field"`
	Message string `json:"message"`
}

func EValidation(msg string, fields ...FieldError) *Error {
	var det any
	if len(fields) > 0 {
		det = fields
	}
	return &Error{Code: CodeValidationFailed, HTTP: 422, Message: msg, Details: det}
}

func ENotFound(what string) *Error {
	return &Error{Code: CodeNotFound, HTTP: 404, Message: what + " not found"}
}

func EConflict(msg string) *Error {
	return &Error{Code: CodeConflict, HTTP: 409, Message: msg}
}

func EPermissionDenied(msg string) *Error {
	return &Error{Code: CodePermissionDenied, HTTP: 403, Message: msg}
}

func EUnauthenticated(msg string) *Error {
	return &Error{Code: CodeUnauthenticated, HTTP: 401, Message: msg}
}

func ETenantSuspended() *Error {
	return &Error{Code: CodeTenantSuspended, HTTP: 403, Message: "tenant is suspended"}
}

func EAgentDisabled(msg string) *Error {
	return &Error{Code: CodeAgentDisabled, HTTP: 403, Message: msg}
}

func EInvitationExpired() *Error {
	return &Error{
		Code: CodeInvitationExpired, HTTP: 410,
		Message: "invitation has expired",
		Details: map[string]string{"hint": "ask a tenant admin to resend the invitation via POST /api/v1/users/{id}/invite/resend"},
	}
}

func ERateLimited(retryAfterSeconds int) *Error {
	return &Error{Code: CodeRateLimited, HTTP: 429, Message: "rate limit exceeded", RetryAfterSeconds: retryAfterSeconds}
}

func EInternal(msg string) *Error {
	return &Error{Code: CodeInternal, HTTP: 500, Message: msg}
}

func ENotImplemented(msg string) *Error {
	return &Error{Code: CodeNotImplemented, HTTP: 501, Message: msg}
}

// AsError unwraps err into a *domain.Error if possible.
func AsError(err error) (*Error, bool) {
	var de *Error
	if errors.As(err, &de) {
		return de, true
	}
	return nil, false
}
