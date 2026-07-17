package email

import (
	"context"
	"errors"
	"sync"
	"time"
)

// Sender fans a message across ordered providers with the BR-9 failover matrix
// and a per-provider in-memory circuit breaker (a provider that keeps failing
// is skipped until its cooldown probe). The first provider is primary; the rest
// are failover targets selected on Transient errors only.
type Sender struct {
	providers []Provider
	mu        sync.Mutex
	breakers  map[string]*breaker
}

// NewSender builds a Sender over an ordered provider list (primary first).
func NewSender(providers ...Provider) *Sender {
	return &Sender{providers: providers, breakers: map[string]*breaker{}}
}

// Providers returns the configured provider names (boot introspection).
func (s *Sender) Providers() []string {
	out := make([]string, 0, len(s.providers))
	for _, p := range s.providers {
		out = append(out, p.Name())
	}
	return out
}

// Result reports the outcome of a Send attempt.
type Result struct {
	Provider      string
	ProviderMsgID string
	Class         ErrorClass
	Err           error
}

// Send tries providers in order. Permanent and Ambiguous errors do not fail
// over (BR-9); Transient errors advance to the next provider. A provider with
// an open circuit is skipped.
func (s *Sender) Send(ctx context.Context, m Message) Result {
	var last Result
	last.Class = ClassTransient
	last.Err = errors.New("no email provider available")
	for _, p := range s.providers {
		b := s.breakerFor(p.Name())
		if b.open() {
			continue
		}
		id, err := p.Send(ctx, m)
		if err == nil {
			b.success()
			return Result{Provider: p.Name(), ProviderMsgID: id, Class: ClassNone}
		}
		cls := classOf(err)
		switch cls {
		case ClassPermanent:
			b.failure()
			return Result{Provider: p.Name(), Class: ClassPermanent, Err: err}
		case ClassAmbiguous:
			// Post-submit timeout: do not fail over, caller retries same provider.
			return Result{Provider: p.Name(), Class: ClassAmbiguous, Err: err}
		default: // Transient
			b.failure()
			last = Result{Provider: p.Name(), Class: ClassTransient, Err: err}
		}
	}
	return last
}

func (s *Sender) breakerFor(name string) *breaker {
	s.mu.Lock()
	defer s.mu.Unlock()
	b, ok := s.breakers[name]
	if !ok {
		b = &breaker{threshold: 5, cooldown: 30 * time.Second}
		s.breakers[name] = b
	}
	return b
}

func classOf(err error) ErrorClass {
	var se *SendError
	if errors.As(err, &se) {
		return se.Class
	}
	return ClassTransient
}

// breaker is a minimal per-provider circuit breaker: after `threshold`
// consecutive failures it opens for `cooldown`, then allows a single probe.
type breaker struct {
	mu        sync.Mutex
	failures  int
	openedAt  time.Time
	threshold int
	cooldown  time.Duration
}

func (b *breaker) open() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.failures < b.threshold {
		return false
	}
	if time.Since(b.openedAt) >= b.cooldown {
		// half-open: allow one probe.
		return false
	}
	return true
}

func (b *breaker) success() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.failures = 0
}

func (b *breaker) failure() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.failures++
	if b.failures >= b.threshold {
		b.openedAt = time.Now()
	}
}
