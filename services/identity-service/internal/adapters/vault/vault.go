// Package vault holds the real Vault transit Signer adapter (IDN-FR-050).
//
// Private keys never leave Vault: Generate creates an RSA-2048 transit key and
// reads back only its public PEM; Sign asks Vault to produce the RS256
// (pkcs1v15 over sha2-256) signature. It speaks the real Vault HTTP API
// (deploy/docker-compose.dev.yml: dev-mode Vault = real Vault API) — no SDK
// dependency, just net/http. When VAULT_ADDR is unset the service falls back to
// keys.LocalSigner (dev), so this adapter is the production signing path.
package vault

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"
)

// TransitSigner implements keys.Signer against Vault's transit secrets engine.
type TransitSigner struct {
	Addr   string // e.g. http://localhost:8200
	Token  string // Vault token
	Mount  string // transit mount path (default "transit")
	client *http.Client
}

// New builds a TransitSigner and ensures the transit mount exists (idempotent).
func New(addr, token, mount string) (*TransitSigner, error) {
	if mount == "" {
		mount = "transit"
	}
	s := &TransitSigner{Addr: strings.TrimRight(addr, "/"), Token: token, Mount: mount, client: &http.Client{Timeout: 5 * time.Second}}
	if err := s.ensureMount(context.Background()); err != nil {
		return nil, err
	}
	return s, nil
}

func (s *TransitSigner) do(ctx context.Context, method, path string, body any) (map[string]any, int, error) {
	var rdr *bytes.Reader
	if body != nil {
		raw, _ := json.Marshal(body)
		rdr = bytes.NewReader(raw)
	} else {
		rdr = bytes.NewReader(nil)
	}
	req, err := http.NewRequestWithContext(ctx, method, s.Addr+path, rdr)
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("X-Vault-Token", s.Token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := s.client.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	var out map[string]any
	if resp.StatusCode != http.StatusNoContent {
		_ = json.NewDecoder(resp.Body).Decode(&out)
	}
	return out, resp.StatusCode, nil
}

// ensureMount enables the transit engine, tolerating "already in use".
func (s *TransitSigner) ensureMount(ctx context.Context) error {
	out, code, err := s.do(ctx, http.MethodPost, "/v1/sys/mounts/"+s.Mount, map[string]any{"type": "transit"})
	if err != nil {
		return err
	}
	if code == http.StatusNoContent || code == http.StatusOK {
		return nil
	}
	// 400 with "path is already in use" is fine.
	if code == http.StatusBadRequest {
		if errs, _ := out["errors"].([]any); len(errs) > 0 {
			if msg, _ := errs[0].(string); strings.Contains(msg, "already in use") {
				return nil
			}
		}
	}
	return fmt.Errorf("vault enable transit: status %d (%v)", code, out["errors"])
}

// Generate creates a new RSA-2048 transit key and returns its name (kid) and
// public-key PEM. The private key is created in and never leaves Vault.
func (s *TransitSigner) Generate(ctx context.Context) (string, string, error) {
	kid := "identity-" + uuid.NewString()
	if _, code, err := s.do(ctx, http.MethodPost, "/v1/"+s.Mount+"/keys/"+kid, map[string]any{"type": "rsa-2048"}); err != nil {
		return "", "", err
	} else if code != http.StatusOK && code != http.StatusNoContent {
		return "", "", fmt.Errorf("vault create key: status %d", code)
	}
	pem, err := s.publicPEM(ctx, kid)
	if err != nil {
		return "", "", err
	}
	return kid, pem, nil
}

func (s *TransitSigner) publicPEM(ctx context.Context, kid string) (string, error) {
	out, code, err := s.do(ctx, http.MethodGet, "/v1/"+s.Mount+"/keys/"+kid, nil)
	if err != nil {
		return "", err
	}
	if code != http.StatusOK {
		return "", fmt.Errorf("vault read key: status %d", code)
	}
	data, _ := out["data"].(map[string]any)
	keysMap, _ := data["keys"].(map[string]any)
	// Latest version wins; dev keys start at version "1".
	var pem string
	for _, v := range keysMap {
		if m, ok := v.(map[string]any); ok {
			if p, ok := m["public_key"].(string); ok && p != "" {
				pem = p
			}
		}
	}
	if pem == "" {
		return "", fmt.Errorf("vault key %s: no public_key in response", kid)
	}
	return pem, nil
}

// Sign returns the RS256 (pkcs1v15/sha2-256) signature over signingString,
// computed inside Vault. Vault hashes the input itself (prehashed=false).
func (s *TransitSigner) Sign(ctx context.Context, kid string, signingString []byte) ([]byte, error) {
	body := map[string]any{
		"input":               base64.StdEncoding.EncodeToString(signingString),
		"hash_algorithm":      "sha2-256",
		"signature_algorithm": "pkcs1v15",
		"prehashed":           false,
	}
	out, code, err := s.do(ctx, http.MethodPost, "/v1/"+s.Mount+"/sign/"+kid, body)
	if err != nil {
		return nil, err
	}
	if code != http.StatusOK {
		return nil, fmt.Errorf("vault sign: status %d (%v)", code, out["errors"])
	}
	data, _ := out["data"].(map[string]any)
	sig, _ := data["signature"].(string)
	// Format: "vault:v<version>:<base64-std-signature>".
	parts := strings.SplitN(sig, ":", 3)
	if len(parts) != 3 {
		return nil, fmt.Errorf("vault sign: unexpected signature format %q", sig)
	}
	return base64.StdEncoding.DecodeString(parts[2])
}
