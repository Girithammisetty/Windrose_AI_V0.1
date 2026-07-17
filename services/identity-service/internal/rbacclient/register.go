package rbacclient

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"

	"github.com/windrose-ai/identity-service/internal/domain"
)

// actionDef mirrors rbac-service domain.ActionDef's JSON shape (RBC-FR-022).
// Identity's guarded actions are all TENANT-scoped (WorkspaceScoped=false): the
// OPA admin short-circuit (windrose.authz_input) requires ctx_ok, which for a
// non-workspace action means no workspace_id is threaded — exactly how
// identity's requireScope middleware calls the authorizer.
type actionDef struct {
	Action          string `json:"action"`
	Service         string `json:"service"`
	Resource        string `json:"resource"`
	Verb            string `json:"verb"`
	WorkspaceScoped bool   `json:"workspace_scoped"`
	Description     string `json:"description,omitempty"`
}

// GuardedActions are the action strings identity's API gates on
// (internal/api ActUserAdmin/ActSvcAcctAdmin/ActCredentialRead/ActTenantAdmin).
// They MUST be catalog-known in rbac or the OPA admin short-circuit denies them
// with reason "unknown_action" even for a tenant Admin. identity.tenant.admin is
// already in rbac's canonical catalog; the other three are identity-owned
// additions registered here (and, durably, in rbac's catalog.go — see report).
var GuardedActions = []actionDef{
	{"identity.tenant.admin", "identity", "tenant", "admin", false, "tenant administration (platform/tenant admin)"},
	{"identity.user.admin", "identity", "user", "admin", false, "tenant user directory administration"},
	{"identity.service_account.admin", "identity", "service_account", "admin", false, "service account / API key administration"},
	{"identity.credential.read", "identity", "credential", "read", false, "read tenant credential material"},
}

// Registrar idempotently registers identity's guarded actions with rbac's
// deploy-time catalog API (POST /api/v1/actions/register), which upserts the
// entries and refreshes the Redis catalog key the OPA projection reads. It
// authenticates with a short-lived identity-signed service token (rbac verifies
// it against identity's JWKS), the same mechanism the last-admin Checker uses.
type Registrar struct {
	BaseURL string             // rbac-service base URL
	Issuer  domain.TokenIssuer // mints the service token (platform signing key)
	HTTP    *http.Client
	Log     *slog.Logger
}

func (r *Registrar) client() *http.Client {
	if r.HTTP != nil {
		return r.HTTP
	}
	return &http.Client{Timeout: 5 * time.Second}
}

// register performs one registration attempt.
func (r *Registrar) register(ctx context.Context) error {
	token, _, err := r.Issuer.Issue(domain.Claims{
		Subject: "svc:identity-service", Typ: domain.TypService, Scopes: []string{},
	})
	if err != nil {
		return fmt.Errorf("mint rbac service token: %w", err)
	}
	body, _ := json.Marshal(map[string]any{"actions": GuardedActions})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		r.BaseURL+"/api/v1/actions/register", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := r.client().Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("rbac actions/register: status %d: %s", resp.StatusCode, string(raw))
	}
	return nil
}

// Run registers the guarded actions, retrying with backoff until success or ctx
// cancellation. It is launched in a goroutine at boot because identity starts
// BEFORE rbac in the boot order: a hard fail-loud os.Exit would deadlock the
// boot. Instead every failed attempt logs loudly (WARN) and, after the retry
// budget is exhausted, an ERROR — so a genuinely-broken registration is never
// silent, while a not-yet-ready rbac simply retries.
func (r *Registrar) Run(ctx context.Context) {
	const maxAttempts = 30
	backoff := 2 * time.Second
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		if err := r.register(ctx); err != nil {
			if r.Log != nil {
				r.Log.Warn("rbac action registration attempt failed (will retry)",
					"attempt", attempt, "max", maxAttempts, "error", err)
			}
			select {
			case <-ctx.Done():
				return
			case <-time.After(backoff):
			}
			if backoff < 30*time.Second {
				backoff *= 2
			}
			continue
		}
		if r.Log != nil {
			r.Log.Info("rbac action registration succeeded",
				"actions", len(GuardedActions), "rbac", r.BaseURL)
		}
		return
	}
	if r.Log != nil {
		r.Log.Error("rbac action registration FAILED after all retries — identity's guarded "+
			"actions are not catalog-known; OPA will deny them with unknown_action",
			"rbac", r.BaseURL)
	}
}
