package domain

import (
	"math"
	"sync"
	"time"
)

// OBORateLimit per IDN-FR-044 / AC-14: 60 issuances per minute per (user, agent).
const (
	OBORateLimit  = 60
	OBORateWindow = time.Minute
)

// RateLimiter is the issuance rate limiter. The in-memory sliding window
// below is per-instance; a Redis-backed implementation slots in behind this
// interface for multi-replica deployments (documented adapter).
type RateLimiter interface {
	// Allow records an attempt for key and reports whether it is within the
	// limit; when denied it returns the seconds to wait before retrying.
	Allow(key string, now time.Time) (ok bool, retryAfterSeconds int)
}

// SlidingWindowLimiter is an exact sliding-window limiter (unit-tier impl).
type SlidingWindowLimiter struct {
	Limit  int
	Window time.Duration

	mu     sync.Mutex
	events map[string][]time.Time
}

func NewSlidingWindowLimiter(limit int, window time.Duration) *SlidingWindowLimiter {
	return &SlidingWindowLimiter{Limit: limit, Window: window, events: map[string][]time.Time{}}
}

func (l *SlidingWindowLimiter) Allow(key string, now time.Time) (bool, int) {
	l.mu.Lock()
	defer l.mu.Unlock()
	cutoff := now.Add(-l.Window)
	evs := l.events[key]
	kept := evs[:0]
	for _, t := range evs {
		if t.After(cutoff) {
			kept = append(kept, t)
		}
	}
	if len(kept) >= l.Limit {
		retry := int(math.Ceil(kept[0].Add(l.Window).Sub(now).Seconds()))
		if retry < 1 {
			retry = 1
		}
		l.events[key] = kept
		return false, retry
	}
	l.events[key] = append(kept, now)
	return true, 0
}
