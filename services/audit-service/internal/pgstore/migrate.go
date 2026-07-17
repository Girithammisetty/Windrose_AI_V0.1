package pgstore

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"strings"

	"github.com/golang-migrate/migrate/v4"
	_ "github.com/golang-migrate/migrate/v4/database/postgres" // driver
	"github.com/golang-migrate/migrate/v4/source/iofs"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/audit-service/migrations"
)

// Bootstrap provisions the audit database and the least-privilege runtime role
// using a superuser admin connection, then is followed by Migrate. This is what
// lets the shipped default DSN connect as the NON-owner audit_rw role: the owner
// (windrose) creates the DB and role here; migrations (run as the owner) create
// the tables + RLS + grants. All steps are idempotent.
//
// adminDSN connects as the superuser (e.g. windrose) to an existing database
// (e.g. windrose). dbName is the audit database to create. runtimeRole/runtimePass
// are the non-owner login the service uses at runtime.
func Bootstrap(ctx context.Context, adminDSN, dbName, runtimeRole, runtimePass string) error {
	conn, err := pgx.Connect(ctx, adminDSN)
	if err != nil {
		return fmt.Errorf("admin connect: %w", err)
	}
	defer conn.Close(ctx)

	// Runtime role: LOGIN, NOSUPERUSER, NOBYPASSRLS (fully subject to RLS).
	var roleExists bool
	if err := conn.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)`, runtimeRole).Scan(&roleExists); err != nil {
		return fmt.Errorf("role check: %w", err)
	}
	if !roleExists {
		// Identifiers can't be parameterized; runtimeRole is operator-controlled config.
		stmt := fmt.Sprintf(`CREATE ROLE %s LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS`,
			quoteIdent(runtimeRole), quoteLiteral(runtimePass))
		if _, err := conn.Exec(ctx, stmt); err != nil && !isDuplicate(err) {
			return fmt.Errorf("create role: %w", err)
		}
	} else {
		// Ensure the password/attributes match config on every boot.
		stmt := fmt.Sprintf(`ALTER ROLE %s LOGIN PASSWORD %s NOSUPERUSER NOBYPASSRLS`,
			quoteIdent(runtimeRole), quoteLiteral(runtimePass))
		if _, err := conn.Exec(ctx, stmt); err != nil {
			return fmt.Errorf("alter role: %w", err)
		}
	}

	// Audit database (CREATE DATABASE cannot run inside a transaction block).
	var dbExists bool
	if err := conn.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)`, dbName).Scan(&dbExists); err != nil {
		return fmt.Errorf("db check: %w", err)
	}
	if !dbExists {
		if _, err := conn.Exec(ctx, fmt.Sprintf(`CREATE DATABASE %s`, quoteIdent(dbName))); err != nil && !isDuplicate(err) {
			return fmt.Errorf("create database: %w", err)
		}
	}
	return nil
}

// Migrate applies the embedded forward-only migrations against ownerDSN (the
// migration/owner role on the audit DB).
func Migrate(ownerDSN string) error {
	src, err := iofs.New(migrations.FS, ".")
	if err != nil {
		return fmt.Errorf("migration source: %w", err)
	}
	m, err := migrate.NewWithSourceInstance("iofs", src, pgxToStdlibDSN(ownerDSN))
	if err != nil {
		return fmt.Errorf("migrate init: %w", err)
	}
	defer m.Close()
	if err := m.Up(); err != nil && !errors.Is(err, migrate.ErrNoChange) {
		return fmt.Errorf("migrate up: %w", err)
	}
	return nil
}

// pgxToStdlibDSN ensures the golang-migrate "postgres://" scheme (it uses
// database/sql, which is happy with the same URL).
func pgxToStdlibDSN(dsn string) string { return dsn }

func quoteIdent(s string) string { return `"` + strings.ReplaceAll(s, `"`, `""`) + `"` }
func quoteLiteral(s string) string { return `'` + strings.ReplaceAll(s, `'`, `''`) + `'` }

func isDuplicate(err error) bool {
	return err != nil && (strings.Contains(err.Error(), "already exists") ||
		strings.Contains(err.Error(), "duplicate key"))
}

// ReplaceDBAndUser rewrites a DSN's user:pass and database — used to derive the
// owner-on-audit DSN from the admin DSN for the migration step.
func ReplaceDBAndUser(dsn, user, pass, db string) (string, error) {
	u, err := url.Parse(dsn)
	if err != nil {
		return "", err
	}
	if user != "" {
		u.User = url.UserPassword(user, pass)
	}
	u.Path = "/" + db
	return u.String(), nil
}
