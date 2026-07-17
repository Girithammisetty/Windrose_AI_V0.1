package authz

import (
	"context"
	"os"

	"github.com/windrose-ai/go-common/opaclient"
	"github.com/windrose-ai/go-common/redisx"
)

// AdminInput is one control-plane authorization question for the tool-registry
// admin API (MASTER-FR-012/016). This is distinct from Input, which is the
// gateway pipeline's normative per-invocation document (BRD 13 §3) evaluated by
// the windrose.tool_plane policy: admin routes are authorized exactly like
// chart-/case-service routes — against the rbac permissions_flat projection via
// the windrose.authz_input bundle in the local OPA sidecar.
type AdminInput struct {
	Subject     AdminSubject
	Action      string
	ResourceURN string
	Tenant      string
}

// AdminSubject describes the admin-API caller.
type AdminSubject struct {
	ID     string
	Typ    string
	OboSub string
	Scopes []string
}

// AdminAuthorizer answers allow/deny for admin routes. The real runtime
// implementation is *AdminOPA; the permissive/static doubles are test-only.
type AdminAuthorizer interface {
	Allow(ctx context.Context, in AdminInput) bool
}

// AdminOPA is the real runtime admin authorizer (MASTER-FR-012). Each decision
// loads the caller's permissions_flat projection slice from Redis (rbac's key
// scheme) and POSTs it to the local OPA sidecar evaluating the
// windrose.authz_input bundle. It never calls rbac synchronously and fails
// closed on any transport error.
type AdminOPA struct {
	client *opaclient.Client
	loader *opaclient.ProjectionLoader
}

// NewAdminOPA builds the admin authorizer over the OPA sidecar (opaURL, e.g.
// http://localhost:8281) and a Redis projection loader (redisAddr host:port).
func NewAdminOPA(opaURL, redisAddr string) *AdminOPA {
	if opaURL == "" {
		opaURL = "http://localhost:8281"
	}
	return &AdminOPA{
		client: opaclient.New(opaURL),
		loader: opaclient.NewLoader(redisx.NewFromEnv(redisAddr, os.Getenv)),
	}
}

// Allow loads the projection then evaluates OPA. Fails closed on transport
// error (MASTER-FR-012). Tool-plane admin actions are tenant-scoped, so no
// workspace id is carried.
func (o *AdminOPA) Allow(ctx context.Context, in AdminInput) bool {
	oi := opaclient.Input{
		Subject: opaclient.Subject{
			ID: in.Subject.ID, Typ: in.Subject.Typ, OboSub: in.Subject.OboSub, Scopes: in.Subject.Scopes,
		},
		Action:      in.Action,
		ResourceURN: in.ResourceURN,
		Tenant:      in.Tenant,
	}
	dec, err := o.client.CheckWithRedis(ctx, o.loader, oi)
	if err != nil {
		return false
	}
	return dec.Allow
}

// AdminAllowAll is the permissive unit-test double (never wired from cmd/*).
type AdminAllowAll struct{}

// Allow always permits.
func (AdminAllowAll) Allow(context.Context, AdminInput) bool { return true }

// AdminStatic denies the listed actions and allows the rest (authz-matrix unit
// double).
type AdminStatic struct{ Denied map[string]bool }

// Allow permits unless the action is in Denied.
func (s AdminStatic) Allow(_ context.Context, in AdminInput) bool { return !s.Denied[in.Action] }
