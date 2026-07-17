package engine

import (
	"github.com/windrose-ai/query-service/internal/domain"
)

// Routing thresholds (QRY-FR-040).
const (
	DuckDBMaxScanBytes    = 500 << 20 // 500 MB estimated scan
	DuckDBMaxDatasetBytes = 5 << 30   // 5 GB total referenced dataset size
)

// Routing warnings surfaced to callers.
const (
	WarnHintOverridden = "HINT_OVERRIDDEN"
	WarnEngineFallback = "ENGINE_FALLBACK"
)

// RouteInput is everything the plan-time routing decision needs (§4.3).
type RouteInput struct {
	WarehousePrimary   bool // tenant configured warehouse_primary
	EstimatedScanBytes int64
	TotalDatasetBytes  int64
	DialectPortable    bool
	EngineHint         string // "", duckdb, trino, warehouse
	DuckDBHealthy      bool
	TrinoHealthy       bool
	WarehouseHealthy   bool
}

// RouteDecision is the chosen engine plus the recorded reason (QRY-FR-040:
// decision + reasons recorded in history).
type RouteDecision struct {
	Engine   string
	Reason   domain.RoutingReason
	Warnings []string
}

// Route evaluates the decision table top-down (BRD §4.3):
//
//	tenant warehouse_primary                          → warehouse (tenant_policy)
//	scan ≤ 500MB ∧ datasets ≤ 5GB ∧ portable ∧ up     → duckdb   (small_interactive)
//	Trino healthy                                     → trino    (default_large)
//	Trino unhealthy, warehouse healthy                → warehouse(engine_fallback + warning)
//	otherwise                                         → 503 ENGINE_UNAVAILABLE
//
// engine_hint may promote duckdb→trino/warehouse but a duckdb hint above
// thresholds is ignored with warning HINT_OVERRIDDEN.
func Route(in RouteInput) (RouteDecision, error) {
	base, err := routeBase(in)
	if err != nil {
		return RouteDecision{}, err
	}
	switch in.EngineHint {
	case "", base.Engine:
		return base, nil
	case NameDuckDB:
		// A hint may never force DuckDB above thresholds (QRY-FR-040).
		base.Warnings = append(base.Warnings, WarnHintOverridden)
		base.Reason.Warnings = append(base.Reason.Warnings, WarnHintOverridden)
		return base, nil
	case NameTrino:
		if base.Engine == NameDuckDB && in.TrinoHealthy {
			return RouteDecision{Engine: NameTrino, Reason: domain.RoutingReason{Rule: "engine_hint", Detail: "promoted from duckdb by hint"}}, nil
		}
		base.Warnings = append(base.Warnings, WarnHintOverridden)
		base.Reason.Warnings = append(base.Reason.Warnings, WarnHintOverridden)
		return base, nil
	case NameWarehouse:
		if (base.Engine == NameDuckDB || base.Engine == NameTrino) && in.WarehouseHealthy {
			return RouteDecision{Engine: NameWarehouse, Reason: domain.RoutingReason{Rule: "engine_hint", Detail: "promoted by hint"}}, nil
		}
		base.Warnings = append(base.Warnings, WarnHintOverridden)
		base.Reason.Warnings = append(base.Reason.Warnings, WarnHintOverridden)
		return base, nil
	default:
		return RouteDecision{}, domain.EValidation("unknown engine_hint " + in.EngineHint)
	}
}

func routeBase(in RouteInput) (RouteDecision, error) {
	if in.WarehousePrimary {
		if in.WarehouseHealthy {
			return RouteDecision{Engine: NameWarehouse, Reason: domain.RoutingReason{Rule: "tenant_policy"}}, nil
		}
		if in.TrinoHealthy {
			return RouteDecision{
				Engine:   NameTrino,
				Reason:   domain.RoutingReason{Rule: "engine_fallback", Detail: "warehouse_primary but warehouse unavailable", Warnings: []string{WarnEngineFallback}},
				Warnings: []string{WarnEngineFallback},
			}, nil
		}
		return RouteDecision{}, domain.EEngineUnavailable("no engine available for tenant policy warehouse_primary")
	}
	if in.EstimatedScanBytes <= DuckDBMaxScanBytes &&
		in.TotalDatasetBytes <= DuckDBMaxDatasetBytes &&
		in.DialectPortable && in.DuckDBHealthy {
		return RouteDecision{Engine: NameDuckDB, Reason: domain.RoutingReason{Rule: "small_interactive"}}, nil
	}
	if in.TrinoHealthy {
		return RouteDecision{Engine: NameTrino, Reason: domain.RoutingReason{Rule: "default_large"}}, nil
	}
	if in.WarehouseHealthy {
		return RouteDecision{
			Engine:   NameWarehouse,
			Reason:   domain.RoutingReason{Rule: "engine_fallback", Detail: "trino unavailable in cell", Warnings: []string{WarnEngineFallback}},
			Warnings: []string{WarnEngineFallback},
		}, nil
	}
	// BR-13: DuckDB-eligible queries are unaffected by big-engine outages —
	// they matched the small_interactive rule above. Reaching here means no
	// engine can serve this plan.
	return RouteDecision{}, domain.EEngineUnavailable("all engines down for this plan")
}
