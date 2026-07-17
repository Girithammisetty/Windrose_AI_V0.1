package api

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/notification-service/internal/channels/webhook"
	"github.com/windrose-ai/notification-service/internal/domain"
)

type webhookBody struct {
	URL        string   `json:"url"`
	EventTypes []string `json:"event_types"`
	Active     *bool    `json:"active"`
}

func newSecret() string {
	b := make([]byte, 32)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func (s *Server) handleCreateWebhook(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	var body webhookBody
	if !decodeBody(w, r, &body) {
		return
	}
	if len(body.EventTypes) == 0 {
		writeErr(w, r, domain.EValidation("event_types required", nil))
		return
	}
	// SSRF guard at registration (BR-6, AC-12).
	if _, err := webhook.GuardURL(body.URL, s.WebhookSender.AllowHTTP); err != nil {
		writeErr(w, r, domain.EURLForbidden(err.Error()))
		return
	}
	// Verification handshake (NOTIF-FR-022): endpoint must echo the challenge.
	challenge := uuid.NewString()
	vctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	if err := s.WebhookSender.VerifyChallenge(vctx, body.URL, challenge); err != nil {
		writeErr(w, r, domain.EVerifyFailed("challenge verification failed: "+err.Error()))
		return
	}
	now := time.Now().UTC()
	active := true
	if body.Active != nil {
		active = *body.Active
	}
	ep := &domain.WebhookEndpoint{
		ID: domain.NewID(), TenantID: o.Tenant, URL: body.URL, EventTypes: body.EventTypes,
		Secrets: []domain.WebhookSecret{{Version: 1, Secret: newSecret(), CreatedAt: now}},
		Active:  active, VerifiedAt: &now, CircuitState: domain.CircuitClosed,
		CreatedBy: o.UserID, CreatedAt: now, UpdatedAt: now,
	}
	if err := s.Store.CreateWebhook(r.Context(), ep); err != nil {
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusCreated, ep)
}

func (s *Server) handleListWebhooks(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	limit, cursor := parsePage(r)
	list, err := s.Store.ListWebhooks(r.Context(), o.Tenant, limit+1, cursor)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	page := PageInfo{}
	if len(list) > limit {
		list = list[:limit]
		page = PageInfo{NextCursor: list[len(list)-1].ID.String(), HasMore: true}
	}
	if list == nil {
		list = []*domain.WebhookEndpoint{}
	}
	writeJSON(w, http.StatusOK, PageEnvelope[*domain.WebhookEndpoint]{Data: list, Page: page})
}

func (s *Server) handleGetWebhook(w http.ResponseWriter, r *http.Request) {
	o, ep, ok := s.loadWebhook(w, r)
	if !ok {
		return
	}
	_ = o
	writeData(w, http.StatusOK, ep)
}

func (s *Server) handleUpdateWebhook(w http.ResponseWriter, r *http.Request) {
	o, ep, ok := s.loadWebhook(w, r)
	if !ok {
		return
	}
	_ = o
	var body webhookBody
	if !decodeBody(w, r, &body) {
		return
	}
	if body.URL != "" && body.URL != ep.URL {
		if _, err := webhook.GuardURL(body.URL, s.WebhookSender.AllowHTTP); err != nil {
			writeErr(w, r, domain.EURLForbidden(err.Error()))
			return
		}
		ep.URL = body.URL
	}
	if len(body.EventTypes) > 0 {
		ep.EventTypes = body.EventTypes
	}
	if body.Active != nil {
		ep.Active = *body.Active
	}
	if err := s.Store.UpdateWebhook(r.Context(), ep); err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, ep)
}

func (s *Server) handleDeleteWebhook(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		s.notFound(w, r)
		return
	}
	if err := s.Store.DeleteWebhook(r.Context(), o.Tenant, id); err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// handleRotateSecret adds a new secret version and expires the prior one in 24h,
// so both validate during the overlap (NOTIF-FR-022, AC-6).
func (s *Server) handleRotateSecret(w http.ResponseWriter, r *http.Request) {
	o, ep, ok := s.loadWebhook(w, r)
	if !ok {
		return
	}
	_ = o
	now := time.Now().UTC()
	expiry := now.Add(24 * time.Hour)
	maxVer := 0
	for i := range ep.Secrets {
		if ep.Secrets[i].ExpiresAt == nil {
			ep.Secrets[i].ExpiresAt = &expiry
		}
		if ep.Secrets[i].Version > maxVer {
			maxVer = ep.Secrets[i].Version
		}
	}
	ep.Secrets = append(ep.Secrets, domain.WebhookSecret{Version: maxVer + 1, Secret: newSecret(), CreatedAt: now})
	if err := s.Store.UpdateWebhook(r.Context(), ep); err != nil {
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, ep)
}

func (s *Server) handleListDeliveries(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		s.notFound(w, r)
		return
	}
	if _, err := s.Store.GetWebhook(r.Context(), o.Tenant, id); err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	limit, cursor := parsePage(r)
	list, err := s.Store.ListDeliveriesForEndpoint(r.Context(), o.Tenant, id, limit+1, cursor)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	page := PageInfo{}
	if len(list) > limit {
		list = list[:limit]
		page = PageInfo{NextCursor: list[len(list)-1].ID.String(), HasMore: true}
	}
	if list == nil {
		list = []*domain.Delivery{}
	}
	writeJSON(w, http.StatusOK, PageEnvelope[*domain.Delivery]{Data: list, Page: page})
}

// handleRedeliver requeues a webhook delivery for immediate re-send
// (NOTIF-FR-024).
func (s *Server) handleRedeliver(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	epID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		s.notFound(w, r)
		return
	}
	did, err := uuid.Parse(chi.URLParam(r, "did"))
	if err != nil {
		s.notFound(w, r)
		return
	}
	if _, err := s.Store.GetWebhook(r.Context(), o.Tenant, epID); err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	if err := s.Store.RequeueWebhookDelivery(r.Context(), o.Tenant, did); err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	writeData(w, http.StatusAccepted, map[string]string{"status": "requeued"})
}

func (s *Server) loadWebhook(w http.ResponseWriter, r *http.Request) (domain.Op, *domain.WebhookEndpoint, bool) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return o, nil, false
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		s.notFound(w, r)
		return o, nil, false
	}
	ep, err := s.Store.GetWebhook(r.Context(), o.Tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return o, nil, false
	}
	return o, ep, true
}
