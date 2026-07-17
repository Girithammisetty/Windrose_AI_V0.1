// Package rbacclient implements domain.LastAdminChecker (BR-9: a tenant's
// last admin cannot be deactivated) against the REAL rbac-service HTTP API.
//
// rbac has no "list admins of tenant" read, but its service-callable decision
// endpoint POST /api/v1/authz/check (RequireServiceOrSuperAdmin) returns the
// decision *reason*: a tenant admin short-circuits every action check with
// reason "admin_bypass" (BR-7). So "is user X a tenant admin" ==
// check(subject=X, any known tenant-scoped action) → allowed && admin_bypass.
//
// IsLastAdmin(t, u) therefore:
//  1. asks rbac whether u is an admin — if not, u cannot be the last admin;
//  2. lists the tenant's other ACTIVE users from the identity store and asks
//     rbac the same question for each — the first other admin proves u is not
//     the last one.
//
// Identity holds the platform signing keys, so each call carries a
// short-lived RS256 service token (typ "service") minted via the same Issuer
// that signs every platform JWT; rbac verifies it against identity's JWKS.
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

	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/domain"
)

// probeAction is a canonical tenant-scoped action from rbac's own seeded
// catalog. Its only job is to be a *known* action so the decision reaches the
// admin short-circuit; we classify on reason=="admin_bypass", never on the
// action being role-granted.
const probeAction = "rbac.role.create"

// reasonAdminBypass mirrors rbac's authz.ReasonAdminBypass (BR-7 admin flag).
const reasonAdminBypass = "admin_bypass"

// Checker is the real BR-9 guard. All fields except HTTP and Log are required.
type Checker struct {
	BaseURL string             // rbac-service base URL, e.g. http://localhost:8081
	Store   domain.Store       // identity's own user directory (candidate admins)
	Issuer  domain.TokenIssuer // mints the service token (platform signing key)
	HTTP    *http.Client
	Log     *slog.Logger
}

var _ domain.LastAdminChecker = (*Checker)(nil)

func (c *Checker) client() *http.Client {
	if c.HTTP != nil {
		return c.HTTP
	}
	return &http.Client{Timeout: 5 * time.Second}
}

// IsLastAdmin reports whether userID is the tenant's only active admin.
// Errors (rbac unreachable, token mint failure) propagate: BR-9 fails closed
// rather than silently allowing the last admin to be deactivated.
func (c *Checker) IsLastAdmin(ctx context.Context, tenantID, userID uuid.UUID) (bool, error) {
	token, _, err := c.Issuer.Issue(domain.Claims{
		Subject: "svc:identity-service", TenantID: tenantID, Typ: domain.TypService, Scopes: []string{},
	})
	if err != nil {
		return false, fmt.Errorf("mint rbac service token: %w", err)
	}

	// If the user being deactivated is not an admin, BR-9 does not apply.
	admin, err := c.isAdmin(ctx, token, tenantID, userID.String())
	if err != nil {
		return false, err
	}
	if !admin {
		return false, nil
	}

	// Look for any OTHER active admin in the tenant.
	page := domain.PageRequest{Limit: domain.MaxPageLimit}
	for {
		users, info, err := c.Store.ListUsers(ctx, tenantID, domain.UserFilter{}, page)
		if err != nil {
			return false, err
		}
		for _, u := range users {
			if u.ID == userID || u.Status != domain.UserActive || u.DeletedAt != nil {
				continue
			}
			otherAdmin, err := c.isAdmin(ctx, token, tenantID, u.ID.String())
			if err != nil {
				return false, err
			}
			if otherAdmin {
				return false, nil // another active admin exists
			}
		}
		if !info.HasMore || info.NextCursor == nil {
			break
		}
		next, err := domain.ParsePage("", *info.NextCursor)
		if err != nil {
			return false, err
		}
		page.AfterID = next.AfterID
	}
	return true, nil
}

// checkDecision is rbac's POST /api/v1/authz/check response body.
type checkDecision struct {
	Allowed bool   `json:"allowed"`
	Reason  string `json:"reason"`
}

// isAdmin asks rbac whether subject userID carries the tenant admin flag.
func (c *Checker) isAdmin(ctx context.Context, token string, tenantID uuid.UUID, userID string) (bool, error) {
	body, _ := json.Marshal(map[string]any{
		"subject": map[string]any{"id": userID, "typ": domain.TypUser},
		"action":  probeAction,
		"tenant":  tenantID.String(),
	})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		c.BaseURL+"/api/v1/authz/check", bytes.NewReader(body))
	if err != nil {
		return false, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.client().Do(req)
	if err != nil {
		return false, fmt.Errorf("rbac authz/check: %w", err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	if resp.StatusCode != http.StatusOK {
		return false, fmt.Errorf("rbac authz/check: status %d: %s", resp.StatusCode, string(raw))
	}
	var d checkDecision
	if err := json.Unmarshal(raw, &d); err != nil {
		return false, fmt.Errorf("rbac authz/check: decode: %w", err)
	}
	if c.Log != nil {
		c.Log.Debug("rbac last-admin probe", "tenant", tenantID, "user", userID,
			"allowed", d.Allowed, "reason", d.Reason)
	}
	return d.Allowed && d.Reason == reasonAdminBypass, nil
}
