//go:build integration

package integration

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/domain"
	"github.com/windrose-ai/identity-service/internal/keys"
	pgstore "github.com/windrose-ai/identity-service/internal/store/postgres"
)

// TestF1_RestartTokenIssuance proves the F-1 fix: after a process restart in
// Postgres mode, the persisted signing key is still in the registry but the
// FRESH LocalSigner has no private key for it. Bootstrap must detect this and
// mint an immediately-usable key so token issuance keeps working — otherwise
// every issuance would fail "unknown kid" with no recovery path.
func TestF1_RestartTokenIssuance(t *testing.T) {
	requirePG(t)
	ctx := context.Background()
	store := pgstore.New(appPool)

	issue := func(km *keys.KeyManager) (string, error) {
		iss := keys.NewIssuer(km, time.Now)
		tid, _ := uuid.NewV7()
		tok, _, err := iss.Issue(domain.Claims{Subject: "u", TenantID: tid, Typ: domain.TypUser, Scopes: []string{"a.b.c"}})
		if err != nil {
			return "", err
		}
		// Round-trip through Verify to confirm the kid resolves.
		if _, err := iss.Verify(tok); err != nil {
			return "", err
		}
		return tok, nil
	}

	// Boot 1: fresh signer, empty (or shared) registry.
	km1 := keys.NewKeyManager(store, keys.NewLocalSigner(), time.Now)
	if err := km1.Bootstrap(ctx); err != nil {
		t.Fatalf("boot 1 bootstrap: %v", err)
	}
	if _, err := issue(km1); err != nil {
		t.Fatalf("boot 1 issuance: %v", err)
	}

	// Boot 2: brand-new LocalSigner (simulating a restart) over the SAME
	// Postgres registry, which now contains boot 1's usable-but-unmintable key.
	km2 := keys.NewKeyManager(store, keys.NewLocalSigner(), time.Now)
	if err := km2.Bootstrap(ctx); err != nil {
		t.Fatalf("boot 2 bootstrap: %v", err)
	}
	if _, err := issue(km2); err != nil {
		t.Fatalf("F-1: token issuance broken after restart: %v", err)
	}

	// The active signing key after boot 2 must be one this signer can sign
	// with, and JWKS must publish it for verifiers.
	active, err := km2.SigningKey()
	if err != nil {
		t.Fatalf("no active key after boot 2: %v", err)
	}
	jwks, err := km2.JWKS()
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, k := range jwks.Keys {
		if k.Kid == active.KID {
			found = true
		}
	}
	if !found {
		t.Fatal("active key missing from JWKS after restart")
	}
}
