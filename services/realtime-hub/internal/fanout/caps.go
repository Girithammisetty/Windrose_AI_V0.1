package fanout

import (
	"context"
	"time"

	"github.com/redis/go-redis/v9"
)

// Connection caps (RTH-FR-040), configurable per cell.
const (
	DefaultPerUser   = 10
	DefaultPerTenant = 2000
	DefaultPerPod    = 50000
)

// Caps enforces per-user and per-tenant connection limits cell-wide using real
// Redis counters (INCR on connect / DECR on disconnect), so the caps hold
// across pods with no session affinity (RTH-FR-040/041). Per-pod is enforced
// locally by the hub.
type Caps struct {
	rdb       redis.UniversalClient
	perUser   int64
	perTenant int64
}

// NewCaps builds a cap enforcer.
func NewCaps(rdb redis.UniversalClient, perUser, perTenant int) *Caps {
	if perUser <= 0 {
		perUser = DefaultPerUser
	}
	if perTenant <= 0 {
		perTenant = DefaultPerTenant
	}
	return &Caps{rdb: rdb, perUser: int64(perUser), perTenant: int64(perTenant)}
}

func userKey(tenant, user string) string { return "rt:cc:user:" + tenant + "/" + user }
func tenantKey(tenant string) string      { return "rt:cc:tenant:" + tenant }

// LimitKind identifies which cap was hit.
type LimitKind string

const (
	LimitNone   LimitKind = ""
	LimitUser   LimitKind = "user"
	LimitTenant LimitKind = "tenant"
)

// Reserve atomically claims one connection slot for (tenant, user). It returns
// LimitNone on success (caller must Release on disconnect), or the kind of cap
// that was exceeded (nothing is reserved in that case).
func (c *Caps) Reserve(ctx context.Context, tenant, user string) (LimitKind, error) {
	// Tenant counter first.
	tn, err := c.rdb.Incr(ctx, tenantKey(tenant)).Result()
	if err != nil {
		return LimitNone, err
	}
	c.rdb.Expire(ctx, tenantKey(tenant), 24*time.Hour)
	if tn > c.perTenant {
		c.rdb.Decr(ctx, tenantKey(tenant))
		return LimitTenant, nil
	}
	un, err := c.rdb.Incr(ctx, userKey(tenant, user)).Result()
	if err != nil {
		c.rdb.Decr(ctx, tenantKey(tenant))
		return LimitNone, err
	}
	c.rdb.Expire(ctx, userKey(tenant, user), 24*time.Hour)
	if un > c.perUser {
		c.rdb.Decr(ctx, userKey(tenant, user))
		c.rdb.Decr(ctx, tenantKey(tenant))
		return LimitUser, nil
	}
	return LimitNone, nil
}

// Release returns a reserved slot on disconnect.
func (c *Caps) Release(ctx context.Context, tenant, user string) {
	c.rdb.Decr(ctx, userKey(tenant, user))
	c.rdb.Decr(ctx, tenantKey(tenant))
}
