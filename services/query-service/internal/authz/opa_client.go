package authz

import (
	"context"
	"os"

	"github.com/windrose-ai/go-common/opaclient"
	"github.com/windrose-ai/go-common/redisx"
)

// OPAClient is the real runtime authorizer (MASTER-FR-012). Each decision (1)
// loads the caller's permissions_flat projection slice from Redis (the
// rbac-service key scheme, RBC-FR-040) and (2) POSTs it as input to the local
// OPA sidecar, which evaluates the windrose.authz_input Rego bundle. It never
// calls rbac-service synchronously in the request path. This is the shared
// libs/go-common implementation, so query-service's allow/deny is byte-for-byte
// the decision rbac's Go Decide returns for the same projection.
type OPAClient struct {
	client *opaclient.Client
	loader *opaclient.ProjectionLoader
}

// NewOPAClient builds the real authorizer over the OPA sidecar (opaURL, e.g.
// http://localhost:8281) and a Redis projection loader (redisAddr, host:port).
func NewOPAClient(opaURL, redisAddr string) *OPAClient {
	if opaURL == "" {
		opaURL = "http://localhost:8281"
	}
	return &OPAClient{
		client: opaclient.New(opaURL),
		loader: opaclient.NewLoader(redisx.NewFromEnv(redisAddr, os.Getenv)),
	}
}

// Allow loads the projection from Redis then evaluates it against OPA. It fails
// closed on any Redis/OPA transport error (MASTER-FR-012).
func (o *OPAClient) Allow(ctx context.Context, in Input) bool {
	oi := opaclient.Input{
		Subject: opaclient.Subject{
			ID:     in.Subject.ID,
			Typ:    in.Subject.Typ,
			OboSub: in.Subject.OboSub,
			Scopes: in.Subject.Scopes,
		},
		Action:      in.Action,
		ResourceURN: in.ResourceURN,
		Tenant:      in.Tenant,
		WorkspaceID: in.WorkspaceID,
	}
	dec, err := o.client.CheckWithRedis(ctx, o.loader, oi)
	if err != nil {
		return false
	}
	return dec.Allow
}
