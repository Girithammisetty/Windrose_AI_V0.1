package exec

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"strings"

	"github.com/google/uuid"

	"github.com/windrose-ai/query-service/internal/datasets"
	"github.com/windrose-ai/query-service/internal/domain"
	"github.com/windrose-ai/query-service/internal/engine"
	"github.com/windrose-ai/query-service/internal/sqlsafe"
)

// Warning codes surfaced on plans/executions.
const (
	WarnDatasetDeprecated = "DATASET_DEPRECATED" // BR-4
	WarnDryRun            = "DRY_RUN"            // history marker for dry-run rows
)

// Estimate is the plan-time cost estimate (QRY-FR-041).
type Estimate struct {
	ScanBytes         int64  `json:"estimated_scan_bytes"`
	Rows              int64  `json:"estimated_rows,omitempty"`
	PartitionsPruned  string `json:"partitions_pruned,omitempty"`
	Confidence        string `json:"confidence"` // high | low
	TotalDatasetBytes int64  `json:"-"`
}

// EstimateFn produces the cost estimate from resolved dataset stats and the
// classified statement; the default sums Iceberg-reported dataset sizes
// (confidence high when stats exist for every referenced dataset).
type EstimateFn func(ctx context.Context, metas []*datasets.Meta, cls *sqlsafe.Classification) Estimate

func defaultEstimate(_ context.Context, metas []*datasets.Meta, _ *sqlsafe.Classification) Estimate {
	var est Estimate
	est.Confidence = "high"
	var rows int64
	for _, m := range metas {
		est.ScanBytes += m.SizeBytes
		rows += m.RowCount
		if m.SizeBytes == 0 {
			est.Confidence = "low"
		}
	}
	est.Rows = rows
	est.TotalDatasetBytes = est.ScanBytes
	return est
}

// PlanRequest carries everything planning needs.
type PlanRequest struct {
	Op      domain.Op
	SQLText string
	Decls   []domain.VariableDecl
	Values  map[string]json.RawMessage
	// Binds are raw positional prepared-statement arguments for SQL that
	// carries $n placeholders (chart-service's /sql/run contract). Mutually
	// exclusive with Decls/Values (:name variables).
	Binds      []any
	EngineHint string
	Limit      int64 // caller-requested row limit (agents: min with 10k)
	Async      bool
	SavedRefs  []domain.DatasetRef // save-time refs (informational)
}

// Plan is the full pre-execution plan (QRY-FR-041).
type Plan struct {
	Rewritten      *sqlsafe.Rewritten
	Classification *sqlsafe.Classification
	ExecSQL        string // final SQL shipped to the engine (post limit-wrap)
	Estimate       Estimate
	Route          engine.RouteDecision
	Ceilings       domain.Ceilings
	CeilingVerdict string // ok | exceeded
	CeilingDetail  map[string]any
	Warnings       []string
	DatasetURNs    []string
	CacheKey       string
	// Materializations are datasets to load into the engine's private catalog
	// before the SQL runs (QRY-FR-005). Empty unless a {{dataset()}} macro
	// resolved to physical source URIs, or the semantic auto-materialize path
	// is enabled for a referenced schema.
	Materializations []engine.TableSource
	// RedactedParams is what history stores (BR-12: PII params redacted).
	RedactedParams map[string]any
	LimitInjected  bool
	DryRunForced   bool
}

// buildPlan runs the full planning pipeline:
// bind → resolve datasets → rewrite (bound params only) → classify (AST) →
// tenant guard → agent hardening → estimate → ceilings → route.
func (b *Broker) buildPlan(ctx context.Context, req PlanRequest) (*Plan, error) {
	// Binds mode (chart-service contract): raw $n placeholders + ordered
	// argument values, mutually exclusive with declared :name variables.
	bindsMode := len(req.Binds) > 0
	if bindsMode && (len(req.Decls) > 0 || len(req.Values) > 0) {
		return nil, domain.EValidation("binds cannot be combined with declared variables; use one parameter style")
	}
	if err := domain.ValidateDecls(req.Decls); err != nil {
		return nil, err
	}
	bindings, err := domain.BindValues(req.Decls, req.Values)
	if err != nil {
		return nil, err
	}

	// Resolve dataset refs via dataset-service (QRY-FR-005).
	scanRefs := sqlsafe.DatasetRefs
	if bindsMode {
		scanRefs = sqlsafe.DatasetRefsBinds
	}
	refs, err := scanRefs(req.SQLText)
	if err != nil {
		return nil, err
	}
	idents := map[string]string{}
	var metas []*datasets.Meta
	var warnings []string
	var urns []string
	var materializations []engine.TableSource
	namespaces := map[string]bool{}
	versionPins := make([]string, 0, len(refs))
	for _, ref := range refs {
		meta, err := b.Resolver.Resolve(ctx, req.Op.Tenant, ref.Name, ref.Version)
		if err != nil {
			return nil, err
		}
		idents[fmt.Sprintf("%s@%d", ref.Name, ref.Version)] = meta.PhysicalIdent
		metas = append(metas, meta)
		urns = append(urns, meta.URN)
		namespaces[strings.ToLower(meta.Namespace)] = true
		versionPins = append(versionPins, fmt.Sprintf("%s@%d", strings.ToLower(meta.Name), meta.Version))
		if len(meta.SourceURIs) > 0 {
			materializations = append(materializations, engine.TableSource{
				Ident:  meta.PhysicalIdent,
				URIs:   meta.SourceURIs,
				Format: meta.SourceFormat,
			})
		}
		if meta.Deprecated && !contains(warnings, WarnDatasetDeprecated) {
			warnings = append(warnings, WarnDatasetDeprecated) // BR-4
		}
	}

	// Safe substitution: values become bound parameters, never text
	// (QRY-FR-003). Binds mode passes the caller's ordered args through as
	// prepared-statement arguments (count-checked), equally never spliced.
	var rw *sqlsafe.Rewritten
	if bindsMode {
		rw, err = sqlsafe.RewriteBinds(req.SQLText, req.Binds, idents)
	} else {
		rw, err = sqlsafe.Rewrite(req.SQLText, bindings, idents)
	}
	if err != nil {
		return nil, err
	}

	// AST classification (QRY-FR-020).
	cls, err := sqlsafe.Classify(rw.SQL)
	if err != nil {
		return nil, err
	}

	// Semantic auto-materialization (QRY-FR-005): the semantic layer compiles
	// chart SQL that references physical tables directly (e.g.
	// FROM "main"."claims") with no {{dataset()}} macro. When enabled for the
	// table's schema, resolve it by name so its source parquet is materialized
	// and the tenant guard admits the schema. Inert when the allowlist is empty
	// (prod behavior unchanged unless DUCKDB_AUTOMATERIALIZE_SCHEMAS is set).
	if len(b.AutoMaterializeSchemas) > 0 {
		seen := map[string]bool{}
		for _, id := range idents {
			seen[id] = true
		}
		for _, t := range cls.Tables {
			if t.Schema == "" || t.Catalog != "" {
				continue // unqualified (CTE/alias) or catalog-qualified: skip
			}
			if !b.AutoMaterializeSchemas[t.Schema] {
				continue
			}
			meta, err := b.Resolver.Resolve(ctx, req.Op.Tenant, t.Name, 0)
			if err != nil {
				if de, ok := domain.AsError(err); ok && de.Code == domain.CodeDatasetNotFound {
					continue // not a governed dataset; let the guard decide
				}
				return nil, err
			}
			if seen[meta.PhysicalIdent] {
				continue // already resolved (e.g. via a macro)
			}
			seen[meta.PhysicalIdent] = true
			namespaces[strings.ToLower(meta.Namespace)] = true
			metas = append(metas, meta)
			urns = append(urns, meta.URN)
			versionPins = append(versionPins, fmt.Sprintf("%s@%d", strings.ToLower(meta.Name), meta.Version))
			if len(meta.SourceURIs) > 0 {
				materializations = append(materializations, engine.TableSource{
					Ident:  meta.PhysicalIdent,
					URIs:   meta.SourceURIs,
					Format: meta.SourceFormat,
				})
			}
			if meta.Deprecated && !contains(warnings, WarnDatasetDeprecated) {
				warnings = append(warnings, WarnDatasetDeprecated)
			}
		}
	}

	// Identifier-level tenant guard (QRY-FR-021, BR-2).
	guardCfg := sqlsafe.GuardConfig{AllowedNamespaces: namespaces, InfoSchemaTables: sqlsafe.DefaultInfoSchemaTables()}
	if b.ExtraNamespaces != nil {
		for ns := range b.ExtraNamespaces(req.Op.Tenant) {
			guardCfg.AllowedNamespaces[strings.ToLower(ns)] = true
		}
	}
	if err := sqlsafe.Guard(cls, guardCfg); err != nil {
		return nil, err
	}

	plan := &Plan{Rewritten: rw, Classification: cls, ExecSQL: rw.SQL, Warnings: warnings, DatasetURNs: urns, Materializations: materializations}

	// Agent hardening (QRY-FR-022): forced dry-run happens by construction —
	// this same plan runs before any execution — plus LIMIT injection.
	if req.Op.Caller == domain.CallerAgent {
		plan.DryRunForced = true
		if !cls.HasOuterLimit {
			limit := int64(domain.AgentInjectedLimit)
			if req.Limit > 0 && req.Limit < limit {
				limit = req.Limit
			}
			plan.ExecSQL = sqlsafe.WrapWithLimit(rw.SQL, limit)
			plan.LimitInjected = true
		}
	}

	// Cost estimate (QRY-FR-041).
	estimate := b.estimateFn()(ctx, metas, cls)
	if estimate.PartitionsPruned == "" && len(metas) > 0 {
		estimate.PartitionsPruned = "0/0"
	}
	plan.Estimate = estimate

	// Ceilings (QRY-FR-042).
	limits, err := b.Store.GetTenantLimits(ctx, req.Op.Tenant)
	if err != nil {
		return nil, err
	}
	plan.Ceilings = domain.EffectiveCeilings(limits, req.Op.Caller, req.Async)
	plan.CeilingVerdict = "ok"
	if estimate.ScanBytes > plan.Ceilings.MaxScanBytes {
		plan.CeilingVerdict = "exceeded"
		plan.CeilingDetail = map[string]any{
			"ceiling":              "max_scan_bytes",
			"max_scan_bytes":       plan.Ceilings.MaxScanBytes,
			"estimated_scan_bytes": estimate.ScanBytes,
			"confidence":           estimate.Confidence,
		}
	}

	// Routing (QRY-FR-040, §4.3).
	warehousePrimary := limits != nil && limits.WarehousePrimary
	decision, err := engine.Route(engine.RouteInput{
		WarehousePrimary:   warehousePrimary,
		EstimatedScanBytes: estimate.ScanBytes,
		TotalDatasetBytes:  estimate.TotalDatasetBytes,
		DialectPortable:    true,
		EngineHint:         req.EngineHint,
		DuckDBHealthy:      b.Engines.Healthy(ctx, engine.NameDuckDB),
		TrinoHealthy:       b.Engines.Healthy(ctx, engine.NameTrino),
		WarehouseHealthy:   b.Engines.Healthy(ctx, engine.NameWarehouse),
	})
	if err != nil {
		return nil, err
	}
	plan.Route = decision
	plan.Warnings = append(plan.Warnings, decision.Warnings...)

	// Result cache key pins dataset versions (QRY-FR-046).
	plan.CacheKey = cacheKey(req.Op.Tenant, cls.Fingerprint, rw.Args, versionPins)

	// History parameters with PII redaction (BR-12, AC-14).
	plan.RedactedParams = redactParams(rw, cls, bindings, metas)
	return plan, nil
}

// cacheKey = (tenant, sql_fingerprint, bound_params_hash, dataset_versions).
func cacheKey(tenant uuid.UUID, fingerprint string, args []any, versionPins []string) string {
	h := sha256.New()
	fmt.Fprintf(h, "%s|%s|", tenant, fingerprint)
	b, _ := json.Marshal(args)
	h.Write(b)
	sorted := append([]string(nil), versionPins...)
	sort.Strings(sorted)
	fmt.Fprintf(h, "|%s", strings.Join(sorted, ","))
	return hex.EncodeToString(h.Sum(nil))
}

// redactParams builds the history param map, replacing values bound to
// PII-tagged columns with «redacted» (BR-12: full values never persist).
func redactParams(rw *sqlsafe.Rewritten, cls *sqlsafe.Classification, bindings map[string]domain.BoundValue, metas []*datasets.Meta) map[string]any {
	piiCols := map[string]bool{}
	for _, m := range metas {
		for _, c := range m.Columns {
			if c.PIITag != "" {
				piiCols[strings.ToLower(c.Name)] = true
			}
		}
	}
	redactedNames := map[string]bool{}
	for i, name := range rw.ParamNames {
		if col, ok := cls.ParamColumns[i+1]; ok && piiCols[col] {
			redactedNames[name] = true
		}
	}
	out := map[string]any{}
	for name, bv := range bindings {
		if redactedNames[name] {
			out[name] = "«redacted»"
		} else {
			out[name] = bv.Display
		}
	}
	return out
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}
