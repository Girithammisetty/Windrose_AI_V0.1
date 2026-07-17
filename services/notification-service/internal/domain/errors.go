package domain

import (
	"errors"
	"net/http"
)

// Stable machine-readable error codes (MASTER-FR-024) plus BRD-19 specific ones.
const (
	CodeValidationFailed = "VALIDATION_FAILED"
	CodeNotFound         = "NOT_FOUND"
	CodePermission       = "PERMISSION_DENIED"
	CodeUnauthenticated  = "UNAUTHENTICATED"
	CodeConflict         = "CONFLICT"
	CodeRateLimited      = "RATE_LIMITED"
	CodeInternal         = "INTERNAL"
	CodeVerifyFailed     = "VERIFY_FAILED"                // webhook handshake (NOTIF-FR-022)
	CodeURLForbidden     = "URL_FORBIDDEN"                // SSRF guard (BR-6, AC-12)
	CodeRenderFailed     = "RENDER_FAILED"                // template publish/preview (NOTIF-FR-040, AC-8)
	CodeFilterField      = "FILTER_FIELD_NOT_WHITELISTED" // rule attrs (BR-12)
)

// Error is a typed API error carrying its HTTP status and machine code.
type Error struct {
	Code    string
	HTTP    int
	Message string
	Details any
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }

// AsError extracts a *Error from an error chain.
func AsError(err error) (*Error, bool) {
	var de *Error
	if errors.As(err, &de) {
		return de, true
	}
	return nil, false
}

// Constructors.
func EValidation(msg string, details any) *Error {
	return &Error{Code: CodeValidationFailed, HTTP: http.StatusUnprocessableEntity, Message: msg, Details: details}
}
func ENotFound() *Error {
	return &Error{Code: CodeNotFound, HTTP: http.StatusNotFound, Message: "not found"}
}
func EPermissionDenied(msg string) *Error {
	return &Error{Code: CodePermission, HTTP: http.StatusForbidden, Message: msg}
}
func EUnauthenticated(msg string) *Error {
	return &Error{Code: CodeUnauthenticated, HTTP: http.StatusUnauthorized, Message: msg}
}
func EConflict(msg string) *Error {
	return &Error{Code: CodeConflict, HTTP: http.StatusConflict, Message: msg}
}
func ERateLimited(msg string) *Error {
	return &Error{Code: CodeRateLimited, HTTP: http.StatusTooManyRequests, Message: msg}
}
func EVerifyFailed(msg string) *Error {
	return &Error{Code: CodeVerifyFailed, HTTP: http.StatusUnprocessableEntity, Message: msg}
}
func EURLForbidden(msg string) *Error {
	return &Error{Code: CodeURLForbidden, HTTP: http.StatusUnprocessableEntity, Message: msg}
}
func ERenderFailed(msg string, details any) *Error {
	return &Error{Code: CodeRenderFailed, HTTP: http.StatusUnprocessableEntity, Message: msg, Details: details}
}
func EFilterField(msg string, details any) *Error {
	return &Error{Code: CodeFilterField, HTTP: http.StatusUnprocessableEntity, Message: msg, Details: details}
}
