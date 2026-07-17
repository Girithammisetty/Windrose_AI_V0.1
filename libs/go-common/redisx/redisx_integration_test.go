//go:build integration

package redisx

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"
)

func addr() string {
	if a := os.Getenv("REDIS_ADDR"); a != "" {
		return a
	}
	return "localhost:6379"
}

func TestRedisReal(t *testing.T) {
	ctx := context.Background()
	c := New(addr())
	defer c.Close()
	if err := c.Ping(ctx); err != nil {
		t.Skipf("redis unavailable at %s: %v", addr(), err)
	}

	k := "test:" + uuid.NewString()

	// SetNX dedup semantics (MASTER-FR-032).
	fresh, err := c.SetNX(ctx, k, time.Minute)
	if err != nil || !fresh {
		t.Fatalf("first SetNX: fresh=%v err=%v", fresh, err)
	}
	fresh, err = c.SetNX(ctx, k, time.Minute)
	if err != nil || fresh {
		t.Fatalf("second SetNX should be dup: fresh=%v err=%v", fresh, err)
	}

	// Exists + Get + Del.
	ok, err := c.Exists(ctx, k)
	if err != nil || !ok {
		t.Fatalf("exists: %v %v", ok, err)
	}
	if err := c.Set(ctx, k, "hello", time.Minute); err != nil {
		t.Fatal(err)
	}
	v, ok, err := c.Get(ctx, k)
	if err != nil || !ok || v != "hello" {
		t.Fatalf("get: %q %v %v", v, ok, err)
	}
	ttl, err := c.TTL(ctx, k)
	if err != nil || ttl <= 0 {
		t.Fatalf("ttl: %v %v", ttl, err)
	}
	if err := c.Del(ctx, k); err != nil {
		t.Fatal(err)
	}
	if _, ok, _ := c.Get(ctx, k); ok {
		t.Fatal("key should be gone after Del")
	}
}
