// Package redisx is a thin real Redis client wrapper over go-redis. It backs
// the opaclient projection reads, the API-key denylist, consumer dedup, and
// rate-limit windows. It speaks the real Redis wire protocol against a real
// Redis 7 server (deploy/docker-compose.dev.yml) — there is no in-memory mode.
package redisx

import (
	"context"
	"crypto/tls"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
)

// Client wraps a go-redis UniversalClient with the small, typed surface the
// platform needs. The embedded client is exported so callers that need the
// full go-redis API (Lua scripts, pub/sub) can reach it.
type Client struct {
	R redis.UniversalClient
}

// Config configures a Redis connection with optional auth/TLS, so a tenant can
// point the platform at their own Redis-compatible service — self-hosted Redis,
// AWS ElastiCache, Azure Cache for Redis, GCP Memorystore — via configuration
// alone, never a code change. Username/Password are both optional (Redis 6+ ACL
// users, or the single AUTH token most managed offerings use as Password with
// Username left empty); TLS is required by most managed offerings.
type Config struct {
	Addr     string // host:port
	Username string
	Password string
	TLS      bool
}

// New dials addr (host:port) with no auth/TLS — the local-dev default.
// Equivalent to NewFromConfig(Config{Addr: addr}). It does not ping; callers
// use Ping in readiness probes.
func New(addr string) *Client {
	return NewFromConfig(Config{Addr: addr})
}

// NewFromConfig dials per Config — the way to reach an authenticated/TLS
// managed Redis-compatible service in production.
func NewFromConfig(cfg Config) *Client {
	opts := &redis.Options{Addr: cfg.Addr, Username: cfg.Username, Password: cfg.Password}
	if cfg.TLS {
		opts.TLSConfig = &tls.Config{MinVersion: tls.VersionTLS12}
	}
	return &Client{R: redis.NewClient(opts)}
}

// NewFromEnv builds a Client from an already-resolved addr plus the platform's
// three standard Redis auth/TLS env vars — REDIS_USERNAME, REDIS_PASSWORD,
// REDIS_TLS ("1"/"true"/"yes", case-insensitive) — read via lookup (typically
// os.Getenv). This is the one place every service should source Redis
// credentials from, so pointing the platform at a managed, authenticated
// Redis-compatible service (ElastiCache/Azure Cache/Memorystore) is a
// configuration change, never a code change. All three env vars are optional;
// omitting them reproduces New(addr)'s local-dev, no-auth behavior exactly.
func NewFromEnv(addr string, lookup func(string) string) *Client {
	tlsOn := false
	switch strings.ToLower(lookup("REDIS_TLS")) {
	case "1", "true", "yes":
		tlsOn = true
	}
	return NewFromConfig(Config{
		Addr: addr, Username: lookup("REDIS_USERNAME"), Password: lookup("REDIS_PASSWORD"), TLS: tlsOn,
	})
}

// Wrap adapts an existing go-redis client (shared connection pools).
func Wrap(r redis.UniversalClient) *Client { return &Client{R: r} }

// Ping checks connectivity (readyz).
func (c *Client) Ping(ctx context.Context) error { return c.R.Ping(ctx).Err() }

// Close releases the connection pool.
func (c *Client) Close() error { return c.R.Close() }

// Set writes key=value with an optional TTL (0 = no expiry). Satisfies the
// denylist RedisCmd contract.
func (c *Client) Set(ctx context.Context, key string, value any, ttl time.Duration) error {
	return c.R.Set(ctx, key, value, ttl).Err()
}

// Get returns the raw string value and ok=false when the key is absent.
func (c *Client) Get(ctx context.Context, key string) (string, bool, error) {
	v, err := c.R.Get(ctx, key).Result()
	if err == redis.Nil {
		return "", false, nil
	}
	if err != nil {
		return "", false, err
	}
	return v, true, nil
}

// Exists reports whether key is present. Satisfies the denylist RedisCmd
// contract.
func (c *Client) Exists(ctx context.Context, key string) (bool, error) {
	n, err := c.R.Exists(ctx, key).Result()
	return n > 0, err
}

// SetNX sets key only if absent (returns true when it wrote). This is the
// idempotent-consumer dedup primitive (MASTER-FR-032: SETNX event_id, 24h TTL).
func (c *Client) SetNX(ctx context.Context, key string, ttl time.Duration) (bool, error) {
	return c.R.SetNX(ctx, key, 1, ttl).Result()
}

// Del removes keys.
func (c *Client) Del(ctx context.Context, keys ...string) error {
	if len(keys) == 0 {
		return nil
	}
	return c.R.Del(ctx, keys...).Err()
}

// TTL returns the remaining TTL for key (negative durations for no-expiry /
// missing per go-redis semantics).
func (c *Client) TTL(ctx context.Context, key string) (time.Duration, error) {
	return c.R.TTL(ctx, key).Result()
}

// Publish sends payload on a pub/sub channel (perm.invalidate notifications).
func (c *Client) Publish(ctx context.Context, channel string, payload any) error {
	return c.R.Publish(ctx, channel, payload).Err()
}
