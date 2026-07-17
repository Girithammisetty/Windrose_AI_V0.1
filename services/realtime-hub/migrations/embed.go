// Package migrations embeds realtime-hub's forward-only SQL migrations
// (MASTER-FR-060), applied via golang-migrate at startup.
package migrations

import "embed"

// FS carries the .sql migration files.
//
//go:embed *.sql
var FS embed.FS
