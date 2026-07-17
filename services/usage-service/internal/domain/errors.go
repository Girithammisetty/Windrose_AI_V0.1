package domain

import "errors"

// Domain sentinel errors mapped to stable API error codes (MASTER-FR-024).
var (
	ErrNotFound      = errors.New("not_found")
	ErrValidation    = errors.New("validation_failed")
	ErrConflict      = errors.New("conflict")
	ErrPermission    = errors.New("permission_denied")
	ErrRateLimited   = errors.New("rate_limited")
	ErrCrossTenant   = errors.New("cross_tenant")
)

// FieldError is one per-field validation problem (MASTER-FR-024 details).
type FieldError struct {
	Field   string `json:"field"`
	Message string `json:"message"`
}

// ValidationError carries per-field details.
type ValidationError struct {
	Fields []FieldError
}

func (e *ValidationError) Error() string { return "validation_failed" }

// Is lets errors.Is(err, ErrValidation) match.
func (e *ValidationError) Is(target error) bool { return target == ErrValidation }
