// Package register performs deploy-time action-catalog registration (RBC-FR-022):
// audit-service pushes its action manifest to rbac's idempotent registration API
// at startup so the catalog OPA consumes knows every action this service
// authorizes against (`action_known`). In dev/e2e the service mints a short-lived
// service-typed JWT signed with the platform signing key.
package register

import (
	"bytes"
	"context"
	"crypto/rsa"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"

	"github.com/golang-jwt/jwt/v5"

	"github.com/windrose-ai/audit-service/internal/authz"
)

// Config carries the endpoint + credentials for the registration call.
type Config struct {
	RBACURL       string
	SigningKeyPEM string
	SigningKID    string
	Issuer        string
	Audience      string
	TenantID      string
}

// Register mints a service token and POSTs audit-service's action manifest to
// rbac /api/v1/actions/register. Idempotent; best-effort with retries.
func Register(ctx context.Context, cfg Config) error {
	if cfg.RBACURL == "" || cfg.SigningKeyPEM == "" {
		slog.Warn("action registration skipped (RBAC_URL or signing key unset)")
		return nil
	}
	key, err := jwt.ParseRSAPrivateKeyFromPEM([]byte(cfg.SigningKeyPEM))
	if err != nil {
		return fmt.Errorf("parse signing key: %w", err)
	}
	tok, err := mintServiceToken(key, cfg)
	if err != nil {
		return fmt.Errorf("mint service token: %w", err)
	}
	body, _ := json.Marshal(map[string]any{"actions": authz.Manifest()})
	url := cfg.RBACURL + "/api/v1/actions/register"
	var lastErr error
	for attempt := 0; attempt < 10; attempt++ {
		req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
		if err != nil {
			return err
		}
		req.Header.Set("Authorization", "Bearer "+tok)
		req.Header.Set("Content-Type", "application/json")
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			lastErr = err
		} else {
			raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
			_ = resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				slog.Info("action catalog registered with rbac", "actions", len(authz.Manifest()))
				return nil
			}
			lastErr = fmt.Errorf("rbac register status %d: %s", resp.StatusCode, string(raw))
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(time.Duration(attempt+1) * 500 * time.Millisecond):
		}
	}
	return lastErr
}

func mintServiceToken(key *rsa.PrivateKey, cfg Config) (string, error) {
	now := time.Now()
	claims := jwt.MapClaims{
		"sub":       "svc:audit-service",
		"typ":       "service",
		"tenant_id": cfg.TenantID,
		"scopes":    []string{"rbac.action.register"},
		"iss":       cfg.Issuer,
		"aud":       cfg.Audience,
		"iat":       now.Unix(),
		"exp":       now.Add(5 * time.Minute).Unix(),
		"jti":       fmt.Sprintf("audit-register-%d", now.UnixNano()),
	}
	tok := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	if cfg.SigningKID != "" {
		tok.Header["kid"] = cfg.SigningKID
	}
	return tok.SignedString(key)
}
