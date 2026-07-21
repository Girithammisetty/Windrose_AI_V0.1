package authz

import (
	"context"
	"log/slog"
	"os"

	"github.com/windrose-ai/go-common/opaclient"
	"github.com/windrose-ai/go-common/redisx"
)

// OPA is the real runtime authorizer (MASTER-FR-012). Each decision loads the
// caller's permissions_flat projection from Redis (rbac's key scheme) and POSTs
// it to the local OPA sidecar evaluating the windrose.authz_input bundle. It
// never calls rbac synchronously. Uses the shared go-common implementation so
// the decision is byte-for-byte rbac's Go Decide for the same projection.
type OPA struct {
	client *opaclient.Client
	loader *opaclient.ProjectionLoader
}

// NewOPA builds the authorizer over the OPA sidecar (opaURL, e.g.
// http://localhost:8281) and a Redis projection loader (redisAddr host:port).
func NewOPA(opaURL, redisAddr string) *OPA {
	if opaURL == "" {
		opaURL = "http://localhost:8281"
	}
	client := opaclient.New(opaURL)
	// Redis-miss fallback (RBC-FR-045): a Redis restart/failover must not
	// deny the whole tenant until an operator manually runs
	// deploy/local/reconcile.sh — self-heal from rbac-service's real SQL
	// ground truth instead. Reuses the same credential the deploy-time
	// action-catalog registration already uses (RBAC_URL/REGISTER_SIGNING_*).
	if cfg, ok := opaclient.FallbackConfigFromEnv(); ok {
		if err := client.EnableMissFallback(cfg); err != nil {
			slog.Warn("opaclient: miss-fallback signing key invalid, Redis-miss will deny (RBC-FR-045)", "err", err)
		}
	}
	return &OPA{
		client: client,
		loader: opaclient.NewLoader(redisx.NewFromEnv(redisAddr, os.Getenv)),
	}
}

// Allow loads the projection then evaluates OPA. Fails closed on transport
// error (MASTER-FR-012).
func (o *OPA) Allow(ctx context.Context, in Input) bool {
	oi := opaclient.Input{
		Subject: opaclient.Subject{
			ID: in.Subject.ID, Typ: in.Subject.Typ, OboSub: in.Subject.OboSub, Scopes: in.Subject.Scopes,
		},
		Action:      in.Action,
		ResourceURN: in.ResourceURN,
		WorkspaceID: in.WorkspaceID,
		Tenant:      in.Tenant,
	}
	dec, err := o.client.CheckWithRedis(ctx, o.loader, oi)
	if err != nil {
		return false
	}
	return dec.Allow
}
