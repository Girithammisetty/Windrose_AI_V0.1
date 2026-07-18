// projectuser recomputes + writes the permissions_flat projection for ONE user
// (a single-user variant of cmd/rebuild), straight from SQL ground truth. Use it
// to repair a single drifted user without the full-tenant sweep — cmd/rebuild
// iterates every user and can spike memory on a large shared stack.
//
//	projectuser -tenant <uuid> -user <user_id>
package main

import (
	"context"
	"crypto/tls"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"

	"github.com/windrose-ai/rbac-service/internal/projection"
	"github.com/windrose-ai/rbac-service/internal/store"
)

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func redisOptions() *redis.Options {
	opts := &redis.Options{
		Addr:     env("REDIS_ADDR", "localhost:6379"),
		Username: os.Getenv("REDIS_USERNAME"),
		Password: os.Getenv("REDIS_PASSWORD"),
	}
	switch strings.ToLower(os.Getenv("REDIS_TLS")) {
	case "1", "true", "yes":
		opts.TLSConfig = &tls.Config{MinVersion: tls.VersionTLS12}
	}
	return opts
}

func main() {
	tenantFlag := flag.String("tenant", "", "tenant uuid (required)")
	userFlag := flag.String("user", "", "user id (required)")
	flag.Parse()

	tenant, err := uuid.Parse(*tenantFlag)
	if err != nil || *userFlag == "" {
		fmt.Fprintln(os.Stderr, "usage: projectuser -tenant <uuid> -user <user_id>")
		os.Exit(2)
	}
	ctx := context.Background()

	pool, err := pgxpool.New(ctx, env("DATABASE_URL", "postgres://rbac:rbac@localhost:5432/rbac?sslmode=disable"))
	if err != nil {
		slog.Error("db pool", "err", err)
		os.Exit(1)
	}
	defer pool.Close()
	st := store.New(pool)

	rdb := redis.NewClient(redisOptions())
	defer func() { _ = rdb.Close() }()
	writer := projection.NewRedisWriter(rdb, projection.DefaultTTL)

	snap, err := st.LoadSnapshot(ctx, tenant, *userFlag)
	if err != nil {
		slog.Error("snapshot", "err", err)
		os.Exit(1)
	}
	flat := projection.Flatten(snap)
	if err := writer.WriteUser(ctx, flat); err != nil {
		slog.Error("write", "err", err)
		os.Exit(1)
	}
	if err := writer.PublishInvalidate(ctx, tenant.String(), []string{*userFlag}); err != nil {
		slog.Warn("invalidate publish failed", "err", err)
	}
	fmt.Printf("rebuilt projection for %s in tenant %s (version %d, tenantActions=%d workspaces=%d admin=%v)\n",
		*userFlag, tenant, flat.Version, len(flat.TenantActions), len(flat.WorkspaceActions), flat.Flags.Admin)
}
