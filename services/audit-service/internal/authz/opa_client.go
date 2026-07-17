package authz

import (
	"context"
	"os"

	"github.com/windrose-ai/go-common/opaclient"
	"github.com/windrose-ai/go-common/redisx"
)

// OPAClient is the real runtime authorizer (MASTER-FR-012): each decision loads
// the caller's permissions_flat projection from Redis (rbac key scheme,
// RBC-FR-040) and POSTs it to the local OPA sidecar's windrose.authz_input
// bundle. It fails closed on any transport error. This is the same shared
// libs/go-common implementation every service uses, so audit-service's
// allow/deny is byte-for-byte rbac's decision for the same projection.
type OPAClient struct {
	client *opaclient.Client
	loader *opaclient.ProjectionLoader
}

// NewOPAClient builds the real authorizer over the OPA sidecar (opaURL) and a
// Redis projection loader (redisAddr).
func NewOPAClient(opaURL, redisAddr string) *OPAClient {
	if opaURL == "" {
		opaURL = "http://localhost:8281"
	}
	return &OPAClient{
		client: opaclient.New(opaURL),
		loader: opaclient.NewLoader(redisx.NewFromEnv(redisAddr, os.Getenv)),
	}
}

// Allow loads the projection then evaluates it against OPA; fails closed.
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
	}
	dec, err := o.client.CheckWithRedis(ctx, o.loader, oi)
	if err != nil {
		return false
	}
	return dec.Allow
}
