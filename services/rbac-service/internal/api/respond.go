package api

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"

	"github.com/windrose-ai/rbac-service/internal/store"
)

// ErrorBody is the master-BRD error envelope (MASTER-FR-024):
// {error: {code, message, details?, trace_id}}.
type ErrorBody struct {
	Error ErrorDetail `json:"error"`
}

type ErrorDetail struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	Details any    `json:"details,omitempty"`
	TraceID string `json:"trace_id"`
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, r *http.Request, status int, code, message string, details any) {
	writeJSON(w, status, ErrorBody{Error: ErrorDetail{
		Code: code, Message: message, Details: details, TraceID: TraceID(r.Context()),
	}})
}

// writeStoreError maps store error types to HTTP semantics.
func writeStoreError(w http.ResponseWriter, r *http.Request, err error) {
	var ce *store.ConflictError
	if errors.As(err, &ce) {
		writeError(w, r, http.StatusConflict, ce.Code, ce.Message, nil)
		return
	}
	var ve *store.ValidationError
	if errors.As(err, &ve) {
		status := http.StatusUnprocessableEntity
		if ve.Code == store.CodeValidationFailed {
			status = http.StatusBadRequest
		}
		writeError(w, r, status, ve.Code, ve.Message, ve.Details)
		return
	}
	if errors.Is(err, store.ErrNotFound) {
		// Cross-tenant and nonexistent are indistinguishable by design
		// (MASTER-FR-003: 404, never 403, to avoid existence leaks).
		writeError(w, r, http.StatusNotFound, "NOT_FOUND", "resource not found", nil)
		return
	}
	slog.Error("internal error", "err", err, "path", r.URL.Path, "trace_id", TraceID(r.Context()))
	writeError(w, r, http.StatusInternalServerError, "INTERNAL", "internal error", nil)
}

// PageEnvelope is the collection envelope (MASTER-FR-022).
type PageEnvelope[T any] struct {
	Data []T      `json:"data"`
	Page PageInfo `json:"page"`
}

type PageInfo struct {
	NextCursor string `json:"next_cursor,omitempty"`
	HasMore    bool   `json:"has_more"`
}

func writePage[T any](w http.ResponseWriter, p store.Page[T]) {
	if p.Data == nil {
		p.Data = []T{}
	}
	writeJSON(w, http.StatusOK, PageEnvelope[T]{Data: p.Data, Page: PageInfo{NextCursor: p.NextCursor, HasMore: p.HasMore}})
}

func decodeBody(w http.ResponseWriter, r *http.Request, dst any) bool {
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20))
	if err := dec.Decode(dst); err != nil {
		writeError(w, r, http.StatusBadRequest, "VALIDATION_FAILED", "invalid JSON body: "+err.Error(), nil)
		return false
	}
	return true
}
