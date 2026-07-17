// Package api is chart-service's HTTP surface (BRD 07 §5). It wires the chi
// router, the real JWT middleware (go-common/authjwt), OPA authorization, the
// Redis result cache, and the compile→execute→shape resolver. All collaborator
// ports are interfaces so unit tests can substitute in-memory doubles; the
// cmd/server wiring always supplies the real adapters.
package api

import (
	"context"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/chart-service/internal/authz"
	"github.com/windrose-ai/chart-service/internal/domain"
	"github.com/windrose-ai/chart-service/internal/export"
	"github.com/windrose-ai/chart-service/internal/resolve"
	"github.com/windrose-ai/go-common/authjwt"
	"github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/go-common/metricsx"
)

// chiRoutePattern resolves the matched chi route template for a bounded metrics
// route label (evaluated after routing), falling back to "other".
func chiRoutePattern(r *http.Request) string {
	if rc := chi.RouteContext(r.Context()); rc != nil {
		if p := rc.RoutePattern(); p != "" {
			return p
		}
	}
	return "other"
}

// Store is the persistence port used by the HTTP layer (satisfied by
// *store.PG; doubled in unit tests).
type Store interface {
	CreateDashboard(ctx context.Context, d *domain.Dashboard, envs []event.Envelope) error
	GetDashboard(ctx context.Context, tenant, id uuid.UUID) (*domain.Dashboard, error)
	ListDashboards(ctx context.Context, tenant, ws uuid.UUID, module string, archived bool, tag string, limit int, after *uuid.UUID) ([]domain.Dashboard, error)
	UpdateDashboard(ctx context.Context, d *domain.Dashboard, envs []event.Envelope) error
	SetDashboardArchived(ctx context.Context, tenant, id uuid.UUID, archived bool, envs []event.Envelope) error
	DeleteDashboard(ctx context.Context, tenant, id uuid.UUID, envs []event.Envelope) error
	DashboardBlockingCharts(ctx context.Context, tenant, dashboardID uuid.UUID) ([]uuid.UUID, error)

	CreateChart(ctx context.Context, c *domain.Chart, envs []event.Envelope) error
	GetChart(ctx context.Context, tenant, id uuid.UUID) (*domain.Chart, error)
	ListCharts(ctx context.Context, tenant, dashboardID uuid.UUID) ([]domain.Chart, error)
	UpdateChart(ctx context.Context, c *domain.Chart, versionBump bool, expectVersion int, envs []event.Envelope) error
	DeleteChart(ctx context.Context, tenant, id uuid.UUID, envs []event.Envelope) error
	ChartAllowsCases(ctx context.Context, tenant, id uuid.UUID) (bool, error)

	CreateLink(ctx context.Context, tenant, parentID, childID uuid.UUID, cols []domain.ColumnPair, linkType int, envs []event.Envelope) error
	RemoveLink(ctx context.Context, tenant, parentID, childID uuid.UUID, envs []event.Envelope) error

	CreateOperation(ctx context.Context, op *domain.Operation, tenant uuid.UUID) error
	GetOperation(ctx context.Context, tenant, id uuid.UUID) (*domain.Operation, error)
	UpdateOperation(ctx context.Context, tenant, id uuid.UUID, status, url, urn, errMsg string, expires *time.Time) error
	ConcurrentExports(ctx context.Context, tenant uuid.UUID) (int, error)

	GetIdempotent(ctx context.Context, tenant uuid.UUID, key, method, path string) (int, []byte, bool, error)
	PutIdempotent(ctx context.Context, tenant uuid.UUID, key, method, path string, status int, body []byte) error
}

// Cache is the result-cache port (satisfied by *cache.Redis).
type Cache interface {
	Get(ctx context.Context, key string) (*domain.ShapedResult, bool, error)
	Set(ctx context.Context, key, tenant, chartID string, srcURNs []string, res *domain.ShapedResult) error
	InvalidateChart(ctx context.Context, tenant, chartID string) error
	AcquireLock(ctx context.Context, key string) (bool, error)
	ReleaseLock(ctx context.Context, key string) error
}

// Resolver is the data-resolution port (satisfied by *resolve.Resolver).
type Resolver interface {
	Resolve(ctx context.Context, token string, chart *domain.Chart, req domain.ResolveRequest) (*domain.ShapedResult, error)
	Drilldown(ctx context.Context, token, queryURN string, dr resolve.DrilldownRequest) (resolve.ExecResult, error)
}

// FieldValidator returns the known dimension/measure field set for a chart's
// sources (CHART-FR-013). A nil result skips unknown-field checks (dev fallback).
type FieldValidator interface {
	KnownFields(ctx context.Context, token string, sources []domain.ChartSource) (map[string]bool, error)
}

// Server holds the HTTP dependencies.
type Server struct {
	Store      Store
	Cache      Cache
	Authz      authz.Authorizer
	Resolver   Resolver
	Verifier   *authjwt.Verifier
	Exports    *export.FSStore
	Fields     FieldValidator // optional; nil in dev
	PreviewSem chan struct{}  // per-tenant preview concurrency cap (BR-11)
	// PNGRenderer is the headless-renderer sidecar base URL. Empty → PNG export
	// is infra-gated and returns PNG_RENDERER_UNAVAILABLE (documented).
	PNGRenderer string
}

// Router builds the chi router with all routes.
func (s *Server) Router() http.Handler {
	r := chi.NewRouter()
	// RED metrics (MASTER-FR-050): real /metrics + per-request rate/errors/
	// duration, replacing the former empty-200 stub.
	metrics := metricsx.New("chart-service")
	r.Use(recoverer, traceMiddleware, metrics.Middleware(chiRoutePattern))

	r.Get("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	r.Get("/readyz", s.handleReady)
	r.Handle("/metrics", metrics.Handler())

	// MCP backend facade (GAP-2, BRD 13 TPL-FR-012): tool-plane federates
	// approved chart.dashboard.create writes here. Not behind authMiddleware —
	// the caller is the mesh-authenticated mcp-gateway, not a human JWT.
	r.Post("/internal/v1/mcp/invoke", s.handleToolFacade)

	r.Route("/api/v1", func(r chi.Router) {
		// public: chart-type catalog (no tenant data) still requires auth.
		r.Group(func(r chi.Router) {
			r.Use(s.authMiddleware)
			r.Get("/chart-types", s.handleChartTypes)

			// Dashboards.
			r.Post("/dashboards", s.handleCreateDashboard)
			r.Get("/dashboards", s.handleListDashboards)
			r.Get("/dashboards/{id}", s.handleGetDashboard)
			r.Get("/dashboards/{id}/charts", s.handleListDashboardCharts)
			r.Patch("/dashboards/{id}", s.handleUpdateDashboard)
			r.Delete("/dashboards/{id}", s.handleDeleteDashboard)
			r.Post("/dashboards/{id}/archive", s.handleArchiveDashboard)
			r.Patch("/dashboards/{id}/restore", s.handleRestoreDashboard)
			r.Post("/dashboards/{id}/export-bundle", s.handleExportBundle)
			r.Post("/dashboards/import", s.handleImportBundle)
			r.Post("/dashboards/{id}/data", s.handleBatchData)

			// Charts.
			r.Post("/dashboards/{id}/charts", s.handleCreateChart)
			r.Get("/charts/{id}", s.handleGetChart)
			r.Patch("/charts/{id}", s.handleUpdateChart)
			r.Delete("/charts/{id}", s.handleDeleteChart)
			r.Get("/charts/{id}/data", s.handleChartData)
			r.Post("/charts/preview", s.handlePreview)
			r.Post("/charts/{id}/drilldown", s.handleDrilldown)
			r.Post("/charts/{id}/export", s.handleExport)
			r.Put("/charts/{id}/link", s.handleCreateLink)
			r.Delete("/charts/{id}/link", s.handleRemoveLink)

			// Operations + export artifacts.
			r.Get("/operations/{id}", s.handleGetOperation)
		})
		// Signed artifact download is validated by HMAC, not JWT.
		r.Get("/exports/*", s.handleDownloadExport)
	})
	return r
}

func (s *Server) handleReady(w http.ResponseWriter, r *http.Request) {
	// Readiness is best-effort; DB ping is done in cmd/server's real wiring.
	_ = r
	w.WriteHeader(http.StatusOK)
}
