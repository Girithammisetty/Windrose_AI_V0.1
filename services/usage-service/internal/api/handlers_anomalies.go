package api

import (
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/usage-service/internal/domain"
)

func (s *Server) handleListAnomalies(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	status := r.URL.Query().Get("status")
	anoms, err := s.Store.ListAnomalies(r.Context(), op.Tenant, status, 100)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if anoms == nil {
		anoms = []domain.Anomaly{}
	}
	writePage(w, anoms, "", false)
}

func (s *Server) handleDismissAnomaly(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErrCode(w, r, http.StatusNotFound, "NOT_FOUND", "anomaly not found", nil)
		return
	}
	by := ""
	if c := ClaimsFrom(r.Context()); c != nil {
		by = c.EffectiveUser()
	}
	if err := s.Store.DismissAnomaly(r.Context(), op, id, by); err != nil {
		if err == domain.ErrNotFound {
			s.auditCrossTenant(r, domain.AnomalyURN(op.Tenant, id))
			writeErrCode(w, r, http.StatusNotFound, "NOT_FOUND", "anomaly not found", nil)
			return
		}
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, map[string]any{"id": id.String(), "status": domain.AnomalyDismissed})
}
