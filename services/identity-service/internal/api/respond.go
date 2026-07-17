package api

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"

	"github.com/windrose-ai/identity-service/internal/domain"
)

type ctxKey int

const (
	ctxClaims ctxKey = iota
	ctxTraceID
	ctxSpiffeID
)

// TraceIDFrom returns the request trace id (MASTER-FR-028).
func TraceIDFrom(ctx context.Context) string {
	if v, ok := ctx.Value(ctxTraceID).(string); ok {
		return v
	}
	return ""
}

// ClaimsFrom returns the verified JWT claims, if authenticated.
func ClaimsFrom(ctx context.Context) *domain.Claims {
	if v, ok := ctx.Value(ctxClaims).(*domain.Claims); ok {
		return v
	}
	return nil
}

// errorBody is the master error envelope (MASTER-FR-024).
type errorBody struct {
	Error errorInner `json:"error"`
}

type errorInner struct {
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

func writeErr(w http.ResponseWriter, r *http.Request, err error) {
	de, ok := domain.AsError(err)
	if !ok {
		de = &domain.Error{Code: "INTERNAL", HTTP: http.StatusInternalServerError, Message: "internal error"}
	}
	if de.RetryAfterSeconds > 0 {
		w.Header().Set("Retry-After", strconv.Itoa(de.RetryAfterSeconds)) // AC-14
	}
	writeJSON(w, de.HTTP, errorBody{Error: errorInner{
		Code: de.Code, Message: de.Message, Details: de.Details, TraceID: TraceIDFrom(r.Context()),
	}})
}

// collection is the pagination envelope (MASTER-FR-022).
type collection[T any] struct {
	Data []T             `json:"data"`
	Page domain.PageInfo `json:"page"`
}

func writePage[T any](w http.ResponseWriter, items []T, info domain.PageInfo) {
	if items == nil {
		items = []T{}
	}
	writeJSON(w, http.StatusOK, collection[T]{Data: items, Page: info})
}

func decodeBody(r *http.Request, v any) error {
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		return domain.EValidation("invalid JSON body: " + err.Error())
	}
	return nil
}
