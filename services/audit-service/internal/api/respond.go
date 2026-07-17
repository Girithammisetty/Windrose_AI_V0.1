package api

import (
	"encoding/json"
	"net/http"

	"github.com/windrose-ai/audit-service/internal/domain"
)

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// writeErr renders the master error envelope (MASTER-FR-024).
func writeErr(w http.ResponseWriter, r *http.Request, err *domain.Error) {
	writeJSON(w, err.HTTP, map[string]any{
		"error": map[string]any{
			"code":     err.Code,
			"message":  err.Message,
			"details":  err.Details,
			"trace_id": TraceID(r.Context()),
		},
	})
}
