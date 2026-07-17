package api

import (
	"fmt"
	"net/http"
	"sync"

	"github.com/google/uuid"

	"github.com/windrose-ai/realtime-hub/internal/fanout"
)

// handleSSE is the primary transport (RTH-FR-001): GET /api/v1/stream. It
// authenticates via ticket or bearer, enforces caps, opens the event stream,
// and subscribes each requested topic (per-topic authz, resume via
// Last-Event-ID). All UI features must work over SSE alone (RTH-FR-002).
func (s *Server) handleSSE(w http.ResponseWriter, r *http.Request) {
	id, err := s.authenticateConnect(r)
	if err != nil {
		writeErr(w, r, http.StatusUnauthorized, "UNAUTHENTICATED", "invalid ticket or token", 0)
		return
	}
	if len(id.Topics) > s.MaxTopicsPerConn {
		writeErr(w, r, http.StatusBadRequest, "VALIDATION_FAILED",
			fmt.Sprintf("max %d topics per connection", s.MaxTopicsPerConn), 0)
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeErr(w, r, http.StatusInternalServerError, "INTERNAL", "streaming unsupported", 0)
		return
	}
	if !s.reserveConn(w, r, id) {
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no") // disable proxy buffering for SSE
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	connID := uuid.NewString()
	sink := &sseSink{w: w, f: flusher}
	c := s.Hub.AddConn(connID, id.Subject, id.Tenant, id.Typ, id.Scopes, "sse", id.IPHash, sink, id.Exp)
	// Expose the connection id so incremental-subscribe/refresh side channels
	// can target it (RTH-FR-001).
	sink.writeRaw(fmt.Sprintf("event: control\ndata: {\"type\":\"connected\",\"conn_id\":%q}\n\n", connID))

	lastEventID := r.Header.Get("Last-Event-ID")
	for _, raw := range id.Topics {
		s.subscribeOne(r.Context(), c, id, raw, lastEventID)
	}

	// Block until the client disconnects or the connection closes; the writer
	// goroutine owns all subsequent writes. On client disconnect we must wait
	// for the writer to exit before returning, or it would touch the
	// ResponseWriter after the handler returns (use-after-free panic).
	select {
	case <-r.Context().Done():
		c.Close(0, "client_disconnect")
		<-c.Done()
	case <-c.Done():
	}
}

// sseSink writes the SSE wire format (RTH-FR-001). A single writer goroutine
// drives it; the mutex only guards the connect-time preamble vs. that goroutine.
type sseSink struct {
	w  http.ResponseWriter
	f  http.Flusher
	mu sync.Mutex
}

func (s *sseSink) writeRaw(str string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	_, _ = fmt.Fprint(s.w, str)
	s.f.Flush()
}

// WriteEvent writes id/event/data frames (RTH-FR-001).
func (s *sseSink) WriteEvent(evID, event string, data []byte) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if evID != "" {
		if _, err := fmt.Fprintf(s.w, "id: %s\n", evID); err != nil {
			return err
		}
	}
	if _, err := fmt.Fprintf(s.w, "event: %s\ndata: %s\n\n", event, data); err != nil {
		return err
	}
	s.f.Flush()
	return nil
}

// WriteHeartbeat writes the 15s `: hb` comment (RTH-FR-033); when degraded it
// also emits a control heartbeat so UIs can show a staleness hint (BR-7).
func (s *sseSink) WriteHeartbeat(degraded bool) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, err := fmt.Fprint(s.w, ": hb\n\n"); err != nil {
		return err
	}
	if degraded {
		_, _ = fmt.Fprint(s.w, "event: control\ndata: {\"type\":\"heartbeat\",\"degraded\":true}\n\n")
	}
	s.f.Flush()
	return nil
}

// Close sends a final control frame with the close code (SSE has no native
// close codes) and lets the handler return, terminating the response.
func (s *sseSink) Close(code int, reason string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if code != 0 {
		_, _ = fmt.Fprintf(s.w, "event: control\ndata: {\"type\":\"close\",\"code\":%d,\"reason\":%q}\n\n", code, reason)
		s.f.Flush()
	}
	return nil
}

var _ fanout.Sink = (*sseSink)(nil)
