package keys

import (
	"context"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/domain"
	"github.com/windrose-ai/identity-service/internal/store/memory"
)

type fakeClock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *fakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *fakeClock) Advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.t = c.t.Add(d)
}

func newKM(t *testing.T) (*KeyManager, *Issuer, *fakeClock) {
	t.Helper()
	clock := &fakeClock{t: time.Now().UTC()}
	km := NewKeyManager(memory.New(), NewLocalSigner(), clock.Now)
	if err := km.Bootstrap(context.Background()); err != nil {
		t.Fatal(err)
	}
	return km, NewIssuer(km, clock.Now), clock
}

func testClaims() domain.Claims {
	tid, _ := uuid.NewV7()
	return domain.Claims{Subject: "u-1", TenantID: tid, Typ: domain.TypUser, Scopes: []string{"a.b.c"}}
}

// TestBootstrapRestartMintsUsableKey covers the F-1 fix at unit tier: a
// second KeyManager with a FRESH LocalSigner over the same store must mint a
// new immediately-usable key (the persisted key is unmintable by the new
// in-memory signer), so issuance keeps working.
func TestBootstrapRestartMintsUsableKey(t *testing.T) {
	store := memory.New()
	clock := &fakeClock{t: time.Now().UTC()}

	km1 := NewKeyManager(store, NewLocalSigner(), clock.Now)
	if err := km1.Bootstrap(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, _, err := NewIssuer(km1, clock.Now).Issue(testClaims()); err != nil {
		t.Fatalf("boot 1 issuance: %v", err)
	}

	// New signer = simulated restart; same store carries boot 1's key.
	km2 := NewKeyManager(store, NewLocalSigner(), clock.Now)
	if err := km2.Bootstrap(context.Background()); err != nil {
		t.Fatal(err)
	}
	iss2 := NewIssuer(km2, clock.Now)
	tok, _, err := iss2.Issue(testClaims())
	if err != nil {
		t.Fatalf("F-1: issuance broken after restart: %v", err)
	}
	if _, err := iss2.Verify(tok); err != nil {
		t.Fatalf("F-1: verify broken after restart: %v", err)
	}
	// Two keys now exist in the registry (boot 1's + boot 2's fresh one).
	ks, _ := store.ListSigningKeys(context.Background())
	if len(ks) != 2 {
		t.Fatalf("expected 2 registry keys after restart, got %d", len(ks))
	}
}

func TestIssueAndVerify(t *testing.T) {
	_, iss, _ := newKM(t)
	tok, expiresIn, err := iss.Issue(testClaims())
	if err != nil {
		t.Fatal(err)
	}
	if expiresIn != 300 {
		t.Errorf("TTL = %d, want 300 (MASTER-FR-010)", expiresIn)
	}
	claims, err := iss.Verify(tok)
	if err != nil {
		t.Fatalf("verify failed: %v", err)
	}
	if claims.Subject != "u-1" || claims.Typ != domain.TypUser || len(claims.Scopes) != 1 {
		t.Errorf("claims roundtrip wrong: %+v", claims)
	}
}

func TestVerifyRejectsExpired(t *testing.T) {
	_, iss, clock := newKM(t)
	tok, _, _ := iss.Issue(testClaims())
	clock.Advance(domain.TokenTTL + domain.ClockSkew + time.Minute)
	if _, err := iss.Verify(tok); err == nil {
		t.Fatal("expired token verified")
	}
}

func TestVerifyRejectsAlgNone(t *testing.T) {
	_, iss, _ := newKM(t)
	// alg=none with empty signature (IDN-FR-045 / AC-13).
	none := "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0." + // {"alg":"none","typ":"JWT"}
		"eyJzdWIiOiJ1LTEiLCJ0eXAiOiJ1c2VyIn0."
	if _, err := iss.Verify(none); err == nil {
		t.Fatal("alg=none token verified")
	}
	// HS256 (symmetric downgrade) must also fail.
	hs := "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1LTEifQ.c2ln"
	if _, err := iss.Verify(hs); err == nil {
		t.Fatal("HS256 token verified")
	}
	// Tampered payload fails signature check.
	tok, _, _ := iss.Issue(testClaims())
	parts := strings.Split(tok, ".")
	if _, err := iss.Verify(parts[0] + ".eyJzdWIiOiJoYXgifQ." + parts[2]); err == nil {
		t.Fatal("tampered token verified")
	}
}

// TestRotationOverlap covers IDN-FR-052 + AC-8: the new key is published
// >=10 min before use; old-key tokens verify during the overlap window and
// fail after retirement.
func TestRotationOverlap(t *testing.T) {
	km, iss, clock := newKM(t)
	oldTok, _, err := iss.Issue(testClaims())
	if err != nil {
		t.Fatal(err)
	}
	oldKey, _ := km.SigningKey()

	newKid, err := km.Rotate(context.Background(), domain.Actor{Type: "platform", ID: "test"})
	if err != nil {
		t.Fatal(err)
	}
	// New key is in JWKS immediately (published before use)...
	jwks, _ := km.JWKS()
	if len(jwks.Keys) != 2 {
		t.Fatalf("JWKS should carry both keys during overlap, got %d", len(jwks.Keys))
	}
	// ...but not used for signing until not_before (+10 min).
	cur, _ := km.SigningKey()
	if cur.KID != oldKey.KID {
		t.Fatal("new key used before its not_before")
	}
	// Old-key tokens verify during the overlap window.
	if _, err := iss.Verify(oldTok); err != nil {
		t.Fatalf("old-key token should verify during overlap: %v", err)
	}
	// After the lead, the new key signs.
	clock.Advance(11 * time.Minute)
	cur, _ = km.SigningKey()
	if cur.KID != newKid {
		t.Fatalf("expected new key to sign after not_before, got %s", cur.KID)
	}
	newTok, _, err := iss.Issue(testClaims())
	if err != nil {
		t.Fatal(err)
	}
	// Past retirement (= not_before + TTL + skew), old-key tokens fail (AC-8).
	clock.Advance(domain.TokenTTL + domain.ClockSkew + time.Minute)
	if _, err := iss.Verify(oldTok); err == nil {
		t.Fatal("old-key token verified after retirement")
	}
	// The old kid is also dropped from JWKS.
	jwks, _ = km.JWKS()
	if len(jwks.Keys) != 1 || jwks.Keys[0].Kid != newKid {
		t.Fatalf("JWKS should only carry the new key after retirement")
	}
	// New-key tokens minted fresh still verify.
	fresh, _, _ := iss.Issue(testClaims())
	if _, err := iss.Verify(fresh); err != nil {
		t.Fatalf("fresh token failed: %v", err)
	}
	_ = newTok // newTok itself has expired by now (TTL), which is correct
}
