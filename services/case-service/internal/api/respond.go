package api

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"

	"github.com/windrose-ai/case-service/internal/domain"
	"github.com/windrose-ai/case-service/internal/store"
)

// ErrorBody is the master-BRD error envelope (MASTER-FR-024).
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
		de = mapStoreErr(err)
	}
	if de == nil {
		slog.Error("internal error", "err", err, "path", r.URL.Path, "trace_id", TraceID(r.Context()))
		de = domain.EInternal("internal error")
	}
	writeJSON(w, de.HTTP, ErrorBody{Error: ErrorDetail{
		Code: de.Code, Message: de.Message, Details: de.Details, TraceID: TraceID(r.Context()),
	}})
}

// mapStoreErr maps store sentinels onto the error catalog. Returns nil for
// unknown errors (caller substitutes a 500).
func mapStoreErr(err error) *domain.Error {
	switch {
	case errors.Is(err, store.ErrNotFound):
		return domain.ENotFound()
	case errors.Is(err, store.ErrStaleVersion):
		return domain.EStaleVersion()
	case errors.Is(err, store.ErrCodeConflict):
		return domain.EConflict("code already in use in this workspace")
	case errors.Is(err, store.ErrFieldInUse):
		return domain.EFieldInUse()
	case errors.Is(err, store.ErrCaseLimit):
		return domain.ECaseLimitExceeded()
	case errors.Is(err, store.ErrDedupConflict):
		return domain.EConflict("dedup conflict")
	default:
		return nil
	}
}

// writeLookupErr maps lookup failures: ErrNotFound → audited 404 (AC-13).
func (s *Server) writeLookupErr(w http.ResponseWriter, r *http.Request, err error) {
	if errors.Is(err, store.ErrNotFound) {
		s.notFound(w, r)
		return
	}
	writeErr(w, r, err)
}

// PageEnvelope is the collection envelope (MASTER-FR-022).
type PageEnvelope struct {
	Data []any    `json:"data"`
	Page PageInfo `json:"page"`
	Meta any      `json:"meta,omitempty"`
	Facets any    `json:"facets,omitempty"`
}

type PageInfo struct {
	NextCursor string `json:"next_cursor,omitempty"`
	HasMore    bool   `json:"has_more"`
}

func decodeBody(w http.ResponseWriter, r *http.Request, dst any) bool {
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 2<<20))
	if err := dec.Decode(dst); err != nil {
		writeErr(w, r, domain.EValidation("invalid JSON body: "+err.Error(), nil))
		return false
	}
	return true
}
