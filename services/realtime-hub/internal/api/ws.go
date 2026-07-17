package api

import (
	"encoding/json"
	"net/http"
	"sync"

	"github.com/google/uuid"
	"github.com/gorilla/websocket"

	"github.com/windrose-ai/realtime-hub/internal/fanout"
)

var upgrader = websocket.Upgrader{
	ReadBufferSize:  4096,
	WriteBufferSize: 4096,
	// The edge LB terminates TLS and enforces origin; the hub itself accepts
	// any origin (browsers connect through the same-origin edge).
	CheckOrigin: func(r *http.Request) bool { return true },
}

// wsFrame is one client→server frame (RTH-FR-002).
type wsFrame struct {
	Type        string   `json:"type"` // subscribe|unsubscribe|ping|refresh_token
	Topics      []string `json:"topics,omitempty"`
	LastEventID string   `json:"last_event_id,omitempty"`
	Token       string   `json:"token,omitempty"`
}

// handleWS is the secondary transport (RTH-FR-002): GET /api/v1/ws. It shares
// auth, caps, and delivery semantics with SSE, and adds a client→server frame
// channel (subscribe/unsubscribe/ping/refresh_token).
func (s *Server) handleWS(w http.ResponseWriter, r *http.Request) {
	id, err := s.authenticateConnect(r)
	if err != nil {
		writeErr(w, r, http.StatusUnauthorized, "UNAUTHENTICATED", "invalid ticket or token", 0)
		return
	}
	if !s.reserveConn(w, r, id) {
		return
	}
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		// Upgrade already wrote a response; release the reserved slot.
		s.Caps.Release(r.Context(), id.Tenant, id.Subject)
		return
	}
	defer conn.Close()

	connID := uuid.NewString()
	sink := &wsSink{conn: conn}
	c := s.Hub.AddConn(connID, id.Subject, id.Tenant, id.Typ, id.Scopes, "ws", id.IPHash, sink, id.Exp)
	sink.sendJSON(map[string]any{"type": "connected", "conn_id": connID})

	// Initial topics from the query string (optional for WS).
	for _, raw := range id.Topics {
		s.subscribeOne(r.Context(), c, id, raw, r.Header.Get("Last-Event-ID"))
	}

	// Reader loop (this goroutine). The writer goroutine owns all sends.
	for {
		var f wsFrame
		if err := conn.ReadJSON(&f); err != nil {
			break
		}
		switch f.Type {
		case "subscribe":
			for _, raw := range f.Topics {
				s.subscribeOne(r.Context(), c, id, raw, f.LastEventID)
			}
		case "unsubscribe":
			for _, raw := range f.Topics {
				s.Hub.Unsubscribe(c, id.Tenant, raw)
			}
		case "ping":
			// The 15s heartbeat already sends pong; nothing to do.
		case "refresh_token":
			s.refreshWS(r, c, id, f.Token)
		}
	}
	c.Close(0, "client_disconnect")
	<-c.Done()
}

// refreshWS applies an in-band token refresh (RTH-FR-010); a different subject
// closes the connection (BR-10).
func (s *Server) refreshWS(r *http.Request, c *fanout.Conn, id *connIdentity, token string) {
	claims, err := s.Verifier.Verify(r.Context(), token)
	if err != nil {
		return
	}
	if claims.EffectiveUser() != id.Subject || claims.TenantID != id.Tenant {
		c.Close(fanout.CloseTokenExpired, "TOKEN_EXPIRED")
		return
	}
	if claims.ExpiresAt != nil {
		c.RefreshExp(claims.ExpiresAt.Time)
	}
}

// wsSink writes JSON frames (RTH-FR-002). gorilla permits one concurrent writer;
// the connection's single writer goroutine satisfies that, and the mutex guards
// the connect-time preamble against it.
type wsSink struct {
	conn *websocket.Conn
	mu   sync.Mutex
}

func (s *wsSink) sendJSON(v any) error {
	b, _ := json.Marshal(v)
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.conn.WriteMessage(websocket.TextMessage, b)
}

// WriteEvent emits a data or control frame. Control data already carries its
// own "type" field, so it is forwarded verbatim (RTH-FR-002 down frames).
func (s *wsSink) WriteEvent(evID, event string, data []byte) error {
	if event == "control" {
		s.mu.Lock()
		defer s.mu.Unlock()
		return s.conn.WriteMessage(websocket.TextMessage, data)
	}
	return s.sendJSON(map[string]any{"type": "event", "topic": event, "id": evID, "data": json.RawMessage(data)})
}

// WriteHeartbeat sends a pong frame every 15s (RTH-FR-033).
func (s *wsSink) WriteHeartbeat(degraded bool) error {
	m := map[string]any{"type": "pong"}
	if degraded {
		m["degraded"] = true
	}
	return s.sendJSON(m)
}

// Close sends a WebSocket close frame with the application close code (§5).
func (s *wsSink) Close(code int, reason string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	msg := websocket.FormatCloseMessage(code, reason)
	if code == 0 {
		msg = websocket.FormatCloseMessage(websocket.CloseNormalClosure, reason)
	}
	_ = s.conn.WriteMessage(websocket.CloseMessage, msg)
	return s.conn.Close()
}

var _ fanout.Sink = (*wsSink)(nil)
