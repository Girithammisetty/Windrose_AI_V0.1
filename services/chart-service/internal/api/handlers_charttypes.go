package api

import (
	"net/http"
	"strings"

	"github.com/windrose-ai/chart-service/internal/domain"
)

// handleChartTypes serves the 30-type catalog + per-type JSON Schemas
// (CHART-FR-012 / AC-7).
func (s *Server) handleChartTypes(w http.ResponseWriter, r *http.Request) {
	writeData(w, http.StatusOK, domain.Catalog())
}

// bearerToken returns the raw bearer token to forward to upstream services.
func bearerToken(r *http.Request) string {
	h := r.Header.Get("Authorization")
	if strings.HasPrefix(h, "Bearer ") {
		return strings.TrimSpace(h[len("Bearer "):])
	}
	return ""
}
