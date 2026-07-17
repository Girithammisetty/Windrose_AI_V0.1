package api

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/go-common/httpx"
	"github.com/windrose-ai/realtime-hub/internal/events"
	"github.com/windrose-ai/realtime-hub/internal/fanout"
	"github.com/windrose-ai/realtime-hub/internal/topics"
)

// ScopePublish is the token scope a producer must hold to publish internally
// (RTH-FR-021). Only trusted platform services/agents are issued it.
const ScopePublish = "realtime.publish"

// authenticatePublisher verifies the caller is a trusted platform service or
// agent principal authorized to publish. It returns false (and writes 401) for
// unauthenticated or unauthorized callers, closing the cross-tenant
// event-forgery hole. This is app-layer defense in depth on top of the mesh
// mTLS that fronts the internal listener.
func (s *Server) authenticatePublisher(w http.ResponseWriter, r *http.Request) bool {
	raw := r.Header.Get("Authorization")
	if !strings.HasPrefix(raw, "Bearer ") {
		httpx.WriteJSON(w, http.StatusUnauthorized, publishAck("", false, "UNAUTHENTICATED: producer token required"))
		return false
	}
	claims, err := s.Verifier.Verify(r.Context(), strings.TrimPrefix(raw, "Bearer "))
	if err != nil {
		httpx.WriteJSON(w, http.StatusUnauthorized, publishAck("", false, "UNAUTHENTICATED: invalid token"))
		return false
	}
	// Only service or agent principals may publish, and only with the
	// realtime.publish scope. A user token can never publish (it can only
	// subscribe), preventing a browser from forging events into any tenant.
	isPrincipal := claims.Typ == "service" || strings.HasPrefix(claims.Typ, "agent")
	if !isPrincipal || !claims.HasScope(ScopePublish) {
		httpx.WriteJSON(w, http.StatusUnauthorized, publishAck("", false, "PERMISSION_DENIED: producer not authorized"))
		return false
	}
	return true
}

// publishRequest mirrors the gRPC PublishRequest (§5). ttl_seconds=0 is
// ephemeral (skips the replay buffer, BR-13); the default for chat is 600s.
type publishRequest struct {
	TenantID    string          `json:"tenant_id"`
	Topic       string          `json:"topic"`
	EventID     string          `json:"event_id"`
	PayloadJSON json.RawMessage `json:"payload_json"`
	TTLSeconds  uint32          `json:"ttl_seconds"`
}

// handleInternalPublish is the internal producer API (RTH-FR-021): latency-
// critical streams (agent-runtime chat tokens) publish here and the event fans
// out to authorized subscribers on any pod via real Redis pub/sub (AC-8). It is
// idempotent by event_id (AC-16) and enforces the 64KB payload cap (RTH-FR-022).
//
// Transport note: the BRD sketches gRPC over SPIFFE mTLS; this implementation
// uses HTTP+JSON on a SEPARATE internal listener (api.Server.InternalRouter,
// bound to INTERNAL_LISTEN_ADDR) with identical delivery semantics. mesh mTLS
// fronts that listener at the network layer; independently, every publish is
// authenticated at the app layer (authenticatePublisher) so an unauthenticated
// or user-token caller cannot forge events into any tenant.
func (s *Server) handleInternalPublish(w http.ResponseWriter, r *http.Request) {
	if !s.authenticatePublisher(w, r) {
		return
	}
	var req publishRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httpx.WriteJSON(w, http.StatusBadRequest, publishAck("", false, "INVALID_ARGUMENT: bad body"))
		return
	}
	if req.TenantID == "" || req.Topic == "" {
		httpx.WriteJSON(w, http.StatusBadRequest, publishAck(req.EventID, false, "INVALID_ARGUMENT: tenant_id and topic required"))
		return
	}
	if _, err := topics.Parse(req.Topic); err != nil {
		httpx.WriteJSON(w, http.StatusBadRequest, publishAck(req.EventID, false, "INVALID_ARGUMENT: INVALID_TOPIC"))
		return
	}
	if len(req.PayloadJSON) > events.PayloadCap {
		httpx.WriteJSON(w, http.StatusRequestEntityTooLarge, publishAck(req.EventID, false, "RESOURCE_EXHAUSTED: payload > 64KB"))
		return
	}
	eventID := req.EventID
	if eventID == "" {
		eventID = uuid.NewString()
	}
	t, _ := topics.Parse(req.Topic)
	ev := fanout.Event{ID: eventID, Topic: req.Topic, Data: req.PayloadJSON, Chat: t.IsChat()}
	ttl := time.Duration(req.TTLSeconds) * time.Second
	if req.TTLSeconds == 0 && t.IsChat() {
		ttl = 10 * time.Minute // chat default (RTH-FR-034 / BR-13)
	}
	if err := s.Hub.IngestInternal(r.Context(), req.TenantID, req.Topic, ev, ttl); err != nil {
		httpx.WriteJSON(w, http.StatusServiceUnavailable, publishAck(eventID, false, "unavailable: "+err.Error()))
		return
	}
	httpx.WriteJSON(w, http.StatusOK, publishAck(eventID, true, ""))
}

func publishAck(eventID string, accepted bool, reason string) map[string]any {
	return map[string]any{"event_id": eventID, "accepted": accepted, "reason": reason}
}
