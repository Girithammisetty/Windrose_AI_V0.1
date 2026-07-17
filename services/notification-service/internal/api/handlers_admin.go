package api

import (
	"net/http"
	"time"

	"github.com/windrose-ai/notification-service/internal/domain"
)

func (s *Server) handleStats(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	window := 24 * time.Hour
	if v := r.URL.Query().Get("window"); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			window = d
		}
	}
	stats, err := s.Store.TenantDeliveryStats(r.Context(), o.Tenant, time.Now().Add(-window))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, map[string]any{"window": window.String(), "by_channel": stats})
}

func (s *Server) handleListSuppressions(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	list, err := s.Store.ListSuppressions(r.Context(), o.Tenant, 200)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if list == nil {
		list = []*domain.Suppression{}
	}
	writeData(w, http.StatusOK, list)
}

func (s *Server) handleClearSuppression(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	emailHash := r.URL.Query().Get("email_hash")
	if emailHash == "" {
		writeErr(w, r, domain.EValidation("email_hash query param required", nil))
		return
	}
	if err := s.Store.ClearSuppression(r.Context(), o.Tenant, emailHash); err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
