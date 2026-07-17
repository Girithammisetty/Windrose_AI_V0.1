package webhook

import (
	"testing"
	"time"

	"github.com/windrose-ai/notification-service/internal/domain"
)

// TestAC04_SignatureVerifyAndReplay proves a consumer can verify the
// X-Windrose-Signature with HMAC-SHA256 over timestamp.body and that a stale
// timestamp (>300s) fails (AC-4).
func TestAC04_SignatureVerifyAndReplay(t *testing.T) {
	secret := "sekret"
	body := []byte(`{"event_id":"e1","event_type":"case.created"}`)
	now := time.Now().Unix()
	hdr := SignatureHeader([]domain.WebhookSecret{{Version: 1, Secret: secret}}, now, body)

	if !Verify(hdr, secret, now, now, 300, body) {
		t.Fatal("valid signature should verify")
	}
	if Verify(hdr, secret, now-400, now, 300, body) {
		t.Fatal("timestamp older than 300s must fail (replay guard)")
	}
	if Verify(hdr, "wrong", now, now, 300, body) {
		t.Fatal("wrong secret must fail")
	}
}

// TestAC06_DualSecretRotation proves both old and new secrets validate during
// the rotation overlap (AC-6).
func TestAC06_DualSecretRotation(t *testing.T) {
	body := []byte(`{"x":1}`)
	now := time.Now().Unix()
	future := time.Now().Add(time.Hour)
	secrets := []domain.WebhookSecret{
		{Version: 1, Secret: "old", ExpiresAt: &future}, // still active during overlap
		{Version: 2, Secret: "new"},
	}
	hdr := SignatureHeader(secrets, now, body)
	if !Verify(hdr, "old", now, now, 300, body) {
		t.Fatal("old secret should still validate during overlap")
	}
	if !Verify(hdr, "new", now, now, 300, body) {
		t.Fatal("new secret should validate")
	}
}

func TestNextRetrySchedule(t *testing.T) {
	base := time.Unix(0, 0)
	want := []time.Duration{time.Minute, 5 * time.Minute, 30 * time.Minute, 2 * time.Hour, 6 * time.Hour, 24 * time.Hour}
	for i, d := range want {
		at, ok := NextRetryAt(base, i+1)
		if !ok || !at.Equal(base.Add(d)) {
			t.Fatalf("attempt %d: got %v ok=%v want %v", i+1, at.Sub(base), ok, d)
		}
	}
	if _, ok := NextRetryAt(base, len(want)+1); ok {
		t.Fatal("schedule should be exhausted after last entry")
	}
}
