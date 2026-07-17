// Package migrations embeds the forward-only SQL migrations (MASTER-FR-060).
package migrations

import "embed"

// FS holds the embedded migration files, applied via golang-migrate.
//
//go:embed *.sql
var FS embed.FS
