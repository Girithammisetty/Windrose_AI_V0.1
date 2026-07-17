package store

import (
	"fmt"
	"strings"

	"github.com/golang-migrate/migrate/v4"
	_ "github.com/golang-migrate/migrate/v4/database/pgx/v5" // pgx5 driver
	"github.com/golang-migrate/migrate/v4/source/iofs"

	"github.com/windrose-ai/rbac-service/migrations"
)

// Migrate applies all forward migrations (golang-migrate, MASTER-FR-060).
// databaseURL is a postgres:// URL; the migration role must own the schema
// (it bypasses RLS as table owner), while the app connects as a plain role.
func Migrate(databaseURL string) error {
	src, err := iofs.New(migrations.FS, ".")
	if err != nil {
		return fmt.Errorf("migrations source: %w", err)
	}
	// golang-migrate's pgx/v5 driver expects the pgx5:// scheme.
	url := databaseURL
	if strings.HasPrefix(url, "postgres://") {
		url = "pgx5://" + strings.TrimPrefix(url, "postgres://")
	} else if strings.HasPrefix(url, "postgresql://") {
		url = "pgx5://" + strings.TrimPrefix(url, "postgresql://")
	}
	m, err := migrate.NewWithSourceInstance("iofs", src, url)
	if err != nil {
		return fmt.Errorf("migrate init: %w", err)
	}
	defer func() { _, _ = m.Close() }()
	if err := m.Up(); err != nil && err != migrate.ErrNoChange {
		return fmt.Errorf("migrate up: %w", err)
	}
	return nil
}
