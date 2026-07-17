package fanout

import (
	"context"
	"sync/atomic"
	"time"

	"github.com/redis/go-redis/v9"
)

// Lease is a Redis leader lease (RTH-FR-042): exactly one pod per Kafka source
// group writes the replay buffer and republishes Kafka events to the pub/sub
// bus, avoiding duplicate ring entries and double live-delivery. Held via
// SET NX PX with periodic renew; lost on pod death (key TTL expiry).
type Lease struct {
	rdb    redis.UniversalClient
	key    string
	holder string
	ttl    time.Duration
	held   atomic.Bool
}

// NewLease builds a lease for a named resource, owned by holder (pod id).
func NewLease(rdb redis.UniversalClient, resource, holder string) *Lease {
	return &Lease{rdb: rdb, key: "rt:leader:" + resource, holder: holder, ttl: 5 * time.Second}
}

// IsLeader reports whether this pod currently holds the lease.
func (l *Lease) IsLeader() bool { return l.held.Load() }

// Run campaigns for and renews the lease until ctx is cancelled.
func (l *Lease) Run(ctx context.Context) {
	t := time.NewTicker(l.ttl / 2)
	defer t.Stop()
	l.tryAcquire(ctx)
	for {
		select {
		case <-ctx.Done():
			l.release(context.Background())
			return
		case <-t.C:
			l.tryAcquire(ctx)
		}
	}
}

func (l *Lease) tryAcquire(ctx context.Context) {
	if l.held.Load() {
		// Renew only if we still own it (value match).
		if renewLease(ctx, l.rdb, l.key, l.holder, l.ttl) {
			return
		}
		l.held.Store(false)
	}
	ok, err := l.rdb.SetNX(ctx, l.key, l.holder, l.ttl).Result()
	if err == nil && ok {
		l.held.Store(true)
	}
}

func (l *Lease) release(ctx context.Context) {
	if !l.held.Load() {
		return
	}
	// Delete only if we own it.
	_ = releaseScript.Run(ctx, l.rdb, []string{l.key}, l.holder).Err()
	l.held.Store(false)
}

var renewScript = redis.NewScript(`
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("PEXPIRE", KEYS[1], ARGV[2])
else
  return 0
end`)

var releaseScript = redis.NewScript(`
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
else
  return 0
end`)

func renewLease(ctx context.Context, rdb redis.UniversalClient, key, holder string, ttl time.Duration) bool {
	n, err := renewScript.Run(ctx, rdb, []string{key}, holder, ttl.Milliseconds()).Int()
	return err == nil && n == 1
}
