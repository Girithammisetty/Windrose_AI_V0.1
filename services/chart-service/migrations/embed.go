// Package migrations embeds chart-service's forward-only SQL migrations
// (MASTER-FR-060), applied at startup via golang-migrate.
package migrations

import "embed"

// FS holds the embedded .sql migration files.
//
//go:embed *.sql
var FS embed.FS
