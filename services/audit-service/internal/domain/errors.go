package domain

import "net/http"

// Error is the service error carrying the master error code + HTTP status
// (MASTER-FR-024).
type Error struct {
	Code    string
	HTTP    int
	Message string
	Details any
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }

// Master error codes (MASTER-FR-024).
const (
	CodeValidation      = "VALIDATION_FAILED"
	CodeNotFound        = "NOT_FOUND"
	CodePermission      = "PERMISSION_DENIED"
	CodeUnauthenticated = "UNAUTHENTICATED"
	CodeConflict        = "CONFLICT"
	CodeInternal        = "INTERNAL"
)

// EValidation builds a 400 with per-field details.
func EValidation(msg string, details any) *Error {
	return &Error{Code: CodeValidation, HTTP: http.StatusBadRequest, Message: msg, Details: details}
}

// ENotFound builds a 404 (also the cross-tenant response, MASTER-FR-003).
func ENotFound() *Error {
	return &Error{Code: CodeNotFound, HTTP: http.StatusNotFound, Message: "not found"}
}

// EPermissionDenied builds a 403.
func EPermissionDenied(msg string) *Error {
	return &Error{Code: CodePermission, HTTP: http.StatusForbidden, Message: msg}
}

// EUnauthenticated builds a 401.
func EUnauthenticated(msg string) *Error {
	return &Error{Code: CodeUnauthenticated, HTTP: http.StatusUnauthorized, Message: msg}
}

// EConflict builds a 409 (unsealed day on verify, AUD-FR-051; supplementing, BR-9).
func EConflict(msg string) *Error {
	return &Error{Code: CodeConflict, HTTP: http.StatusConflict, Message: msg}
}

// EInternal builds a 500.
func EInternal(msg string) *Error {
	return &Error{Code: CodeInternal, HTTP: http.StatusInternalServerError, Message: msg}
}
