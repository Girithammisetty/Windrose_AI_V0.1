package api

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"

	"github.com/windrose-ai/query-service/internal/domain"
	"github.com/windrose-ai/query-service/internal/store"
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

// DataBody wraps single resources.
type DataBody struct {
	Data any `json:"data"`
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeData(w http.ResponseWriter, status int, v any) {
	writeJSON(w, status, DataBody{Data: v})
}

func writeErr(w http.ResponseWriter, r *http.Request, err error) {
	de, ok := domain.AsError(err)
	if !ok {
		slog.Error("internal error", "err", err, "path", r.URL.Path, "trace_id", TraceID(r.Context()))
		de = &domain.Error{Code: domain.CodeInternal, HTTP: http.StatusInternalServerError, Message: "internal error"}
	}
	writeJSON(w, de.HTTP, ErrorBody{Error: ErrorDetail{
		Code: de.Code, Message: de.Message, Details: de.Details, TraceID: TraceID(r.Context()),
	}})
}

// storeErr maps store sentinels onto the error catalog. Cross-tenant and
// nonexistent are indistinguishable by design (MASTER-FR-003: 404, never
// 403, to avoid existence leaks); the caller emits the cross-tenant audit
// event separately.
func storeErr(err error) error {
	switch {
	case errors.Is(err, store.ErrNotFound):
		return domain.ENotFound()
	case errors.Is(err, store.ErrNameConflict):
		return domain.EConflict("name already in use in this workspace")
	case errors.Is(err, store.ErrStaleVersion):
		return domain.EConflict("saved query changed since read (If-Match mismatch)")
	default:
		return err
	}
}

// writeLookupErr maps lookup failures: ErrNotFound → audited 404
// (MASTER-FR-003), everything else through the standard mapping.
func (s *Server) writeLookupErr(w http.ResponseWriter, r *http.Request, err error) {
	if errors.Is(err, store.ErrNotFound) {
		s.notFound(w, r)
		return
	}
	writeErr(w, r, storeErr(err))
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
		writeJSON(w, http.StatusBadRequest, ErrorBody{Error: ErrorDetail{
			Code: domain.CodeValidationFailed, Message: "invalid JSON body: " + err.Error(), TraceID: TraceID(r.Context()),
		}})
		return false
	}
	return true
}
