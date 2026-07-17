package api

import (
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/windrose-ai/go-common/httpx"

	"github.com/windrose-ai/realtime-hub/internal/authz"
)

// handleTopics adds/removes topics on a live connection without a reconnect
// (RTH-FR-001). Each add is OPA-checked; the subscribe-op rate is capped at 10/s
// per connection (RTH-FR-040). The request must reach the pod holding the
// connection (edge routes by conn_id).
func (s *Server) handleTopics(w http.ResponseWriter, r *http.Request) {
	claims := claimsFrom(r.Context())
	connID := chi.URLParam(r, "conn_id")
	c := s.Hub.ConnByID(connID)
	if c == nil || claims == nil || c.Subject != claims.EffectiveUser() || c.Tenant != claims.TenantID {
		writeErr(w, r, http.StatusNotFound, httpx.CodeNotFound, "connection not found", 0)
		return
	}
	var body struct {
		Add         []string `json:"add"`
		Remove      []string `json:"remove"`
		LastEventID string   `json:"last_event_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeErr(w, r, http.StatusBadRequest, httpx.CodeValidation, "invalid body", 0)
		return
	}
	// Subscribe-op rate limit (RTH-FR-040): ≤10 topic-ops/s/connection.
	ops := len(body.Add) + len(body.Remove)
	if !s.allowTopicOps(r, connID, ops) {
		writeErr(w, r, http.StatusTooManyRequests, httpx.CodeRateLimited, "topic-op rate exceeded", 1)
		return
	}
	id := &connIdentity{Subject: claims.EffectiveUser(), Tenant: claims.TenantID, Typ: claims.Typ, Scopes: claims.Scopes}
	added := []string{}
	for _, raw := range body.Add {
		if s.subscribeOne(r.Context(), c, id, raw, body.LastEventID) {
			added = append(added, raw)
		}
	}
	for _, raw := range body.Remove {
		s.Hub.Unsubscribe(c, claims.TenantID, raw)
	}
	httpx.WriteJSON(w, http.StatusOK, map[string]any{
		"data": map[string]any{"subscribed": added, "unsubscribed": body.Remove},
	})
}

// allowTopicOps enforces the per-connection subscribe-op rate via a Redis
// per-second counter (RTH-FR-040).
func (s *Server) allowTopicOps(r *http.Request, connID string, ops int) bool {
	if ops <= 0 {
		return true
	}
	key := fmt.Sprintf("rt:oprate:%s:%d", connID, time.Now().Unix())
	n, err := s.Redis.R.IncrBy(r.Context(), key, int64(ops)).Result()
	if err != nil {
		return true // fail open on Redis error (availability over strictness here)
	}
	s.Redis.R.Expire(r.Context(), key, 2*time.Second)
	return n <= 10
}

// handleRefreshToken refreshes the JWT for a live SSE connection (RTH-FR-010).
// A different subject/tenant closes the connection (BR-10).
func (s *Server) handleRefreshToken(w http.ResponseWriter, r *http.Request) {
	claims := claimsFrom(r.Context())
	connID := chi.URLParam(r, "conn_id")
	c := s.Hub.ConnByID(connID)
	if c == nil || claims == nil {
		writeErr(w, r, http.StatusNotFound, httpx.CodeNotFound, "connection not found", 0)
		return
	}
	if c.Subject != claims.EffectiveUser() || c.Tenant != claims.TenantID {
		writeErr(w, r, http.StatusUnauthorized, "UNAUTHENTICATED", "subject mismatch", 0)
		return
	}
	if claims.ExpiresAt != nil {
		c.RefreshExp(claims.ExpiresAt.Time)
	}
	httpx.WriteJSON(w, http.StatusOK, map[string]any{"data": map[string]any{"refreshed": true}})
}

// handleAdminList lists connections (RTH-FR-044). Ops-scoped.
func (s *Server) handleAdminList(w http.ResponseWriter, r *http.Request) {
	if !s.adminAllowed(r) {
		writeErr(w, r, http.StatusForbidden, httpx.CodePermission, "admin scope required", 0)
		return
	}
	tenant := r.URL.Query().Get("tenant")
	httpx.WriteJSON(w, http.StatusOK, map[string]any{"data": s.Hub.Connections(tenant)})
}

// handleAdminKill drops a connection (RTH-FR-044 / US-7).
func (s *Server) handleAdminKill(w http.ResponseWriter, r *http.Request) {
	if !s.adminAllowed(r) {
		writeErr(w, r, http.StatusForbidden, httpx.CodePermission, "admin scope required", 0)
		return
	}
	connID := chi.URLParam(r, "conn_id")
	if !s.Hub.KillConnection(connID) {
		writeErr(w, r, http.StatusNotFound, httpx.CodeNotFound, "connection not found", 0)
		return
	}
	if claims := claimsFrom(r.Context()); claims != nil {
		s.Auditor.AdminKill(r.Context(), claims.TenantID, claims.EffectiveUser(), connID)
	}
	httpx.WriteJSON(w, http.StatusOK, map[string]any{"data": map[string]any{"killed": connID}})
}

// adminAllowed gates the ops endpoints on the realtime.connection.admin scope
// or a service principal (RTH-FR-044).
func (s *Server) adminAllowed(r *http.Request) bool {
	claims := claimsFrom(r.Context())
	if claims == nil {
		return false
	}
	return claims.Typ == "service" || claims.HasScope(authz.ActionAdmin)
}
