package api

import (
	"context"
	"net/http"

	"github.com/windrose-ai/realtime-hub/internal/authz"
	"github.com/windrose-ai/realtime-hub/internal/fanout"
	"github.com/windrose-ai/realtime-hub/internal/topics"
)

// subscribeOne validates a topic's grammar (RTH-FR-003), authorizes it per-topic
// via OPA/structural rules (RTH-FR-012), and subscribes the connection. On
// failure it delivers the per-topic control error (INVALID_TOPIC /
// TOPIC_FORBIDDEN) without tearing the connection down, and audits denials
// (AC-5). lastEventID drives Last-Event-ID resume (RTH-FR-031).
func (s *Server) subscribeOne(ctx context.Context, c *fanout.Conn, id *connIdentity, raw, lastEventID string) bool {
	t, err := topics.Parse(raw)
	if err != nil {
		c.EnqueueControl(fanout.CtrlError, map[string]any{"topic": raw, "code": "INVALID_TOPIC"})
		return false
	}
	req := authz.Request{
		Subject: authz.Subject{ID: subjectID(id), Typ: id.Typ, OboSub: oboSub(id), Scopes: id.Scopes},
		Tenant:  id.Tenant,
		Topic:   t,
	}
	dec := s.Authz.Authorize(ctx, req)
	if !dec.Allow {
		c.EnqueueControl(fanout.CtrlError, map[string]any{"topic": raw, "code": "TOPIC_FORBIDDEN"})
		if s.Metrics != nil {
			s.Metrics.SubscribeDenied.Inc()
		}
		s.Auditor.TopicDenied(ctx, id.Tenant, id.Subject, raw, dec.Reason)
		return false
	}
	urn := resourceURNFor(id.Tenant, t)
	s.Hub.Subscribe(ctx, c, id.Tenant, raw, urn, lastEventID)
	return true
}

// resourceURNFor returns the resource URN a topic maps to for the revocation
// index (RTH-FR-013): run-status idents are URNs; proposals derive one.
func resourceURNFor(tenant string, t topics.Topic) string {
	switch t.Scheme {
	case topics.SchemeRunStatus:
		return t.Ident
	case topics.SchemeProposal:
		return topics.ProposalURN(tenant, t.Ident)
	default:
		return ""
	}
}

// subjectID / oboSub carry OBO attribution into the authz subject. For ticket
// connects the effective user is already resolved into Subject.
func subjectID(id *connIdentity) string { return id.Subject }
func oboSub(id *connIdentity) string    { return "" }

// reserveConn claims the per-tenant/user/pod caps (RTH-FR-040). It writes the
// 429 CONNECTION_LIMIT response itself on refusal (honoring X-Replace-Oldest
// for the per-user cap) and returns ok=false.
func (s *Server) reserveConn(w http.ResponseWriter, r *http.Request, id *connIdentity) bool {
	if s.Hub.PodFull() {
		s.limited(w, r)
		return false
	}
	kind, err := s.Caps.Reserve(r.Context(), id.Tenant, id.Subject)
	if err != nil {
		writeErr(w, r, http.StatusInternalServerError, "INTERNAL", "cap reserve failed", 0)
		return false
	}
	if kind == fanout.LimitUser && r.Header.Get("X-Replace-Oldest") == "true" {
		if s.Hub.EvictOldestUser(id.Tenant, id.Subject) {
			// The evicted slot was released; retry once.
			if kind2, err := s.Caps.Reserve(r.Context(), id.Tenant, id.Subject); err == nil && kind2 == fanout.LimitNone {
				return true
			}
		}
	}
	if kind != fanout.LimitNone {
		s.limited(w, r)
		return false
	}
	return true
}

func (s *Server) limited(w http.ResponseWriter, r *http.Request) {
	if s.Metrics != nil {
		s.Metrics.ConnLimited.Inc()
	}
	writeErr(w, r, http.StatusTooManyRequests, "CONNECTION_LIMIT", "connection limit reached", 5)
}
