// rebuild is the full-rebuild command (RBC-FR-043): recomputes the
// permissions_flat projection for every known user of a tenant, synchronously,
// straight from SQL ground truth.
//
//	rebuild -tenant <uuid> [-verify]
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

// redisOptions builds go-redis options from REDIS_ADDR plus the platform's
// standard auth/TLS env vars — see cmd/server/main.go's identical helper.
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
	tenantFlag := flag.String("tenant", "", "tenant uuid to rebuild (required)")
	verifyFlag := flag.Bool("verify", false, "verify against SQL ground truth after rebuilding")
	flag.Parse()

	tenant, err := uuid.Parse(*tenantFlag)
	if err != nil {
		fmt.Fprintln(os.Stderr, "usage: rebuild -tenant <uuid> [-verify]")
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

	users, err := st.TenantUserIDs(ctx, tenant)
	if err != nil {
		slog.Error("list users", "err", err)
		os.Exit(1)
	}
	rebuilt := 0
	for _, user := range users {
		snap, err := st.LoadSnapshot(ctx, tenant, user)
		if err != nil {
			slog.Error("snapshot", "user", user, "err", err)
			os.Exit(1)
		}
		if err := writer.WriteUser(ctx, projection.Flatten(snap)); err != nil {
			slog.Error("write", "user", user, "err", err)
			os.Exit(1)
		}
		rebuilt++
	}
	// Tenant-level keys.
	if archived, err := st.ArchivedWorkspaceIDs(ctx, tenant); err == nil {
		ids := make([]string, 0, len(archived))
		for _, id := range archived {
			ids = append(ids, id.String())
		}
		if v, verr := st.NextVersion(ctx); verr == nil {
			_ = writer.WriteArchivedWorkspaces(ctx, tenant.String(), ids, v)
		}
	}
	if catalog, err := st.CatalogMap(ctx); err == nil {
		if v, verr := st.NextVersion(ctx); verr == nil {
			_ = writer.WriteCatalog(ctx, catalog, v)
		}
	}
	if err := writer.PublishInvalidate(ctx, tenant.String(), users); err != nil {
		slog.Warn("invalidate publish failed", "err", err)
	}
	fmt.Printf("rebuilt projection for %d users in tenant %s\n", rebuilt, tenant)

	if *verifyFlag {
		res, err := projection.Verify(ctx, st, projection.NewRedisReader(rdb), writer, tenant, users, false)
		if err != nil {
			slog.Error("verify", "err", err)
			os.Exit(1)
		}
		fmt.Printf("verify: %d users checked, drift=%d\n", res.UsersChecked, res.Drift())
		if res.Drift() > 0 {
			os.Exit(1)
		}
	}
}
