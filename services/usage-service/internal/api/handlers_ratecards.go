package api

import (
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/usage-service/internal/domain"
)

type createRateCardBody struct {
	TenantID      *string            `json:"tenant_id,omitempty"`
	Version       int                `json:"version"`
	EffectiveFrom string             `json:"effective_from"`
	Items         map[string]float64 `json:"items"`
}

// handleCreateRateCard creates a draft rate card (USG-FR-042, platform only).
func (s *Server) handleCreateRateCard(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	var body createRateCardBody
	if !decodeBody(w, r, &body) {
		return
	}
	eff, err := time.Parse("2006-01-02", body.EffectiveFrom)
	if err != nil {
		writeErrCode(w, r, http.StatusBadRequest, "VALIDATION_FAILED", "effective_from must be YYYY-MM-DD", nil)
		return
	}
	rc := domain.RateCard{Version: body.Version, EffectiveFrom: eff, Items: body.Items}
	if body.TenantID != nil {
		id, err := uuid.Parse(*body.TenantID)
		if err != nil {
			writeErrCode(w, r, http.StatusBadRequest, "VALIDATION_FAILED", "tenant_id invalid", nil)
			return
		}
		rc.TenantID = &id
	}
	created, err := s.Store.CreateRateCard(r.Context(), op, rc)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusCreated, rateCardView(created))
}

// handleActivateRateCard activates a draft card (USG-FR-042).
func (s *Server) handleActivateRateCard(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErrCode(w, r, http.StatusNotFound, "NOT_FOUND", "rate card not found", nil)
		return
	}
	rc, err := s.Store.ActivateRateCard(r.Context(), op, id)
	if err != nil {
		if err == domain.ErrConflict {
			writeErrCode(w, r, http.StatusConflict, "CONFLICT", "cannot activate a superseded card", nil)
			return
		}
		if err == domain.ErrNotFound {
			writeErrCode(w, r, http.StatusNotFound, "NOT_FOUND", "rate card not found", nil)
			return
		}
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, rateCardView(rc))
}

func (s *Server) handleListRateCards(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	cards, err := s.Store.ListRateCards(r.Context(), op)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	views := make([]map[string]any, len(cards))
	for i, c := range cards {
		views[i] = rateCardView(c)
	}
	writePage(w, views, "", false)
}

func rateCardView(rc domain.RateCard) map[string]any {
	v := map[string]any{
		"id": rc.ID.String(), "version": rc.Version,
		"effective_from": rc.EffectiveFrom.Format("2006-01-02"),
		"status":         rc.Status, "items": rc.Items,
	}
	if rc.TenantID != nil {
		v["tenant_id"] = rc.TenantID.String()
	}
	return v
}
