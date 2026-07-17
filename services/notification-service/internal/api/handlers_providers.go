package api

import (
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"

	"github.com/windrose-ai/notification-service/internal/domain"
)

// handleProviderStatus ingests an email provider's delivered/bounce/complaint
// callback (NOTIF-FR-021, AC-10). It is authenticated by the provider's own
// signed payload (allowlisted per provider, BR-13), not a Windrose JWT. Hard
// bounces/complaints add the address to the suppression list.
func (s *Server) handleProviderStatus(w http.ResponseWriter, r *http.Request) {
	name := chi.URLParam(r, "provider")
	provider, ok := s.EmailProviders[name]
	if !ok {
		writeErr(w, r, domain.ENotFound())
		return
	}
	updates, err := provider.ParseStatusCallback(r)
	if err != nil {
		writeErr(w, r, domain.EValidation("invalid provider callback: "+err.Error(), nil))
		return
	}
	applied := 0
	for _, u := range updates {
		tenant, found, err := s.Store.FindDeliveryTenantByProviderMsgID(r.Context(), u.ProviderMsgID)
		if err != nil || !found {
			continue
		}
		if _, err := s.Store.UpdateDeliveryStatusByProviderMsgID(r.Context(), tenant, u.ProviderMsgID, u.Status); err != nil {
			continue
		}
		if u.Hard && u.Email != "" {
			reason := "bounce"
			if u.Status == "complained" {
				reason = "complaint"
			}
			_ = s.Store.AddSuppression(r.Context(), tenant, emailHash(u.Email), reason)
		}
		applied++
	}
	writeData(w, http.StatusOK, map[string]int{"applied": applied})
}

func emailHash(addr string) string {
	sum := sha256.Sum256([]byte(strings.ToLower(addr)))
	return hex.EncodeToString(sum[:])
}
