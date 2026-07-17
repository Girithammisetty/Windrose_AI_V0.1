package api

import (
	"net/http"

	"github.com/windrose-ai/notification-service/internal/domain"
)

func (s *Server) handleGetPreferences(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	p, err := s.Store.GetPreferences(r.Context(), o.Tenant, o.UserID)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, p)
}

type prefsBody struct {
	ChannelOverrides map[string][]string `json:"channel_overrides"`
	Mutes            domain.Mutes        `json:"mutes"`
	QuietHours       *domain.QuietHours  `json:"quiet_hours"`
	DigestConfig     map[string]string   `json:"digest_config"`
}

func (s *Server) handlePutPreferences(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	var body prefsBody
	if !decodeBody(w, r, &body) {
		return
	}
	if body.ChannelOverrides == nil {
		body.ChannelOverrides = map[string][]string{}
	}
	if body.DigestConfig == nil {
		body.DigestConfig = map[string]string{}
	}
	p := &domain.UserPreferences{
		TenantID: o.Tenant, UserID: o.UserID, ChannelOverride: body.ChannelOverrides,
		Mutes: body.Mutes, QuietHours: body.QuietHours, DigestConfig: body.DigestConfig,
	}
	if err := s.Store.PutPreferences(r.Context(), p); err != nil {
		writeErr(w, r, err)
		return
	}
	out, _ := s.Store.GetPreferences(r.Context(), o.Tenant, o.UserID)
	writeData(w, http.StatusOK, out)
}
