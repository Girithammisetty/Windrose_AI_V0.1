package ingest

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/audit-service/internal/chain"
	"github.com/windrose-ai/audit-service/internal/domain"
)

// fakeInserter is an in-memory RecordInserter — a UNIT-TEST DOUBLE ONLY; it is
// never reachable from cmd/server (which wires the real *chstore.Store).
type fakeInserter struct{ rows []domain.Record }

func (f *fakeInserter) Insert(_ context.Context, r domain.Record) error {
	f.rows = append(f.rows, r)
	return nil
}

// fakeChain is an in-memory ChainAppender computing real hashes with a local seq.
type fakeChain struct{ seq uint64; prev map[string]string }

func (f *fakeChain) Append(_ context.Context, tenant, eventID uuid.UUID, digest string, occ time.Time) (chain.Link, error) {
	if f.prev == nil {
		f.prev = map[string]string{}
	}
	date := "2026-07-08"
	p, ok := f.prev[date]
	if !ok {
		p = domain.GenesisHash(tenant, date)
	}
	f.seq++
	h := domain.ChainHash(p, eventID, digest, occ)
	f.prev[date] = h
	return chain.Link{ChainDate: date, Seq: f.seq, Hash: h}, nil
}

func env(t string, payload map[string]any) domain.Envelope {
	return domain.Envelope{
		EventID: uuid.New(), EventType: t, TenantID: uuid.New(),
		Actor: domain.Actor{Type: "user", ID: "u-1"}, OccurredAt: time.Now().UTC(), Payload: payload,
	}
}

func TestProcessorStoresCleanPayload(t *testing.T) {
	ins := &fakeInserter{}
	p := &Processor{CH: ins, Chain: &fakeChain{}}
	e := env("case.assigned", map[string]any{"assignee": "u-91"})
	if err := p.Handle(context.Background(), Source{Topic: "case.events.v1"}, e); err != nil {
		t.Fatalf("handle: %v", err)
	}
	if len(ins.rows) != 1 {
		t.Fatalf("expected 1 row, got %d", len(ins.rows))
	}
	r := ins.rows[0]
	if r.PayloadJSON == "" || r.PayloadDigest == "" || r.ChainHash == "" {
		t.Fatalf("clean payload not stored inline with digest+chain: %+v", r)
	}
}

func TestProcessorDropsPIIBodyKeepsDigest(t *testing.T) {
	ins := &fakeInserter{}
	p := &Processor{CH: ins, Chain: &fakeChain{}}
	e := env("mystery.event", map[string]any{"email": "jane@example.com"})
	if err := p.Handle(context.Background(), Source{Topic: "mystery.events.v1", Partition: 2, Offset: 99}, e); err != nil {
		t.Fatalf("handle: %v", err)
	}
	r := ins.rows[0]
	if r.PayloadJSON != "" {
		t.Fatal("PII body should be withheld")
	}
	if r.PayloadDigest == "" || r.PayloadRef == "" {
		t.Fatalf("digest kept + ref set expected: %+v", r)
	}
}

func TestProcessorTerminalOnInvalidEnvelope(t *testing.T) {
	p := &Processor{CH: &fakeInserter{}, Chain: &fakeChain{}}
	bad := env("x.y", nil)
	bad.TenantID = uuid.Nil
	err := p.Handle(context.Background(), Source{}, bad)
	var term *TerminalError
	if !asTerminal(err, &term) || term.Reason != domain.ReasonEnvelopeInvalid {
		t.Fatalf("expected terminal ENVELOPE_INVALID, got %v", err)
	}
}
