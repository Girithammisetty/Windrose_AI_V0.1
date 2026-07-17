package domain

import (
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestCanonicalJSONStableAcrossKeyOrder(t *testing.T) {
	a := map[string]any{"b": 1, "a": map[string]any{"y": 2, "x": 3}, "c": []any{3, 2, 1}}
	b := map[string]any{"c": []any{3, 2, 1}, "a": map[string]any{"x": 3, "y": 2}, "b": 1}
	if string(CanonicalJSON(a)) != string(CanonicalJSON(b)) {
		t.Fatalf("canonical json not stable:\n%s\n%s", CanonicalJSON(a), CanonicalJSON(b))
	}
	if PayloadDigest(a) != PayloadDigest(b) {
		t.Fatal("payload digest differs for equal payloads with different key order")
	}
}

func TestChainHashDeterministicAndSensitive(t *testing.T) {
	tenant := uuid.New()
	id := uuid.New()
	ts := time.Date(2026, 7, 8, 10, 0, 0, 0, time.UTC)
	h1 := ChainHash("prev", id, "digestA", ts)
	h2 := ChainHash("prev", id, "digestA", ts)
	if h1 != h2 {
		t.Fatal("chain hash not deterministic")
	}
	if ChainHash("prev", id, "digestB", ts) == h1 {
		t.Fatal("chain hash insensitive to payload_digest (tamper would be invisible)")
	}
	if ChainHash("other", id, "digestA", ts) == h1 {
		t.Fatal("chain hash insensitive to prev (chain break invisible)")
	}
	if GenesisHash(tenant, "2026-07-08") == GenesisHash(tenant, "2026-07-09") {
		t.Fatal("genesis hash must differ per day")
	}
}

func TestValidateEnvelope(t *testing.T) {
	good := Envelope{
		EventID: uuid.New(), EventType: "dataset.created", TenantID: uuid.New(),
		Actor: Actor{Type: "user", ID: "u-1"}, OccurredAt: time.Now(),
	}
	if err := ValidateEnvelope(good); err != nil {
		t.Fatalf("valid envelope rejected: %v", err)
	}
	bad := good
	bad.TenantID = uuid.Nil
	if err := ValidateEnvelope(bad); err == nil {
		t.Fatal("missing tenant_id must be rejected (ENVELOPE_INVALID)")
	}
}

func TestPIIGate(t *testing.T) {
	clean := CanonicalJSON(map[string]any{"assignee": "u-91", "count": 3})
	if r := PIIGate("case.assigned", clean, nil); !r.Clean {
		t.Fatalf("clean payload wrongly flagged: %+v", r)
	}
	email := CanonicalJSON(map[string]any{"email": "jane.doe@example.com"})
	if r := PIIGate("mystery.event", email, nil); r.Clean || r.Reason != "email" {
		t.Fatalf("email not caught: %+v", r)
	}
	// Allowlisted event type skips scanning even if it would match.
	if r := PIIGate("trusted.event", email, map[string]bool{"trusted.event": true}); !r.Clean {
		t.Fatal("allowlisted event type should skip scan")
	}
	ssn := CanonicalJSON(map[string]any{"note": "ssn 123-45-6789"})
	if r := PIIGate("mystery.event", ssn, nil); r.Clean || r.Reason != "national_id" {
		t.Fatalf("ssn not caught: %+v", r)
	}
}

func TestParseURN(t *testing.T) {
	p := ParseURN("wr:t-42:dataset:dataset/ds-9f2")
	if p.Service != "dataset" || p.Type != "dataset" || p.ID != "ds-9f2" {
		t.Fatalf("bad urn parse: %+v", p)
	}
	if ActionFromEventType("dataset", "dataset.created") != "dataset.dataset.created" {
		t.Fatalf("bad action derive")
	}
}

func TestSubscriptionMatching(t *testing.T) {
	s, err := NewSubscription("")
	if err != nil {
		t.Fatal(err)
	}
	for _, in := range []string{
		"dataset.events.v1", "case.events.v1", "ai.proposal.v1", "ai.tool_invoked.v1",
		"ai.agent_run.v1", "ai.token_usage.v1", // token_usage now flows after wave 1
		"security.cross_tenant_denied",
	} {
		if !s.Matches(in) {
			t.Fatalf("should match %s", in)
		}
	}
	for _, out := range []string{"dataset.events.v1.audit-ingest.dlq", "random.topic", "ai.other.v1", "ai.token_usage.v2"} {
		if s.Matches(out) {
			t.Fatalf("should not match %s", out)
		}
	}
}
