package opaclient

import (
	"bytes"
	"context"
	"crypto/rsa"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

// FallbackConfig configures the synchronous rbac-service fallback used when
// the Redis projection is missing (RBC-FR-045) -- e.g. right after a Redis
// restart/failover/flush, before the projection worker has re-warmed every
// key. Without this configured, a Redis miss denies immediately and stays
// denied until an operator manually triggers a projection rebuild
// (deploy/local/reconcile.sh) -- a total-outage-until-human-intervenes failure
// mode that has no place in a production deploy. Configuring this closes that
// gap by calling rbac-service's own SQL-ground-truth self-heal endpoint
// (rbac-service internal/authz.Checker.Check, exposed as
// POST /api/v1/authz/check), which recomputes the decision from Postgres AND
// re-warms the Redis keys as a side effect -- so the NEXT request for the
// same user hits the fast Redis path again. One extra HTTP round-trip on a
// cold key, not a platform-wide outage.
type FallbackConfig struct {
	// RBACURL is rbac-service's base URL, e.g. http://localhost:8302.
	RBACURL string
	// SigningKeyPEM is the platform RS256 signing key every service already
	// uses to mint its action-catalog-registration service token
	// (REGISTER_SIGNING_KEY_PEM in each service's env) -- reused here, not a
	// new credential.
	SigningKeyPEM string
	SigningKID    string
	Issuer        string
	Audience      string
}

// FallbackConfigFromEnv reads the SAME env vars every service already sets
// for its deploy-time action-catalog registration (RBAC_URL,
// REGISTER_SIGNING_KEY_PEM, REGISTER_SIGNING_KID, JWT_ISSUER, JWT_AUDIENCE —
// see e.g. services/case-service/internal/register.Config) — reused here
// rather than inventing new config, since it's the same credential for the
// same purpose (a service proving its own identity to rbac-service). ok is
// false when RBAC_URL or REGISTER_SIGNING_KEY_PEM is unset, matching
// register.Register's own "not configured, skip" convention: an intentional
// opt-out, not a misconfiguration, so callers should skip quietly on ok=false
// but log loudly if EnableMissFallback still errors on a config that WAS
// present (e.g. an unparseable key).
func FallbackConfigFromEnv() (FallbackConfig, bool) {
	cfg := FallbackConfig{
		RBACURL:       os.Getenv("RBAC_URL"),
		SigningKeyPEM: os.Getenv("REGISTER_SIGNING_KEY_PEM"),
		SigningKID:    os.Getenv("REGISTER_SIGNING_KID"),
		Issuer:        os.Getenv("JWT_ISSUER"),
		Audience:      os.Getenv("JWT_AUDIENCE"),
	}
	return cfg, cfg.RBACURL != "" && cfg.SigningKeyPEM != ""
}

type fallback struct {
	cfg    FallbackConfig
	key    *rsa.PrivateKey
	client *http.Client
}

// EnableMissFallback wires the Redis-miss fallback into c. Call once at
// construction; until called, c behaves exactly as before (fail closed on a
// miss). Returns an error if the signing key doesn't parse -- a
// misconfigured fallback must be a startup error, not a silently-disabled
// security posture change (Rule 2).
func (c *Client) EnableMissFallback(cfg FallbackConfig) error {
	if cfg.RBACURL == "" || cfg.SigningKeyPEM == "" {
		return fmt.Errorf("opaclient: fallback requires RBACURL and SigningKeyPEM")
	}
	key, err := jwt.ParseRSAPrivateKeyFromPEM([]byte(cfg.SigningKeyPEM))
	if err != nil {
		return fmt.Errorf("opaclient: parse fallback signing key: %w", err)
	}
	c.fb = &fallback{cfg: cfg, key: key, client: &http.Client{Timeout: 3 * time.Second}}
	return nil
}

// checkResponse mirrors rbac-service's authz.Decision JSON shape
// ({"allowed":...,"reason":...,"miss":...}) -- deliberately a DIFFERENT field
// name (Allowed, not Allow) from opaclient.Decision, so this is decoded into
// its own type and mapped explicitly rather than unmarshaled straight into
// Decision.
type checkResponse struct {
	Allowed bool   `json:"allowed"`
	Reason  string `json:"reason"`
	Miss    bool   `json:"miss"`
}

// check calls rbac-service's SQL-ground-truth endpoint, scoped to the exact
// tenant of `in` via the minted token's tenant_id claim -- a service token
// bound to one tenant, never a cross-tenant/superadmin credential, matching
// rbac-service's own tenant-binding check on that endpoint
// (req.Tenant != claims.TenantID is satisfied by construction).
func (f *fallback) check(ctx context.Context, in Input) (Decision, error) {
	tok, err := f.mintToken(in.Tenant)
	if err != nil {
		return Decision{}, fmt.Errorf("mint fallback token: %w", err)
	}
	body, _ := json.Marshal(map[string]any{
		"subject": in.Subject, "action": in.Action,
		"resource_urn": in.ResourceURN, "workspace_id": in.WorkspaceID, "tenant": in.Tenant,
	})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, f.cfg.RBACURL+"/api/v1/authz/check", bytes.NewReader(body))
	if err != nil {
		return Decision{}, err
	}
	req.Header.Set("Authorization", "Bearer "+tok)
	req.Header.Set("Content-Type", "application/json")
	resp, err := f.client.Do(req)
	if err != nil {
		return Decision{}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return Decision{}, fmt.Errorf("rbac authz/check: status %d", resp.StatusCode)
	}
	var out checkResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return Decision{}, err
	}
	return Decision{Allow: out.Allowed, Reason: out.Reason, Miss: out.Miss}, nil
}

func (f *fallback) mintToken(tenant string) (string, error) {
	now := time.Now()
	claims := jwt.MapClaims{
		"sub": "svc:opaclient-fallback", "typ": "service", "tenant_id": tenant,
		"scopes": []string{"rbac.authz.check"},
		"iss":    f.cfg.Issuer, "aud": f.cfg.Audience,
		"iat": now.Unix(), "exp": now.Add(5 * time.Minute).Unix(),
		"jti": fmt.Sprintf("opa-fallback-%d", now.UnixNano()),
	}
	tok := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	if f.cfg.SigningKID != "" {
		tok.Header["kid"] = f.cfg.SigningKID
	}
	return tok.SignedString(f.key)
}

// withMissFallback re-checks against rbac-service's SQL ground truth when the
// Redis-projection decision came back as a miss. On any fallback failure
// (rbac-service unreachable, bad response, ...) it logs loudly and returns
// the ORIGINAL deny -- a degraded-mode signal an operator can alert on,
// never a silent security-posture change, and never worse than today's
// baseline (deny on miss).
func (c *Client) withMissFallback(ctx context.Context, in Input, dec Decision) Decision {
	if !dec.Miss || c.fb == nil {
		return dec
	}
	fallbackDec, err := c.fb.check(ctx, in)
	if err != nil {
		slog.Error("opaclient: rbac miss-fallback failed, denying (RBC-FR-045)",
			"err", err, "tenant", in.Tenant, "action", in.Action)
		return dec
	}
	slog.Info("opaclient: rbac miss-fallback resolved a cold projection key",
		"tenant", in.Tenant, "action", in.Action, "allow", fallbackDec.Allow)
	return fallbackDec
}
