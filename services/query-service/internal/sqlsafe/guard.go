package sqlsafe

import (
	"strings"

	"github.com/windrose-ai/query-service/internal/domain"
)

// GuardConfig scopes the identifier-level tenant guard (QRY-FR-021, BR-2).
type GuardConfig struct {
	// AllowedNamespaces are lowercased "schema" or "catalog.schema" names
	// the tenant may reference (e.g. bronze_t42, iceberg.silver_t42).
	AllowedNamespaces map[string]bool
	// InfoSchemaTables is the whitelisted information_schema subset.
	InfoSchemaTables map[string]bool
}

// DefaultInfoSchemaTables per QRY-FR-021.
func DefaultInfoSchemaTables() map[string]bool {
	return map[string]bool{"tables": true, "columns": true, "views": true}
}

// Guard verifies every referenced table after resolution is inside the
// tenant's namespaces (BR-2: reject before engine contact). CTE names are
// exempt; system catalogs are blocked except the information_schema subset.
func Guard(cls *Classification, cfg GuardConfig) error {
	for _, t := range cls.Tables {
		if t.Schema == "" && t.Catalog == "" {
			if cls.CTENames[t.Name] {
				continue // reference to a CTE defined in this query
			}
			return domain.EStatementNotAllowed(
				"unqualified table reference " + t.Name + ": only resolved {{dataset(...)}} references are allowed")
		}
		if t.Schema == "information_schema" {
			if cfg.InfoSchemaTables[t.Name] {
				continue
			}
			return domain.EStatementNotAllowed("information_schema." + t.Name + " is not in the allowed subset")
		}
		if t.Schema == "pg_catalog" || t.Schema == "system" || strings.HasPrefix(t.Schema, "pg_") {
			return domain.EStatementNotAllowed("system catalog access is not allowed: " + t.String())
		}
		key := t.Schema
		if t.Catalog != "" {
			key = t.Catalog + "." + t.Schema
		}
		if !cfg.AllowedNamespaces[key] {
			return domain.EStatementNotAllowed(
				"table " + t.String() + " is outside the tenant's namespaces")
		}
	}
	return nil
}
