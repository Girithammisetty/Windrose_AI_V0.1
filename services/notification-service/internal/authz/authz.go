// Package authz is notification-service's authorization surface. The real
// runtime adapter (OPAClient) loads the caller's permissions_flat projection
// from Redis and evaluates it against the local OPA sidecar via the shared
// go-common client (MASTER-FR-012) — it never calls rbac-service synchronously.
// The AllowAll / Static doubles are for unit tests only.
package authz

import (
	"context"
	"log/slog"
	"os"

	"github.com/windrose-ai/go-common/opaclient"
	"github.com/windrose-ai/go-common/redisx"
)

// Actions (MASTER-FR-016 naming: <service>.<resource>.<verb>). Every verb is
// drawn from rbac's CANONICAL closed set (read/list/create/update/delete/
// execute/assign/approve/admin/export/share) so rbac's ParseAction accepts the
// whole registered batch (RBC-FR-022) — a non-canonical verb like "manage"
// would make RegisterActions reject the batch, leaving action_known=false and
// every guarded route 403. api_drift_test asserts guarded == registered ==
// canonical.
const (
	// Subscription rules (NOTIF-FR-010).
	ActionRuleCreate = "notification.rule.create"
	ActionRuleRead   = "notification.rule.read"
	ActionRuleUpdate = "notification.rule.update"
	ActionRuleDelete = "notification.rule.delete"
	// Webhooks (NOTIF-FR-022/024).
	ActionWebhookCreate  = "notification.webhook.create"
	ActionWebhookRead    = "notification.webhook.read"
	ActionWebhookUpdate  = "notification.webhook.update" // patch + rotate-secret
	ActionWebhookDelete  = "notification.webhook.delete"
	ActionWebhookExecute = "notification.webhook.execute" // manual redeliver
	// Templates (NOTIF-FR-040/041).
	ActionTemplateCreate = "notification.template.create"
	ActionTemplateRead   = "notification.template.read"   // list + preview
	ActionTemplateUpdate = "notification.template.update" // publish
	// Preferences (NOTIF-FR-012).
	ActionPrefRead   = "notification.preference.read"
	ActionPrefUpdate = "notification.preference.update"
	// Inbox (NOTIF-FR-020) — BRD-named action; covers own-inbox reads + marks.
	ActionInboxRead = "notification.inbox.read"
	// Ops (NOTIF-FR-051).
	ActionAdminRead         = "notification.admin.read"
	ActionSuppressionDelete = "notification.suppression.delete"
	// Scheduled dashboard report subscriptions (NOTIF-FR-060). Update also
	// guards pause/resume (mirrors ActionWebhookUpdate's "patch + rotate" shape).
	ActionReportCreate = "notification.report.create"
	ActionReportRead   = "notification.report.read"
	ActionReportUpdate = "notification.report.update"
	ActionReportDelete = "notification.report.delete"
)

// Input is an authorization request.
type Input struct {
	Subject     Subject
	Action      string
	ResourceURN string
	WorkspaceID string
	Tenant      string
}

// Subject mirrors the OPA input subject (MASTER-FR-011).
type Subject struct {
	ID     string
	Typ    string
	OboSub string
	Scopes []string
}

// Authorizer decides whether an action is allowed.
type Authorizer interface {
	Allow(ctx context.Context, in Input) bool
}

// OPAClient is the real authorizer (MASTER-FR-012). Fails closed on error.
type OPAClient struct {
	client *opaclient.Client
	loader *opaclient.ProjectionLoader
}

// NewOPAClient builds the real OPA-backed authorizer.
func NewOPAClient(opaURL, redisAddr string) *OPAClient {
	if opaURL == "" {
		opaURL = "http://localhost:8281"
	}
	client := opaclient.New(opaURL)
	// Redis-miss fallback (RBC-FR-045): a Redis restart/failover must not
	// deny the whole tenant until an operator manually runs
	// deploy/local/reconcile.sh — self-heal from rbac-service's real SQL
	// ground truth instead. Reuses the same credential the deploy-time
	// action-catalog registration already uses (RBAC_URL/REGISTER_SIGNING_*).
	if cfg, ok := opaclient.FallbackConfigFromEnv(); ok {
		if err := client.EnableMissFallback(cfg); err != nil {
			slog.Warn("opaclient: miss-fallback signing key invalid, Redis-miss will deny (RBC-FR-045)", "err", err)
		}
	}
	return &OPAClient{
		client: client,
		loader: opaclient.NewLoader(redisx.NewFromEnv(redisAddr, os.Getenv)),
	}
}

// Allow loads the projection from Redis then evaluates it against OPA.
func (o *OPAClient) Allow(ctx context.Context, in Input) bool {
	oi := opaclient.Input{
		Subject:     opaclient.Subject{ID: in.Subject.ID, Typ: in.Subject.Typ, OboSub: in.Subject.OboSub, Scopes: in.Subject.Scopes},
		Action:      in.Action,
		ResourceURN: in.ResourceURN,
		WorkspaceID: in.WorkspaceID,
		Tenant:      in.Tenant,
	}
	dec, err := o.client.CheckWithRedis(ctx, o.loader, oi)
	if err != nil {
		return false
	}
	return dec.Allow
}

// AllowAll permits everything (unit tests only).
type AllowAll struct{}

// Allow always returns true.
func (AllowAll) Allow(context.Context, Input) bool { return true }

// Static denies the actions in Denied (unit tests only).
type Static struct{ Denied map[string]bool }

// Allow returns false for denied actions.
func (s Static) Allow(_ context.Context, in Input) bool { return !s.Denied[in.Action] }
