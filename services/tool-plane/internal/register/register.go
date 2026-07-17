// Package register implements deploy-time action-catalog registration
// (RBC-FR-022): tool-plane pushes its action manifest (admin actions +
// tool.tool.execute) to rbac's idempotent registration API at startup so the
// catalog OPA consumes knows every action this service authorizes against
// (`action_known`). In production this call carries the service's SPIFFE mTLS
// identity; in dev/e2e the service mints a short-lived service-typed JWT signed
// with the platform signing key. Both tool-plane binaries (cmd/registry and
// cmd/gateway) register; rbac upserts, so the double registration is harmless.
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
	"sync/atomic"
	"time"

	"github.com/golang-jwt/jwt/v5"

	"github.com/windrose-ai/tool-plane/internal/authz"
)

// Config carries the endpoint + credentials for the registration call.
type Config struct {
	RBACURL       string // e.g. http://localhost:8081
	SigningKeyPEM string // RS256 private key (PKCS1/PKCS8 PEM)
	SigningKID    string
	Issuer        string
	Audience      string
	TenantID      string // any valid tenant; the catalog is global
}

// Register mints a service token and POSTs tool-plane's action manifest to
// rbac /api/v1/actions/register. Idempotent (rbac upserts). Best-effort: a
// failure is logged, not fatal, but is surfaced via the returned error.
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

	type entry struct {
		Action          string `json:"action"`
		WorkspaceScoped bool   `json:"workspace_scoped"`
	}
	var actions []entry
	for _, e := range authz.Manifest() {
		actions = append(actions, entry{Action: e.Action, WorkspaceScoped: e.WorkspaceScoped})
	}
	body, _ := json.Marshal(map[string]any{"actions": actions})

	url := cfg.RBACURL + "/api/v1/actions/register"
	// Retry a few times: rbac may still be coming up when tool-plane boots.
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
				slog.Info("action catalog registered with rbac", "actions", len(actions))
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
		"sub":       "svc:tool-plane",
		"typ":       "service",
		"tenant_id": cfg.TenantID,
		"scopes":    []string{"rbac.action.register"},
		"iss":       cfg.Issuer,
		"aud":       cfg.Audience,
		"iat":       now.Unix(),
		"exp":       now.Add(5 * time.Minute).Unix(),
		"jti":       fmt.Sprintf("tool-plane-register-%d", now.UnixNano()),
	}
	tok := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	if cfg.SigningKID != "" {
		tok.Header["kid"] = cfg.SigningKID
	}
	return tok.SignedString(key)
}

// Status is the readiness gate for action registration (fail loudly): while a
// CONFIGURED registration is pending or has failed, the owning binary's /readyz
// returns 503 with the reason, so a deploy where rbac never learned this
// service's actions (→ every authorize would be unknown_action deny) cannot be
// marked healthy. When registration is unconfigured (dev mode) the status stays
// ready.
type Status struct {
	// reason holds the not-ready reason; empty string = ready.
	reason atomic.Value
}

// NewStatus returns a Status that is initially ready (dev-mode default).
func NewStatus() *Status {
	s := &Status{}
	s.reason.Store("")
	return s
}

// Ready reports readiness and, when not ready, the reason.
func (s *Status) Ready() (bool, string) {
	r, _ := s.reason.Load().(string)
	return r == "", r
}

func (s *Status) set(reason string) { s.reason.Store(reason) }

// RunAsync performs registration in the background, gating st until it
// succeeds. Unconfigured (no RBAC_URL / signing key) → skip with a warn and
// stay ready (dev mode). On final failure it logs at ERROR and leaves the
// status not-ready so /readyz serves 503 with the reason.
func RunAsync(ctx context.Context, cfg Config, st *Status) {
	if cfg.RBACURL == "" || cfg.SigningKeyPEM == "" {
		slog.Warn("action registration skipped (RBAC_URL or signing key unset); staying ready (dev mode)")
		return
	}
	st.set("action registration with rbac pending")
	go func() {
		for {
			err := Register(ctx, cfg)
			if err == nil {
				st.set("")
				return
			}
			slog.Error("action catalog registration failed; /readyz reports 503 until registration succeeds", "err", err)
			st.set("action registration with rbac failed: " + err.Error())
			select {
			case <-ctx.Done():
				return
			case <-time.After(30 * time.Second):
			}
		}
	}()
}
