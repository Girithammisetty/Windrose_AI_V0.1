package email

import (
	"context"
	"errors"
	"net/http"
	"testing"
)

type stubProvider struct {
	name string
	err  error
	sent int
}

func (s *stubProvider) Name() string { return s.name }
func (s *stubProvider) Send(context.Context, Message) (string, error) {
	s.sent++
	if s.err != nil {
		return "", s.err
	}
	return "msg-" + s.name, nil
}
func (s *stubProvider) ParseStatusCallback(*http.Request) ([]StatusUpdate, error) { return nil, nil }

// TestBR9_TransientFailsOver proves a Transient error on the primary fails over
// to the secondary (BR-9).
func TestBR9_TransientFailsOver(t *testing.T) {
	primary := &stubProvider{name: "ses", err: &SendError{Class: ClassTransient, Err: errors.New("5xx")}}
	secondary := &stubProvider{name: "sendgrid"}
	s := NewSender(primary, secondary)
	res := s.Send(context.Background(), Message{To: "a@b.c"})
	if res.Class != ClassNone || res.Provider != "sendgrid" {
		t.Fatalf("expected failover to sendgrid success, got %+v", res)
	}
	if secondary.sent != 1 {
		t.Fatalf("secondary should have been tried once, got %d", secondary.sent)
	}
}

// TestBR9_PermanentNoFailover proves a Permanent error does not fail over.
func TestBR9_PermanentNoFailover(t *testing.T) {
	primary := &stubProvider{name: "ses", err: &SendError{Class: ClassPermanent, Err: errors.New("bad addr")}}
	secondary := &stubProvider{name: "sendgrid"}
	s := NewSender(primary, secondary)
	res := s.Send(context.Background(), Message{To: "a@b.c"})
	if res.Class != ClassPermanent {
		t.Fatalf("expected permanent, got %+v", res)
	}
	if secondary.sent != 0 {
		t.Fatalf("secondary must not be tried on permanent, got %d", secondary.sent)
	}
}

// TestBR9_AmbiguousNoFailover proves an Ambiguous (post-submit) error does not
// fail over (avoids double-send).
func TestBR9_AmbiguousNoFailover(t *testing.T) {
	primary := &stubProvider{name: "ses", err: &SendError{Class: ClassAmbiguous, Err: errors.New("timeout")}}
	secondary := &stubProvider{name: "sendgrid"}
	s := NewSender(primary, secondary)
	res := s.Send(context.Background(), Message{To: "a@b.c"})
	if res.Class != ClassAmbiguous || secondary.sent != 0 {
		t.Fatalf("ambiguous must not fail over, got %+v secondary=%d", res, secondary.sent)
	}
}
