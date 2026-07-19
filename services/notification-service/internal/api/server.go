package api

import (
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/windrose-ai/go-common/authjwt"
	"github.com/windrose-ai/go-common/metricsx"
	"github.com/windrose-ai/notification-service/internal/authz"
	"github.com/windrose-ai/notification-service/internal/channels/email"
	"github.com/windrose-ai/notification-service/internal/channels/webhook"
	"github.com/windrose-ai/notification-service/internal/registry"
	"github.com/windrose-ai/notification-service/internal/reports"
	"github.com/windrose-ai/notification-service/internal/store"
)

// Server wires the HTTP layer (dependency container).
type Server struct {
	Store         *store.PG
	Authz         authz.Authorizer
	Verifier      *authjwt.Verifier
	Registry      *registry.Registry
	WebhookSender *webhook.Sender
	// EmailProviders maps provider name → driver for status-callback parsing.
	EmailProviders map[string]email.Provider
	// Reports drives the real Temporal Schedule backing each report
	// subscription (NOTIF-FR-060). nil in unit tests / deployments without
	// TEMPORAL_HOSTPORT configured — CRUD then persists rows but reports
	// EValidation on create/update rather than silently no-op scheduling.
	Reports *reports.Scheduler
	// Ready reports whether deploy-time bootstrap (action-catalog registration
	// with rbac) has completed. Until it returns true /readyz is 503, so a
	// notification-service whose actions never registered — which would 403 every
	// guarded route incl. the inbox (FIX 4) — fails loud instead of looking
	// healthy. nil means "not gated" (unit tests).
	Ready func() bool
}

// Router builds the chi router (base path /api/v1, MASTER-FR-020).
func (s *Server) Router() http.Handler {
	r := chi.NewRouter()
	// RED metrics (MASTER-FR-050): real /metrics + per-request rate/errors/
	// duration via the shared middleware, replacing the bare runtime-only stub.
	metrics := metricsx.New("notification-service")
	r.Use(TraceMiddleware, RecoverMiddleware, metrics.Middleware(chiRoutePattern))

	// Health + metrics (MASTER-FR-051), unauthenticated.
	r.Get("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	r.Get("/readyz", func(w http.ResponseWriter, r *http.Request) {
		if err := s.Store.Ping(r.Context()); err != nil {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		if s.Ready != nil && !s.Ready() {
			// Action manifest not yet registered with rbac: guarded routes would
			// 403. Report not-ready until registration completes (fail loud).
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
	})
	r.Handle("/metrics", metrics.Handler())

	// Provider status callbacks are authenticated by the provider's own signed
	// payload (allowlisted per provider, BR-13), not by a Windrose JWT.
	r.Post("/api/v1/providers/{provider}/status", s.handleProviderStatus)

	r.Route("/api/v1", func(r chi.Router) {
		r.Use(s.Verifier.Middleware(func(w http.ResponseWriter, req *http.Request, _ error) {
			writeErrUnauth(w, req)
		}), s.IdempotencyMiddleware)

		// Inbox (NOTIF-FR-020).
		r.With(s.RequireAction(authz.ActionInboxRead)).Get("/notifications", s.handleListNotifications)
		r.With(s.RequireAction(authz.ActionInboxRead)).Get("/notifications/unread-count", s.handleUnreadCount)
		r.With(s.RequireAction(authz.ActionInboxRead)).Get("/notifications/{id}", s.handleGetNotification)
		r.With(s.RequireAction(authz.ActionInboxRead)).Post("/notifications/{id}/read", s.handleMarkRead)
		r.With(s.RequireAction(authz.ActionInboxRead)).Post("/notifications/{id}/unread", s.handleMarkUnread)
		r.With(s.RequireAction(authz.ActionInboxRead)).Post("/notifications/mark-all-read", s.handleMarkAllRead)

		// Preferences (NOTIF-FR-012).
		r.With(s.RequireAction(authz.ActionPrefRead)).Get("/preferences", s.handleGetPreferences)
		r.With(s.RequireAction(authz.ActionPrefUpdate)).Put("/preferences", s.handlePutPreferences)

		// Subscription rules (NOTIF-FR-010).
		r.With(s.RequireAction(authz.ActionRuleRead)).Get("/rules", s.handleListRules)
		r.With(s.RequireAction(authz.ActionRuleCreate)).Post("/rules", s.handleCreateRule)
		r.With(s.RequireAction(authz.ActionRuleRead)).Get("/rules/{id}", s.handleGetRule)
		r.With(s.RequireAction(authz.ActionRuleUpdate)).Patch("/rules/{id}", s.handleUpdateRule)
		r.With(s.RequireAction(authz.ActionRuleDelete)).Delete("/rules/{id}", s.handleDeleteRule)

		// Scheduled dashboard report subscriptions (NOTIF-FR-060).
		r.With(s.RequireAction(authz.ActionReportRead)).Get("/reports", s.handleListReports)
		r.With(s.RequireAction(authz.ActionReportCreate)).Post("/reports", s.handleCreateReport)
		r.With(s.RequireAction(authz.ActionReportRead)).Get("/reports/{id}", s.handleGetReport)
		r.With(s.RequireAction(authz.ActionReportUpdate)).Patch("/reports/{id}", s.handleUpdateReport)
		r.With(s.RequireAction(authz.ActionReportDelete)).Delete("/reports/{id}", s.handleDeleteReport)
		r.With(s.RequireAction(authz.ActionReportUpdate)).Post("/reports/{id}/trigger", s.handleTriggerReport)

		// Webhooks (NOTIF-FR-022/024).
		r.With(s.RequireAction(authz.ActionWebhookRead)).Get("/webhooks", s.handleListWebhooks)
		r.With(s.RequireAction(authz.ActionWebhookCreate)).Post("/webhooks", s.handleCreateWebhook)
		r.With(s.RequireAction(authz.ActionWebhookRead)).Get("/webhooks/{id}", s.handleGetWebhook)
		r.With(s.RequireAction(authz.ActionWebhookUpdate)).Patch("/webhooks/{id}", s.handleUpdateWebhook)
		r.With(s.RequireAction(authz.ActionWebhookDelete)).Delete("/webhooks/{id}", s.handleDeleteWebhook)
		r.With(s.RequireAction(authz.ActionWebhookUpdate)).Post("/webhooks/{id}/rotate-secret", s.handleRotateSecret)
		r.With(s.RequireAction(authz.ActionWebhookRead)).Get("/webhooks/{id}/deliveries", s.handleListDeliveries)
		r.With(s.RequireAction(authz.ActionWebhookExecute)).Post("/webhooks/{id}/deliveries/{did}/redeliver", s.handleRedeliver)

		// Templates (NOTIF-FR-040/041).
		r.With(s.RequireAction(authz.ActionTemplateRead)).Get("/templates", s.handleListTemplates)
		r.With(s.RequireAction(authz.ActionTemplateCreate)).Post("/templates", s.handleCreateTemplate)
		r.With(s.RequireAction(authz.ActionTemplateUpdate)).Post("/templates/{key}/publish", s.handlePublishTemplate)
		r.With(s.RequireAction(authz.ActionTemplateRead)).Post("/templates/{key}/preview", s.handlePreviewTemplate)

		// Ops (NOTIF-FR-051).
		r.With(s.RequireAction(authz.ActionAdminRead)).Get("/admin/stats", s.handleStats)
		r.With(s.RequireAction(authz.ActionAdminRead)).Get("/admin/suppressions", s.handleListSuppressions)
		r.With(s.RequireAction(authz.ActionSuppressionDelete)).Delete("/admin/suppressions", s.handleClearSuppression)
	})
	return r
}

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

func writeErrUnauth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusUnauthorized, ErrorBody{Error: ErrorDetail{Code: "UNAUTHENTICATED", Message: "invalid or missing token", TraceID: traceID(r.Context())}})
}
