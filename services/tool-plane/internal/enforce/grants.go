package enforce

import (
	"context"
	"encoding/json"

	"github.com/windrose-ai/go-common/opaclient"
	"github.com/windrose-ai/go-common/redisx"
)

// GrantLoader resolves which of a set of resource URNs an OBO user is granted
// (TPL-FR-032: agent toolset ∩ OBO user grant on affected resources). The real
// impl reads the rbac-service permissions_flat projection from Redis — the same
// per-resource grant keys go-common/opaclient uses — so the intersection is
// evaluated from real projection state, never a synchronous rbac call (BR-13).
type GrantLoader interface {
	GrantsFor(ctx context.Context, tenant, user string, urns []string) ([]string, error)
}

// RedisGrantLoader reads resource grants from the rbac Redis projection.
type RedisGrantLoader struct {
	R *redisx.Client
}

// NewRedisGrantLoader builds a loader over Redis.
func NewRedisGrantLoader(r *redisx.Client) *RedisGrantLoader { return &RedisGrantLoader{R: r} }

// GrantsFor returns the subset of urns the user holds a (non-tombstoned) grant
// on, using the rbac key scheme perm:{tenant}:{user}:res:{sha256(urn)[:32]}.
func (l *RedisGrantLoader) GrantsFor(ctx context.Context, tenant, user string, urns []string) ([]string, error) {
	var granted []string
	for _, urn := range urns {
		h := opaclient.URNHash(urn)
		raw, ok, err := l.R.Get(ctx, "perm:"+tenant+":"+user+":res:"+h)
		if err != nil {
			return nil, err
		}
		if !ok {
			continue
		}
		var rv struct {
			Level   string `json:"level"`
			Deleted bool   `json:"deleted"`
		}
		if json.Unmarshal([]byte(raw), &rv) == nil && !rv.Deleted && rv.Level != "" {
			granted = append(granted, urn)
		}
	}
	return granted, nil
}
