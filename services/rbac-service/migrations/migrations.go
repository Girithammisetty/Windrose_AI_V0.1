// Package migrations embeds the forward-only SQL migrations so binaries and
// tests can apply them without a filesystem checkout.
package migrations

import "embed"

//go:embed *.sql
var FS embed.FS
