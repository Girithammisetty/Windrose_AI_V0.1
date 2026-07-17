package api

import (
	"net/http"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/notification-service/internal/domain"
)

type ruleBody struct {
	Scope          string                `json:"scope"`
	SubjectType    string                `json:"subject_type"`
	SubjectID      string                `json:"subject_id"`
	EventTypes     []string              `json:"event_types"`
	ResourceFilter domain.ResourceFilter `json:"resource_filter"`
	Channels       []string              `json:"channels"`
	DigestEnabled  bool                  `json:"digest_enabled"`
	DigestWindow   string                `json:"digest_window"`
	Active         *bool                 `json:"active"`
}

func (s *Server) handleCreateRule(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	var body ruleBody
	if !decodeBody(w, r, &body) {
		return
	}
	if len(body.EventTypes) == 0 || len(body.Channels) == 0 {
		writeErr(w, r, domain.EValidation("event_types and channels are required", nil))
		return
	}
	if err := s.validateFilter(body.EventTypes, body.ResourceFilter); err != nil {
		writeErr(w, r, err)
		return
	}
	active := true
	if body.Active != nil {
		active = *body.Active
	}
	window := body.DigestWindow
	if window == "" {
		window = "1h"
	}
	now := time.Now().UTC()
	rule := &domain.SubscriptionRule{
		ID: domain.NewID(), TenantID: o.Tenant, Scope: defStr(body.Scope, domain.ScopeUser),
		SubjectType: defStr(body.SubjectType, domain.SubjectUser), SubjectID: defStr(body.SubjectID, o.UserID),
		EventTypes: body.EventTypes, ResourceFtr: body.ResourceFilter, Channels: body.Channels,
		DigestEnabled: body.DigestEnabled, DigestWindow: window, Active: active,
		CreatedBy: o.UserID, CreatedAt: now, UpdatedAt: now,
	}
	if err := s.Store.CreateRule(r.Context(), rule); err != nil {
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusCreated, rule)
}

func (s *Server) handleListRules(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	limit, cursor := parsePage(r)
	list, err := s.Store.ListRules(r.Context(), o.Tenant, limit+1, cursor)
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
		list = []*domain.SubscriptionRule{}
	}
	writeJSON(w, http.StatusOK, PageEnvelope[*domain.SubscriptionRule]{Data: list, Page: page})
}

func (s *Server) handleGetRule(w http.ResponseWriter, r *http.Request) {
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
	rule, err := s.Store.GetRule(r.Context(), o.Tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, rule)
}

func (s *Server) handleUpdateRule(w http.ResponseWriter, r *http.Request) {
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
	rule, err := s.Store.GetRule(r.Context(), o.Tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	var body ruleBody
	if !decodeBody(w, r, &body) {
		return
	}
	if len(body.EventTypes) > 0 {
		rule.EventTypes = body.EventTypes
	}
	if body.Channels != nil {
		rule.Channels = body.Channels
	}
	rule.ResourceFtr = body.ResourceFilter
	rule.DigestEnabled = body.DigestEnabled
	if body.DigestWindow != "" {
		rule.DigestWindow = body.DigestWindow
	}
	if body.Active != nil {
		rule.Active = *body.Active
	}
	if err := s.validateFilter(rule.EventTypes, rule.ResourceFtr); err != nil {
		writeErr(w, r, err)
		return
	}
	if err := s.Store.UpdateRule(r.Context(), rule); err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, rule)
}

func (s *Server) handleDeleteRule(w http.ResponseWriter, r *http.Request) {
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
	if err := s.Store.DeleteRule(r.Context(), o.Tenant, id); err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// validateFilter rejects resource_filter attrs not whitelisted for the rule's
// concrete event types (BR-12); the whitelist is returned in details.
func (s *Server) validateFilter(eventTypes []string, f domain.ResourceFilter) error {
	if len(f.Attrs) == 0 {
		return nil
	}
	for field := range f.Attrs {
		allowedSomewhere := false
		var whitelist []string
		for _, et := range eventTypes {
			if strings.Contains(et, "*") {
				allowedSomewhere = true // wildcard patterns are not attr-validated
				break
			}
			m, ok := s.Registry.Lookup(et)
			if !ok {
				continue
			}
			whitelist = append(whitelist, m.FilterAttrs...)
			if s.Registry.Whitelisted(et, field) {
				allowedSomewhere = true
			}
		}
		if !allowedSomewhere {
			return domain.EFilterField("resource_filter attr not whitelisted: "+field,
				map[string]any{"field": field, "whitelist": whitelist})
		}
	}
	return nil
}

func defStr(v, def string) string {
	if v == "" {
		return def
	}
	return v
}
