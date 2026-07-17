package api

import (
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/notification-service/internal/domain"
)

func (s *Server) handleListNotifications(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	limit, cursor := parsePage(r)
	unread := r.URL.Query().Get("filter[unread]") == "true"
	list, err := s.Store.ListNotifications(r.Context(), o.Tenant, o.UserID, unread, limit+1, cursor)
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
		list = []*domain.Notification{}
	}
	writeJSON(w, http.StatusOK, PageEnvelope[*domain.Notification]{Data: list, Page: page})
}

func (s *Server) handleUnreadCount(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	n, err := s.Store.UnreadCount(r.Context(), o.Tenant, o.UserID)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, map[string]int{"unread": n})
}

func (s *Server) handleGetNotification(w http.ResponseWriter, r *http.Request) {
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
	n, err := s.Store.GetNotification(r.Context(), o.Tenant, o.UserID, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, n)
}

func (s *Server) handleMarkRead(w http.ResponseWriter, r *http.Request)   { s.setRead(w, r, true) }
func (s *Server) handleMarkUnread(w http.ResponseWriter, r *http.Request) { s.setRead(w, r, false) }

func (s *Server) setRead(w http.ResponseWriter, r *http.Request, read bool) {
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
	if err := s.Store.SetRead(r.Context(), o.Tenant, o.UserID, id, read); err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleMarkAllRead(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	n, err := s.Store.MarkAllRead(r.Context(), o.Tenant, o.UserID)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, map[string]int64{"marked": n})
}
