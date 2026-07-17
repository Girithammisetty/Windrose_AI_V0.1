package projection

import (
	"context"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// UserLock is the RBC-FR-048 per-user recompute mutex. SKIP-LOCKED dirty-row
// claiming stops two workers from claiming the SAME dirty rows, but a user can
// have rows enqueued at different times claimed by different workers; without a
// per-user lock those two recomputes can interleave load/write and the older
// snapshot's write can land last. This lock serializes recompute per user, and
// callers MUST load their snapshot AFTER acquiring it so the snapshot version
// is monotonic with respect to the write order.
type UserLock struct {
	rdb redis.UniversalClient
	ttl time.Duration
}

// DefaultLockTTL bounds a crashed holder's blast radius; must exceed a normal
// recompute (snapshot load + Redis writes).
const DefaultLockTTL = 30 * time.Second

func NewUserLock(rdb redis.UniversalClient, ttl time.Duration) *UserLock {
	if ttl <= 0 {
		ttl = DefaultLockTTL
	}
	return &UserLock{rdb: rdb, ttl: ttl}
}

func userLockKey(tenant, user string) string {
	return "perm:lock:" + tenant + ":" + user
}

// Acquire tries once to take the lock, returning a fencing token on success.
func (l *UserLock) Acquire(ctx context.Context, tenant, user string) (token string, ok bool, err error) {
	token = uuid.NewString()
	ok, err = l.rdb.SetNX(ctx, userLockKey(tenant, user), token, l.ttl).Result()
	if err != nil || !ok {
		return "", ok, err
	}
	return token, true, nil
}

// AcquireWait retries Acquire until it succeeds, ctx is done, or the budget is
// exhausted (returns ok=false so the caller leaves the work for a later pass).
func (l *UserLock) AcquireWait(ctx context.Context, tenant, user string, budget time.Duration) (token string, ok bool, err error) {
	deadline := time.Now().Add(budget)
	for {
		token, ok, err = l.Acquire(ctx, tenant, user)
		if err != nil || ok {
			return token, ok, err
		}
		if time.Now().After(deadline) {
			return "", false, nil
		}
		select {
		case <-ctx.Done():
			return "", false, ctx.Err()
		case <-time.After(25 * time.Millisecond):
		}
	}
}

// releaseScript deletes the lock only if the caller still holds the token, so a
// slow holder whose lock already expired cannot release a successor's lock.
var releaseScript = redis.NewScript(`
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
`)

// Release drops the lock if still held by token. Best-effort: expiry also frees it.
func (l *UserLock) Release(ctx context.Context, tenant, user, token string) {
	if token == "" {
		return
	}
	_ = releaseScript.Run(ctx, l.rdb, []string{userLockKey(tenant, user)}, token).Err()
}
