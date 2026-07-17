package domain

import "net/http"

// Error is a typed domain error carrying a stable machine code (MASTER-FR-024),
// an HTTP status, and optional per-field details.
type Error struct {
	Status  int
	Code    string
	Message string
	Details any
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }

// Stable error codes used by chart-service (superset of master codes).
const (
	CodeValidation   = "VALIDATION_FAILED"
	CodeNotFound     = "NOT_FOUND"
	CodePermission   = "PERMISSION_DENIED"
	CodeUnauthorized = "UNAUTHENTICATED"
	CodeConflict     = "CONFLICT"
	CodeRateLimited  = "RATE_LIMITED"
	CodeInternal     = "INTERNAL"

	CodeInvalidAggregation = "INVALID_AGGREGATION"
	CodeUnknownDimension   = "UNKNOWN_DIMENSION"
	CodeUnknownChartType   = "UNKNOWN_CHART_TYPE"
	CodeSourceBroken       = "SOURCE_BROKEN"
	CodeChartHasCases      = "CHART_HAS_CASES"
	CodeCircularLink       = "CIRCULAR_LINK"
	CodeNoDrilldown        = "NO_DRILLDOWN_CONFIGURED"
	CodeUpstreamQuery      = "UPSTREAM_QUERY_FAILED"
	CodeExportLimit        = "EXPORT_LIMIT"
	CodeUnmappedURN        = "UNMAPPED_URN"
	CodePreconditionFailed = "PRECONDITION_FAILED"
)

// Constructors. Each returns *Error so callers can wrap with details.

func ENotFound(msg string) *Error {
	return &Error{Status: http.StatusNotFound, Code: CodeNotFound, Message: msg}
}
func EValidation(msg string, details ...any) *Error {
	e := &Error{Status: http.StatusUnprocessableEntity, Code: CodeValidation, Message: msg}
	if len(details) > 0 {
		e.Details = details[0]
	}
	return e
}
func EConflict(msg string) *Error {
	return &Error{Status: http.StatusConflict, Code: CodeConflict, Message: msg}
}
func EPermission(msg string) *Error {
	return &Error{Status: http.StatusForbidden, Code: CodePermission, Message: msg}
}
func EUnauthenticated(msg string) *Error {
	return &Error{Status: http.StatusUnauthorized, Code: CodeUnauthorized, Message: msg}
}
func EInternal(msg string) *Error {
	return &Error{Status: http.StatusInternalServerError, Code: CodeInternal, Message: msg}
}
func ERateLimited(msg string) *Error {
	return &Error{Status: http.StatusTooManyRequests, Code: CodeRateLimited, Message: msg}
}
func EChartHasCases(details any) *Error {
	return &Error{Status: http.StatusPreconditionFailed, Code: CodeChartHasCases,
		Message: "chart(s) with allow_cases=true block deletion", Details: details}
}
func ECircularLink(msg string) *Error {
	return &Error{Status: http.StatusConflict, Code: CodeCircularLink, Message: msg}
}
func ENoDrilldown() *Error {
	return &Error{Status: http.StatusNotFound, Code: CodeNoDrilldown, Message: "no drilldown configured for this chart"}
}
func ESourceBroken(msg string) *Error {
	return &Error{Status: http.StatusUnprocessableEntity, Code: CodeSourceBroken, Message: msg}
}
func EUpstream(msg string) *Error {
	return &Error{Status: http.StatusBadGateway, Code: CodeUpstreamQuery, Message: msg}
}

// AsError type-asserts err to *Error, returning nil,false otherwise.
func AsError(err error) (*Error, bool) {
	e, ok := err.(*Error)
	return e, ok
}
