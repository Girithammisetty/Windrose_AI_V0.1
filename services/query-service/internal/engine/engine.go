// Package engine defines the execution-brokering Engine port (QRY-FR-040)
// with a real in-process DuckDB engine for small/interactive queries, a real
// Trino cluster adapter (trino.go) for large-scale queries reading the
// shared Iceberg REST catalog directly, a cloud-warehouse adapter as a
// compiling stub, and the plan-time routing rules (BRD §4.3).
package engine

import (
	"context"
	"sync"

	"github.com/google/uuid"
)

// Column describes one result column.
type Column struct {
	Name string `json:"name"`
	Type string `json:"type"`
}

// Sink receives streamed result rows. Row may return an error to abort the
// query (result-size ceilings, QRY-FR-042); engines must stop promptly.
type Sink interface {
	Start(cols []Column) error
	Row(vals []any) error
}

// TableSource is a dataset materialization request: the engine-quoted
// identifier (e.g. `"main"."claims"`) to (re)create as a table over the given
// physical object-store URIs before the user SQL runs (QRY-FR-005). Idents are
// server-supplied by the resolver, never user text (BR-1).
type TableSource struct {
	Ident  string   // engine-quoted, e.g. "main"."claims"
	URIs   []string // physical files, e.g. s3://bucket/.../file.parquet
	Format string   // "parquet" (only format materialized today)
}

// Query is one parameterized statement. SQL contains only $n positional
// placeholders; Args carries the bound values in order. There is no code
// path that concatenates a value into SQL text (QRY-FR-003).
type Query struct {
	ExecutionID uuid.UUID
	SQL         string
	Args        []any
	// Tables are datasets to materialize into the worker's private catalog
	// before the SQL runs (QRY-FR-005). Empty for engines/queries that read
	// physical tables directly (unchanged prod behavior).
	Tables []TableSource
}

// Stats are engine-reported execution stats.
type Stats struct {
	Rows      int64
	ScanBytes int64 // 0 when the engine cannot report it
}

// Engine is the execution port (QRY-FR-040).
type Engine interface {
	Name() string
	Healthy(ctx context.Context) bool
	// Execute runs the query, streaming rows into sink. Cancellation and
	// runtime ceilings propagate via ctx (BR-6: engine statement kill ≤5s).
	Execute(ctx context.Context, q Query, sink Sink) (Stats, error)
}

// Engine names (BRD §4.3).
const (
	NameDuckDB    = "duckdb"
	NameTrino     = "trino"
	NameWarehouse = "warehouse"
)

// Registry maps engine names to implementations.
type Registry struct {
	mu      sync.RWMutex
	engines map[string]Engine
}

func NewRegistry(engines ...Engine) *Registry {
	r := &Registry{engines: map[string]Engine{}}
	for _, e := range engines {
		r.engines[e.Name()] = e
	}
	return r
}

// Register adds or replaces an engine (test harnesses swap in fakes here).
func (r *Registry) Register(e Engine) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.engines[e.Name()] = e
}

func (r *Registry) Get(name string) (Engine, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	e, ok := r.engines[name]
	return e, ok
}

// Healthy reports health of a named engine; absent engines are unhealthy.
func (r *Registry) Healthy(ctx context.Context, name string) bool {
	e, ok := r.Get(name)
	return ok && e.Healthy(ctx)
}
