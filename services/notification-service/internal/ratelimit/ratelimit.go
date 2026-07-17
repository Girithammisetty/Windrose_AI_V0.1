// Package ratelimit implements the per-recipient and per-tenant rate limits
// (NOTIF-FR-031/032) against real Redis. Email breaches convert to the digest
// path (never dropped, BR-2); webhooks use a per-endpoint token bucket.
package ratelimit

import (
	"context"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/windrose-ai/go-common/redisx"
)

// Limiter holds the tunable caps.
type Limiter struct {
	r *redisx.Client
	// EmailPerHour is the per-user immediate-email cap (default 20).
	EmailPerHour int
	// TenantEmailPerDay is the per-tenant daily email budget (default 10000).
	TenantEmailPerDay int
	// WebhookPerSec is the per-endpoint token-bucket rate (default 50).
	WebhookPerSec int
}

// New builds a Limiter with BRD defaults.
func New(r *redisx.Client) *Limiter {
	return &Limiter{r: r, EmailPerHour: 20, TenantEmailPerDay: 10000, WebhookPerSec: 50}
}

// AllowEmail increments the user's hourly email counter and reports whether the
// send stays within the immediate cap. On false the caller converts to digest
// (NOTIF-FR-031, AC-9).
func (l *Limiter) AllowEmail(ctx context.Context, tenant, user string) (bool, error) {
	key := fmt.Sprintf("rl:email:%s:%s:%d", tenant, user, time.Now().Unix()/3600)
	n, err := l.r.R.Incr(ctx, key).Result()
	if err != nil {
		return false, err
	}
	if n == 1 {
		_ = l.r.R.Expire(ctx, key, time.Hour).Err()
	}
	return int(n) <= l.EmailPerHour, nil
}

// AllowTenantEmail increments the tenant's daily email budget and reports
// whether it remains within cap (NOTIF-FR-032). Critical-class sends bypass.
func (l *Limiter) AllowTenantEmail(ctx context.Context, tenant string) (bool, error) {
	key := fmt.Sprintf("rl:tbudget:%s:%s", tenant, time.Now().UTC().Format("20060102"))
	n, err := l.r.R.Incr(ctx, key).Result()
	if err != nil {
		return false, err
	}
	if n == 1 {
		_ = l.r.R.Expire(ctx, key, 48*time.Hour).Err()
	}
	return int(n) <= l.TenantEmailPerDay, nil
}

// tokenBucket is a Redis Lua token-bucket refilling at rate/sec with burst=rate.
var tokenBucket = redis.NewScript(`
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local now = tonumber(ARGV[2])
local burst = rate
local tokens = tonumber(redis.call('HGET', key, 'tokens') or burst)
local ts = tonumber(redis.call('HGET', key, 'ts') or now)
local delta = math.max(0, now - ts) * rate
tokens = math.min(burst, tokens + delta)
local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end
redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, 60)
return allowed
`)

// AllowWebhook consumes one token from the endpoint's bucket (NOTIF-FR-031).
func (l *Limiter) AllowWebhook(ctx context.Context, endpointID string) (bool, error) {
	key := "rl:wh:" + endpointID
	res, err := tokenBucket.Run(ctx, l.r.R, []string{key}, l.WebhookPerSec, float64(time.Now().UnixNano())/1e9).Int()
	if err != nil {
		return false, err
	}
	return res == 1, nil
}
