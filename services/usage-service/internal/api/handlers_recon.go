package api

import (
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/usage-service/internal/domain"
)

func (s *Server) handleListReconciliations(w http.ResponseWriter, r *http.Request) {
	recs, err := s.Store.ListReconciliations(r.Context(), 100)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if recs == nil {
		recs = []domain.Reconciliation{}
	}
	writePage(w, recs, "", false)
}

func (s *Server) handleAckReconciliation(w http.ResponseWriter, r *http.Request) {
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErrCode(w, r, http.StatusNotFound, "NOT_FOUND", "reconciliation not found", nil)
		return
	}
	if err := s.Store.AcknowledgeReconciliation(r.Context(), id); err != nil {
		if err == domain.ErrNotFound {
			writeErrCode(w, r, http.StatusNotFound, "NOT_FOUND", "reconciliation not found or not in variance", nil)
			return
		}
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, map[string]any{"id": id.String(), "status": domain.ReconAcknowledged})
}

type adjustmentBody struct {
	MeterKey      string  `json:"meter_key"`
	Month         string  `json:"month"`
	QuantityDelta float64 `json:"quantity_delta"`
	USDDelta      float64 `json:"usd_delta"`
	Reason        string  `json:"reason"`
}

// handleCreateAdjustment records a signed adjustment on a closed month
// (USG-FR-072). Month must be finalized (BR-5, month_open → conflict).
func (s *Server) handleCreateAdjustment(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	var body adjustmentBody
	if !decodeBody(w, r, &body) {
		return
	}
	if body.Reason == "" {
		writeErrCode(w, r, http.StatusBadRequest, "VALIDATION_FAILED", "reason is required", nil)
		return
	}
	if _, err := time.Parse("2006-01", body.Month); err != nil {
		writeErrCode(w, r, http.StatusBadRequest, "VALIDATION_FAILED", "month must be YYYY-MM", nil)
		return
	}
	actor := op.Actor.ID
	a := domain.Adjustment{
		MeterKey: body.MeterKey, Month: body.Month, QuantityDelta: body.QuantityDelta,
		USDDelta: body.USDDelta, Reason: body.Reason, Actor: actor,
	}
	created, err := s.Store.RecordAdjustment(r.Context(), op, a)
	if err != nil {
		if err == domain.ErrConflict {
			writeErrCode(w, r, http.StatusConflict, "CONFLICT", "month is not finalized", map[string]any{"reason": "month_open"})
			return
		}
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusCreated, map[string]any{
		"id": created.ID.String(), "month": created.Month, "meter_key": created.MeterKey,
		"quantity_delta": created.QuantityDelta, "usd_delta": created.USDDelta, "reason": created.Reason,
	})
}
