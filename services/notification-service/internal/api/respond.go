// Package api is notification-service's HTTP layer: chi router, JWT/tenant
// middleware (shared go-common authjwt), OPA authorization per route, the
// master-BRD error envelope + cursor pagination, and the resource handlers.
package api

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"

	"github.com/windrose-ai/notification-service/internal/domain"
	"github.com/windrose-ai/notification-service/internal/store"
)

// ErrorBody is the master error envelope (MASTER-FR-024).
type ErrorBody struct {
	Error ErrorDetail `json:"error"`
}

// ErrorDetail is the inner error object.
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

// PageInfo is the collection page envelope (MASTER-FR-022).
type PageInfo struct {
	NextCursor string `json:"next_cursor,omitempty"`
	HasMore    bool   `json:"has_more"`
}

// PageEnvelope wraps a page of resources.
type PageEnvelope[T any] struct {
	Data []T      `json:"data"`
	Page PageInfo `json:"page"`
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeData(w http.ResponseWriter, status int, v any) { writeJSON(w, status, DataBody{Data: v}) }

func writeErr(w http.ResponseWriter, r *http.Request, err error) {
	de, ok := domain.AsError(err)
	if !ok {
		// Map store sentinels, else 500.
		switch {
		case errors.Is(err, store.ErrNotFound):
			de = domain.ENotFound()
		case errors.Is(err, store.ErrConflict):
			de = domain.EConflict("already exists")
		default:
			slog.Error("internal error", "err", err, "path", r.URL.Path, "trace_id", traceID(r.Context()))
			de = &domain.Error{Code: domain.CodeInternal, HTTP: http.StatusInternalServerError, Message: "internal error"}
		}
	}
	writeJSON(w, de.HTTP, ErrorBody{Error: ErrorDetail{Code: de.Code, Message: de.Message, Details: de.Details, TraceID: traceID(r.Context())}})
}

// writeLookupErr maps ErrNotFound to an audited 404 (MASTER-FR-003), else the
// standard mapping.
func (s *Server) writeLookupErr(w http.ResponseWriter, r *http.Request, err error) {
	if errors.Is(err, store.ErrNotFound) {
		s.notFound(w, r)
		return
	}
	writeErr(w, r, err)
}

func decodeBody(w http.ResponseWriter, r *http.Request, dst any) bool {
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20))
	if err := dec.Decode(dst); err != nil {
		writeErr(w, r, domain.EValidation("invalid JSON body: "+err.Error(), nil))
		return false
	}
	return true
}
