package engine

import (
	"context"
	"database/sql"
	"fmt"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	_ "github.com/trinodb/trino-go-client/trino"
)

// Trino is the real cluster adapter (QRY-FR-040), backed by
// github.com/trinodb/trino-go-client's database/sql driver. Direct-read
// design (chosen over mirroring DuckDB's per-execution materialization):
// Trino has native Iceberg-REST-catalog support, so it queries the shared
// Iceberg tables directly — Query.Tables is inert here, exactly like the
// engine.go doc comment describes for "engines that read physical tables
// directly." The resolver's already-qualified `"schema"."table"` identifiers
// resolve correctly because Catalog is fixed as Trino's default catalog for
// the session (DSN catalog=...), so a two-part identifier implicitly means
// <catalog>.<schema>.<table>.
type Trino struct {
	// Endpoint is the Trino coordinator base URI, e.g. "http://localhost:8080".
	Endpoint string
	// User is the Trino session user (the protocol requires one; server-owned
	// config, never derived from request data). Defaults to "windrose".
	User string
	// Catalog is the fixed default catalog every query resolves against
	// (e.g. "iceberg"). Server-owned, not user input.
	Catalog string
	// Source tags the Trino client session (resource-group identification).
	Source string
	// HealthCacheTTL bounds how often Healthy() actually probes the cluster;
	// Route() calls Healthy() once per query plan (internal/exec/plan.go),
	// so an uncached network round trip there would add latency to every
	// single query, including ones that end up routing elsewhere. Default 5s.
	HealthCacheTTL time.Duration
	// HTTPClient is used for the health probe only; defaults to a client with
	// a short timeout.
	HTTPClient *http.Client

	healthMu sync.Mutex
	healthAt time.Time
	healthOK bool
}

func (t *Trino) Name() string { return NameTrino }

func (t *Trino) dsn() (string, error) {
	if t.Endpoint == "" {
		return "", fmt.Errorf("trino: endpoint not configured")
	}
	u, err := url.Parse(t.Endpoint)
	if err != nil {
		return "", fmt.Errorf("trino: invalid endpoint %q: %w", t.Endpoint, err)
	}
	user := t.User
	if user == "" {
		user = "windrose"
	}
	u.User = url.User(user)
	q := u.Query()
	if t.Catalog != "" {
		q.Set("catalog", t.Catalog)
	}
	if t.Source != "" {
		q.Set("source", t.Source)
	}
	u.RawQuery = q.Encode()
	return u.String(), nil
}

// Healthy probes the coordinator's /v1/info endpoint (cheap — no query
// execution) with a short cached TTL. BR-13's fallback behavior depends on
// this being a timely, non-blocking signal for every plan, not just for
// queries that actually end up routed to Trino.
func (t *Trino) Healthy(ctx context.Context) bool {
	if t.Endpoint == "" {
		return false
	}
	ttl := t.HealthCacheTTL
	if ttl <= 0 {
		ttl = 5 * time.Second
	}
	t.healthMu.Lock()
	if fresh := time.Since(t.healthAt) < ttl; fresh {
		ok := t.healthOK
		t.healthMu.Unlock()
		return ok
	}
	t.healthMu.Unlock()

	client := t.HTTPClient
	if client == nil {
		client = &http.Client{Timeout: 2 * time.Second}
	}
	reqCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()

	ok := false
	if req, err := http.NewRequestWithContext(reqCtx, http.MethodGet, strings.TrimRight(t.Endpoint, "/")+"/v1/info", nil); err == nil {
		if resp, rerr := client.Do(req); rerr == nil {
			ok = resp.StatusCode == http.StatusOK
			resp.Body.Close()
		}
	}

	t.healthMu.Lock()
	t.healthOK = ok
	t.healthAt = time.Now()
	t.healthMu.Unlock()
	return ok
}

// dollarPlaceholder matches the resolver's universal $n positional
// placeholder convention (sqlsafe/rewrite.go — the same contract DuckDB
// receives) so it can be translated into Trino's native ? marker syntax.
var dollarPlaceholder = regexp.MustCompile(`\$(\d+)`)

// trinoize rewrites $n placeholders into Trino's occurrence-based ? markers,
// EXPANDING args so a placeholder referenced more than once (the resolver
// reuses the same $n for a repeated :name binding — e.g.
// `WHERE start <= $1 AND end >= $1`) gets its value repeated once per
// occurrence. Trino's protocol has no notion of "the same named parameter
// twice": each ? consumes its own positional slot in the EXECUTE ... USING
// list, unlike $n (a Postgres-style addressable slot DuckDB's driver
// supports natively). Only the placeholder TOKEN is rewritten — no bound
// value is ever spliced into SQL text (QRY-FR-003 is unaffected).
func trinoize(sqlText string, args []any) (string, []any, error) {
	var expanded []any
	var errOut error
	rewritten := dollarPlaceholder.ReplaceAllStringFunc(sqlText, func(m string) string {
		if errOut != nil {
			return m
		}
		n, err := strconv.Atoi(m[1:])
		if err != nil || n < 1 || n > len(args) {
			errOut = fmt.Errorf("trino: placeholder %s out of range (have %d args)", m, len(args))
			return m
		}
		expanded = append(expanded, args[n-1])
		return "?"
	})
	if errOut != nil {
		return "", nil, errOut
	}
	return rewritten, expanded, nil
}

// Execute runs one prepared, parameterized statement against the Trino
// cluster, streaming rows into sink. Cancellation propagates via ctx: the
// underlying driver issues a real DELETE /v1/query/{id} when rows.Close()
// runs on a cancelled context (BR-6/QRY-FR-045 — no custom code needed here,
// it's built into trino-go-client's driverRows.Close()).
func (t *Trino) Execute(ctx context.Context, q Query, sink Sink) (Stats, error) {
	dsn, err := t.dsn()
	if err != nil {
		return Stats{}, err
	}
	db, err := sql.Open("trino", dsn)
	if err != nil {
		return Stats{}, fmt.Errorf("trino open: %w", err)
	}
	defer db.Close()

	sqlText, args, err := trinoize(q.SQL, q.Args)
	if err != nil {
		return Stats{}, err
	}

	rows, err := db.QueryContext(ctx, sqlText, args...)
	if err != nil {
		if ctx.Err() != nil {
			return Stats{}, ctx.Err()
		}
		return Stats{}, fmt.Errorf("trino query: %w", err)
	}
	defer rows.Close()

	colTypes, err := rows.ColumnTypes()
	if err != nil {
		return Stats{}, err
	}
	cols := make([]Column, len(colTypes))
	for i, ct := range colTypes {
		cols[i] = Column{Name: ct.Name(), Type: trinoLogicalType(ct.DatabaseTypeName())}
	}
	if err := sink.Start(cols); err != nil {
		return Stats{}, err
	}

	var stats Stats
	vals := make([]any, len(cols))
	ptrs := make([]any, len(cols))
	for i := range vals {
		ptrs[i] = &vals[i]
	}
	for rows.Next() {
		if err := rows.Scan(ptrs...); err != nil {
			return stats, err
		}
		// trino-go-client's ConvertValue already returns canonical Go types
		// (bool/int64/float64/string/[]byte/time.Time/map[string]any/[]any) —
		// decimals arrive pre-rendered as strings (QRY-FR-063: no float
		// rounding), unlike DuckDB which needs bespoke big.Int/Decimal
		// handling, so no extra conversion pass is needed here.
		row := make([]any, len(vals))
		copy(row, vals)
		if err := sink.Row(row); err != nil {
			return stats, err
		}
		stats.Rows++
	}
	if err := rows.Err(); err != nil {
		if ctx.Err() != nil {
			return stats, ctx.Err()
		}
		return stats, err
	}
	// ScanBytes stays 0 (an explicitly allowed value per Stats' own doc
	// comment): the client's public API doesn't expose per-query stats
	// (QueryProgressInfo.QueryStats, the only place peakMemoryBytes/
	// processedBytes would come from, is unexported).
	return stats, nil
}

// trinoLogicalType maps Trino's type-name vocabulary to the platform's edge
// type vocabulary (QRY-FR-063), matching duckdb.go's logicalType() output
// values so clients see the same set regardless of which engine executed.
func trinoLogicalType(dbType string) string {
	t := strings.ToUpper(dbType)
	switch {
	case strings.HasPrefix(t, "DECIMAL"):
		return "decimal"
	case t == "TINYINT", t == "SMALLINT", t == "INTEGER":
		return "integer"
	case t == "BIGINT":
		return "bigint"
	case t == "REAL", t == "DOUBLE":
		return "float"
	case t == "BOOLEAN":
		return "boolean"
	case t == "DATE":
		return "date"
	case strings.HasPrefix(t, "TIMESTAMP"), strings.HasPrefix(t, "TIME"):
		return "timestamp"
	case t == "VARBINARY":
		return "binary"
	case t == "ARRAY":
		return "list"
	case t == "MAP", t == "ROW":
		return "struct"
	default:
		return "string"
	}
}
