package enforce

import (
	"context"
	"math"

	"github.com/redis/go-redis/v9"

	"github.com/windrose-ai/go-common/redisx"
)

// RateLimiter enforces true token buckets in REAL Redis at (tenant × tool) and
// (agent_principal × tool) granularity (TPL-FR-033/BR-6). The bucket has capacity
// = the per-minute limit and refills continuously at limit/60 tokens per second,
// evaluated atomically server-side (Lua + Redis TIME), so it is correct across
// gateway replicas sharing one Redis and does not double-allow across a minute
// boundary the way a fixed-window counter would.
type RateLimiter struct {
	r *redisx.Client
}

// NewRateLimiter builds a limiter over Redis.
func NewRateLimiter(r *redisx.Client) *RateLimiter { return &RateLimiter{r: r} }

// tokenBucket atomically refills and consumes one token, returning
// {allowed, retry_after_seconds}. State is a Redis hash {tokens, ts}; the server
// clock (Redis TIME) drives refill so no client clock is trusted.
var tokenBucket = redis.NewScript(`
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = redis.call('TIME')
local now_s = tonumber(now[1]) + tonumber(now[2]) / 1000000
local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then
  tokens = capacity
  ts = now_s
end
local delta = now_s - ts
if delta < 0 then delta = 0 end
tokens = math.min(capacity, tokens + delta * rate)
local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end
redis.call('HSET', key, 'tokens', tokens, 'ts', now_s)
redis.call('EXPIRE', key, 120)
local retry = 0
if allowed == 0 then
  retry = math.ceil((1 - tokens) / rate)
end
return {allowed, retry}
`)

// Allow consumes one token from the bucket named key with capacity limit (per
// minute). Returns allowed and, when denied, retry-after seconds until a token
// refills (TPL-FR-033 RATE_LIMITED + retry_after).
func (rl *RateLimiter) Allow(ctx context.Context, key string, limit int) (bool, int, error) {
	if limit <= 0 {
		limit = 1
	}
	rate := float64(limit) / 60.0
	res, err := tokenBucket.Run(ctx, rl.r.R, []string{"tp:rl:" + key}, limit, rate).Result()
	if err != nil {
		return false, 0, err
	}
	arr, ok := res.([]any)
	if !ok || len(arr) != 2 {
		return false, 0, nil
	}
	allowed, _ := arr[0].(int64)
	retry, _ := arr[1].(int64)
	if allowed == 1 {
		return true, 0, nil
	}
	if retry <= 0 {
		retry = 1
	}
	return false, int(retry), nil
}

// RateForWeight maps a tool cost weight (1..10) to a default per-minute cap
// (TPL-FR-033: weight 1 → 120/min, weight 10 → 6/min), geometric between the two
// declared anchors so both endpoints match exactly.
func RateForWeight(weight int) int {
	if weight <= 1 {
		return 120
	}
	if weight >= 10 {
		return 6
	}
	// 120 * (6/120)^((w-1)/9)
	r := 120.0 * math.Pow(6.0/120.0, float64(weight-1)/9.0)
	return int(math.Round(r))
}
