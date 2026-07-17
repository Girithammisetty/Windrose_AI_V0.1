package redisx

import (
	"testing"

	"github.com/redis/go-redis/v9"
)

// NewFromConfig must thread auth/TLS into the underlying go-redis client so a
// tenant can point at a managed, authenticated Redis-compatible service
// (ElastiCache/Azure Cache/Memorystore) via config alone — this is the whole
// point of Phase 3 "swappable dependency providers" for the cache dependency.
func TestNewFromConfig_ThreadsAuthAndTLS(t *testing.T) {
	c := NewFromConfig(Config{
		Addr: "redis.example.internal:6380", Username: "app", Password: "s3cr3t", TLS: true,
	})
	rc, ok := c.R.(*redis.Client)
	if !ok {
		t.Fatalf("expected *redis.Client, got %T", c.R)
	}
	opts := rc.Options()
	if opts.Addr != "redis.example.internal:6380" {
		t.Errorf("Addr = %q", opts.Addr)
	}
	if opts.Username != "app" {
		t.Errorf("Username = %q", opts.Username)
	}
	if opts.Password != "s3cr3t" {
		t.Errorf("Password = %q", opts.Password)
	}
	if opts.TLSConfig == nil {
		t.Error("expected TLSConfig to be set when Config.TLS is true")
	}
}

func TestNewFromConfig_NoTLSByDefault(t *testing.T) {
	c := NewFromConfig(Config{Addr: "localhost:6379"})
	rc := c.R.(*redis.Client)
	if rc.Options().TLSConfig != nil {
		t.Error("expected no TLSConfig when Config.TLS is false")
	}
}

// New(addr) must remain the exact zero-auth/zero-TLS local-dev default — every
// existing caller that hasn't opted into Config yet must see no behavior change.
func TestNew_IsAuthlessLocalDevDefault(t *testing.T) {
	c := New("localhost:6379")
	rc := c.R.(*redis.Client)
	opts := rc.Options()
	if opts.Addr != "localhost:6379" || opts.Username != "" || opts.Password != "" || opts.TLSConfig != nil {
		t.Errorf("New() must be equivalent to NewFromConfig(Config{Addr: addr}), got %+v", opts)
	}
}
