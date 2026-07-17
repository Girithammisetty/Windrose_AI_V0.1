// Package migrations embeds the forward-only SQL migrations (MASTER-FR-060)
// so the binary and the test harness apply the exact same schema.
package migrations

import "embed"

//go:embed *.sql
var FS embed.FS
