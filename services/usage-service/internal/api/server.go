package api

import (
	"context"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/windrose-ai/usage-service/internal/authz"
	"github.com/windrose-ai/usage-service/internal/domain"
	"github.com/windrose-ai/usage-service/internal/events"
	"github.com/windrose-ai/usage-service/internal/store"
)

// Store is the persistence surface the API depends on (satisfied by *store.PG;
// a unit-test double may implement it).
type Store interface {
	EmitEvent(ctx context.Context, env events.Envelope) error
	GetIdempotent(ctx context.Context, tenant uuid.UUID, key string) (*store.IdempotencyRecord, error)
	PutIdempotent(ctx context.Context, tenant uuid.UUID, key, method, path string, status int, response []byte) error

	ListMeters(ctx context.Context) ([]domain.Meter, error)

	QueryUsage(ctx context.Context, tenant uuid.UUID, q store.ShowbackQuery) ([]domain.RollupRow, error)
	Chargeback(ctx context.Context, tenant uuid.UUID, month string) ([]store.ChargebackLine, error)
	ReconciliationStatus(ctx context.Context, month string) (string, error)
	ResolvePrices(ctx context.Context, tenant uuid.UUID, at time.Time) (map[string]float64, map[string]uuid.UUID, error)

	CreateBudget(ctx context.Context, op domain.Op, b domain.Budget) (domain.Budget, error)
	GetBudget(ctx context.Context, tenant, id uuid.UUID) (domain.Budget, error)
	ListBudgets(ctx context.Context, tenant, after uuid.UUID, limit int) ([]domain.Budget, error)
	UpdateBudget(ctx context.Context, op domain.Op, id uuid.UUID, newLimit *float64, newAction *string) (domain.Budget, error)
	DeleteBudget(ctx context.Context, op domain.Op, id uuid.UUID) error
	GetBudgetState(ctx context.Context, tenant, id uuid.UUID) (domain.Budget, domain.BudgetState, error)

	CreateRateCard(ctx context.Context, op domain.Op, rc domain.RateCard) (domain.RateCard, error)
	ActivateRateCard(ctx context.Context, op domain.Op, id uuid.UUID) (domain.RateCard, error)
	ListRateCards(ctx context.Context, op domain.Op) ([]domain.RateCard, error)

	ListAnomalies(ctx context.Context, tenant uuid.UUID, status string, limit int) ([]domain.Anomaly, error)
	DismissAnomaly(ctx context.Context, op domain.Op, id uuid.UUID, by string) error

	ListReconciliations(ctx context.Context, limit int) ([]domain.Reconciliation, error)
	AcknowledgeReconciliation(ctx context.Context, id uuid.UUID) error
	RecordAdjustment(ctx context.Context, op domain.Op, a domain.Adjustment) (domain.Adjustment, error)

	Ping(ctx context.Context) error
}

// Server holds the HTTP dependencies.
type Server struct {
	Store    Store
	Authz    authz.Authorizer
	Verifier *Verifier
	Ready    func(ctx context.Context) error // readiness (DB/Kafka/Redis)

	// guarded records every action bound to a route via RequireAction (set at
	// Router build time); the action-catalog drift test asserts each is
	// registered with rbac and uses a canonical verb.
	guarded []string
}

// GuardedActions returns the distinct actions bound to routes. Call after
// Router(). Used by the drift test.
func (s *Server) GuardedActions() []string { return s.guarded }

// Router builds the chi router with the full middleware stack and routes.
func (s *Server) Router() http.Handler {
	r := chi.NewRouter()
	r.Use(TraceMiddleware)
	r.Use(RecoverMiddleware)

	r.Get("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK); _, _ = w.Write([]byte("ok")) })
	r.Get("/readyz", s.handleReady)
	r.Handle("/metrics", promhttp.Handler())

	r.Route("/api/v1", func(r chi.Router) {
		r.Use(AuthMiddleware(s.Verifier))

		r.With(s.RequireAction(authz.ActionMeterRead)).Get("/meters", s.handleListMeters)

		r.With(s.RequireAction(authz.ActionReportRead)).Get("/reports/usage", s.handleReportUsage)
		r.With(s.RequireAction(authz.ActionReportRead)).Get("/reports/chargeback", s.handleChargeback)

		r.With(s.RequireAction(authz.ActionBudgetCreate)).Post("/budgets", s.handleCreateBudget)
		r.With(s.RequireAction(authz.ActionBudgetRead)).Get("/budgets", s.handleListBudgets)
		r.With(s.RequireAction(authz.ActionBudgetRead)).Get("/budgets/{id}", s.handleGetBudget)
		r.With(s.RequireAction(authz.ActionBudgetUpdate)).Patch("/budgets/{id}", s.handlePatchBudget)
		r.With(s.RequireAction(authz.ActionBudgetDelete)).Delete("/budgets/{id}", s.handleDeleteBudget)
		r.With(s.RequireAction(authz.ActionBudgetRead)).Get("/budgets/{id}/state", s.handleBudgetState)
		r.With(s.RequireAction(authz.ActionBudgetRead)).Get("/budget-states", s.handleBudgetStates)

		r.With(s.RequireAction(authz.ActionRateCardCreate)).Post("/rate-cards", s.handleCreateRateCard)
		r.With(s.RequireAction(authz.ActionRateCardUpdate)).Post("/rate-cards/{id}/activate", s.handleActivateRateCard)
		r.With(s.RequireAction(authz.ActionRateCardRead)).Get("/rate-cards", s.handleListRateCards)

		r.With(s.RequireAction(authz.ActionAnomalyRead)).Get("/anomalies", s.handleListAnomalies)
		r.With(s.RequireAction(authz.ActionAnomalyUpdate)).Post("/anomalies/{id}/dismiss", s.handleDismissAnomaly)

		r.With(s.RequireAction(authz.ActionReconRead)).Get("/reconciliations", s.handleListReconciliations)
		r.With(s.RequireAction(authz.ActionReconUpdate)).Post("/reconciliations/{id}/acknowledge", s.handleAckReconciliation)
		r.With(s.RequireAction(authz.ActionReconUpdate)).Post("/adjustments", s.handleCreateAdjustment)
	})

	return r
}

func (s *Server) handleReady(w http.ResponseWriter, r *http.Request) {
	if s.Ready != nil {
		if err := s.Ready(r.Context()); err != nil {
			writeErrCode(w, r, http.StatusServiceUnavailable, "NOT_READY", err.Error(), nil)
			return
		}
	}
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("ready"))
}
