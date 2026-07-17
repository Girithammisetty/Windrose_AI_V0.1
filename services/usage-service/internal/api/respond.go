package api

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"

	"github.com/windrose-ai/usage-service/internal/domain"
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

// PageEnvelope is the collection envelope (MASTER-FR-022).
type PageEnvelope struct {
	Data any      `json:"data"`
	Page PageInfo `json:"page"`
}

type PageInfo struct {
	NextCursor string `json:"next_cursor,omitempty"`
	HasMore    bool   `json:"has_more"`
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeData(w http.ResponseWriter, status int, v any) {
	writeJSON(w, status, DataBody{Data: v})
}

func writePage(w http.ResponseWriter, data any, next string, hasMore bool) {
	writeJSON(w, http.StatusOK, PageEnvelope{Data: data, Page: PageInfo{NextCursor: next, HasMore: hasMore}})
}

// codeFor maps a domain error to (httpStatus, code, details).
func codeFor(err error) (int, string, any) {
	var ve *domain.ValidationError
	if errors.As(err, &ve) {
		return http.StatusBadRequest, "VALIDATION_FAILED", ve.Fields
	}
	switch {
	case errors.Is(err, domain.ErrNotFound):
		return http.StatusNotFound, "NOT_FOUND", nil
	case errors.Is(err, domain.ErrValidation):
		return http.StatusBadRequest, "VALIDATION_FAILED", nil
	case errors.Is(err, domain.ErrConflict):
		return http.StatusConflict, "CONFLICT", nil
	case errors.Is(err, domain.ErrPermission):
		return http.StatusForbidden, "PERMISSION_DENIED", nil
	case errors.Is(err, domain.ErrRateLimited):
		return http.StatusTooManyRequests, "RATE_LIMITED", nil
	default:
		return http.StatusInternalServerError, "INTERNAL", nil
	}
}

func writeErr(w http.ResponseWriter, r *http.Request, err error) {
	status, code, details := codeFor(err)
	if status >= 500 {
		slog.Error("internal error", "err", err, "path", r.URL.Path, "trace_id", TraceID(r.Context()))
		err = errors.New("internal error")
	}
	writeJSON(w, status, ErrorBody{Error: ErrorDetail{
		Code: code, Message: err.Error(), Details: details, TraceID: TraceID(r.Context()),
	}})
}

func writeErrCode(w http.ResponseWriter, r *http.Request, status int, code, msg string, details any) {
	writeJSON(w, status, ErrorBody{Error: ErrorDetail{Code: code, Message: msg, Details: details, TraceID: TraceID(r.Context())}})
}

func decodeBody(w http.ResponseWriter, r *http.Request, dst any) bool {
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20))
	if err := dec.Decode(dst); err != nil {
		writeErrCode(w, r, http.StatusBadRequest, "VALIDATION_FAILED", "invalid JSON body: "+err.Error(), nil)
		return false
	}
	return true
}
