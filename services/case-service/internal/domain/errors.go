package domain

import (
	"errors"
	"net/http"
)

// Stable machine-readable error codes (MASTER-FR-024 + BRD 08 §5).
const (
	CodeValidationFailed    = "VALIDATION_FAILED"
	CodeNotFound            = "NOT_FOUND"
	CodePermissionDenied    = "PERMISSION_DENIED"
	CodeUnauthenticated     = "UNAUTHENTICATED"
	CodeConflict            = "CONFLICT"
	CodeRateLimited         = "RATE_LIMITED"
	CodeInternal            = "INTERNAL"
	CodeInvalidTransition   = "INVALID_TRANSITION"
	CodeDispositionRequired = "DISPOSITION_REQUIRED"
	CodeDispositionNote     = "DISPOSITION_NOTE_REQUIRED"
	CodeBatchTooLarge       = "BATCH_TOO_LARGE"
	CodeProposalFieldDenied = "PROPOSAL_FIELD_NOT_ALLOWED"
	CodeSearchUnavailable   = "SEARCH_UNAVAILABLE"
	CodeRowFetchFailed      = "ROW_FETCH_FAILED"
	CodeFieldInUse          = "FIELD_IN_USE"
	CodeCaseLimitExceeded   = "CASE_LIMIT_EXCEEDED"
	CodeStaleVersion        = "CONFLICT"
)

// Error is a coded application error carrying its HTTP status (MASTER-FR-024).
type Error struct {
	Code    string
	HTTP    int
	Message string
	Details any
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }

// AsError extracts a *Error from err, if present.
func AsError(err error) (*Error, bool) {
	var e *Error
	if errors.As(err, &e) {
		return e, true
	}
	return nil, false
}

// Constructors -----------------------------------------------------------------

func EValidation(msg string, details any) *Error {
	return &Error{Code: CodeValidationFailed, HTTP: http.StatusUnprocessableEntity, Message: msg, Details: details}
}
func ENotFound() *Error {
	return &Error{Code: CodeNotFound, HTTP: http.StatusNotFound, Message: "not found"}
}
func EUnauthenticated(msg string) *Error {
	return &Error{Code: CodeUnauthenticated, HTTP: http.StatusUnauthorized, Message: msg}
}
func EPermissionDenied(msg string) *Error {
	return &Error{Code: CodePermissionDenied, HTTP: http.StatusForbidden, Message: msg}
}
func EConflict(msg string) *Error {
	return &Error{Code: CodeConflict, HTTP: http.StatusConflict, Message: msg}
}
func EInvalidTransition(msg string) *Error {
	return &Error{Code: CodeInvalidTransition, HTTP: http.StatusConflict, Message: msg}
}
func EDispositionRequired() *Error {
	return &Error{Code: CodeDispositionRequired, HTTP: http.StatusUnprocessableEntity, Message: "an active disposition is required to resolve"}
}
func EDispositionNote() *Error {
	return &Error{Code: CodeDispositionNote, HTTP: http.StatusUnprocessableEntity, Message: "this disposition requires a resolution note"}
}
func EBatchTooLarge(msg string) *Error {
	return &Error{Code: CodeBatchTooLarge, HTTP: http.StatusUnprocessableEntity, Message: msg}
}
func EProposalFieldDenied(field string) *Error {
	return &Error{Code: CodeProposalFieldDenied, HTTP: http.StatusUnprocessableEntity, Message: "field not allowed in proposal: " + field}
}
func ESearchUnavailable() *Error {
	return &Error{Code: CodeSearchUnavailable, HTTP: http.StatusServiceUnavailable, Message: "search projection unavailable; try again shortly"}
}
func EFieldInUse() *Error {
	return &Error{Code: CodeFieldInUse, HTTP: http.StatusConflict, Message: "custom field has values on open cases; pass ?orphan=true to force"}
}
func ECaseLimitExceeded() *Error {
	return &Error{Code: CodeCaseLimitExceeded, HTTP: http.StatusUnprocessableEntity, Message: "workspace open-case limit reached"}
}
func EStaleVersion() *Error {
	return &Error{Code: CodeConflict, HTTP: http.StatusConflict, Message: "case changed since read (If-Match mismatch)"}
}
func EInternal(msg string) *Error {
	return &Error{Code: CodeInternal, HTTP: http.StatusInternalServerError, Message: msg}
}
