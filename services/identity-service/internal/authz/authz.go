// Package authz holds the authorization port. Production decisions go to the
// local OPA sidecar over the rbac-service permissions_flat projection
// (MASTER-FR-012), exactly like every other Go service: the request path reads
// the caller's projection slice from Redis (rbac's RBC-FR-040 key scheme) and
// POSTs it to OPA, which evaluates the shared windrose.authz_input Rego bundle.
// The ScopeAuthorizer (token-scope enforcement) remains only as a documented
// dev/test fallback when no OPA sidecar is configured.
package authz

import (
	"context"
	"os"

	"github.com/windrose-ai/go-common/opaclient"
	"github.com/windrose-ai/go-common/redisx"

	"github.com/windrose-ai/identity-service/internal/domain"
)

// Authorizer decides whether a principal may perform an action on a resource.
type Authorizer interface {
	Allow(ctx context.Context, claims *domain.Claims, action, resourceURN string) bool
}

// ScopeAuthorizer allows an action when the token carries it as a scope
// (MASTER-FR-016 naming) or the platform.admin super-scope (IDN-FR-025). This
// is the dev/test fallback: it authorizes off the token, NOT the rbac
// projection, so a tenant admin's `*` grant does not reach it. Prefer
// OPAAuthorizer wherever an OPA sidecar + Redis projection are available.
type ScopeAuthorizer struct{}

func (ScopeAuthorizer) Allow(_ context.Context, c *domain.Claims, action, _ string) bool {
	return c.HasScope(action)
}

// OPAAuthorizer is the real runtime authorizer (MASTER-FR-012). Each decision
// (1) loads the caller's permissions_flat projection slice from Redis (the
// rbac-service key scheme, RBC-FR-040) via the shared go-common ProjectionLoader
// and (2) POSTs it as `input` to the local OPA sidecar, which evaluates the
// windrose.authz_input Rego bundle. The decision is byte-for-byte the one rbac's
// Go Decide returns for the same projection — so a tenant Admin's projection
// admin flag (BR-7) authorizes identity's guarded actions (identity.user.admin,
// identity.service_account.admin, identity.credential.read, identity.tenant.*)
// WITHOUT the scope being baked into the token. It never calls rbac-service
// synchronously in the request path and fails closed on any transport error.
//
// Identity's guarded actions are tenant-scoped (workspace_scoped=false in the
// catalog), so no workspace_id is threaded; the admin short-circuit in the Rego
// requires the action to be catalog-known, so identity registers those actions
// with rbac at boot (see rbacclient.Registrar).
type OPAAuthorizer struct {
	client *opaclient.Client
	loader *opaclient.ProjectionLoader
}

// NewOPAAuthorizer builds the real authorizer over the OPA sidecar (opaURL,
// e.g. http://localhost:8281) and a Redis projection loader (redisAddr,
// host:port).
func NewOPAAuthorizer(opaURL, redisAddr string) *OPAAuthorizer {
	if opaURL == "" {
		opaURL = "http://localhost:8281"
	}
	return &OPAAuthorizer{
		client: opaclient.New(opaURL),
		loader: opaclient.NewLoader(redisx.NewFromEnv(redisAddr, os.Getenv)),
	}
}

func (o *OPAAuthorizer) Allow(ctx context.Context, c *domain.Claims, action, resourceURN string) bool {
	// Platform super-admin (platform.admin scope) bypasses tenant-scoped rbac,
	// mirroring ScopeAuthorizer + IDN-FR-025 (the platform realm has no tenant
	// projection). Every other principal authorizes off the projection.
	if c.IsSuperAdmin() {
		return true
	}
	in := opaclient.Input{
		Subject: opaclient.Subject{
			ID: c.Subject, Typ: c.Typ, OboSub: c.OBOSub, Scopes: c.Scopes,
		},
		Action:      action,
		ResourceURN: resourceURN,
		Tenant:      c.TenantID.String(),
	}
	dec, err := o.client.CheckWithRedis(ctx, o.loader, in)
	if err != nil {
		return false // fail closed (MASTER-FR-012)
	}
	return dec.Allow
}
