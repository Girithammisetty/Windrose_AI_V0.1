package authz

import (
	"context"
	"fmt"
	"log/slog"
	"os"

	"github.com/windrose-ai/go-common/opaclient"
	"github.com/windrose-ai/go-common/redisx"
	"github.com/windrose-ai/realtime-hub/internal/topics"
)

// SessionRegistry resolves chat-session ownership (RTH-FR-003). agent-runtime
// maintains this projection in Redis; the hub only reads it.
type SessionRegistry interface {
	SessionOwner(ctx context.Context, tenant, sessionID string) (owner string, ok bool)
}

// OPAAuthorizer is the real runtime authorizer (RTH-FR-012). run-status and
// proposal topics are decided by the local OPA sidecar over the caller's Redis
// permissions_flat projection; notifications and chat use the structural rules
// (owner-only / session-ownership). It fails closed on any transport error and
// on cross-tenant URNs (no existence leak, RTH-FR-012 / BR-3).
type OPAAuthorizer struct {
	client   *opaclient.Client
	loader   *opaclient.ProjectionLoader
	sessions SessionRegistry
}

// NewOPAAuthorizer builds the authorizer over the OPA sidecar (opaURL, e.g.
// http://localhost:8281) and a Redis projection loader + session registry
// (redisAddr, host:port).
func NewOPAAuthorizer(opaURL, redisAddr string) *OPAAuthorizer {
	if opaURL == "" {
		opaURL = "http://localhost:8281"
	}
	r := redisx.NewFromEnv(redisAddr, os.Getenv)
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
	return &OPAAuthorizer{
		client:   client,
		loader:   opaclient.NewLoader(r),
		sessions: &redisSessions{r: r},
	}
}

// Authorize implements Authorizer against real infra.
func (a *OPAAuthorizer) Authorize(ctx context.Context, req Request) Decision {
	switch req.Topic.Scheme {
	case topics.SchemeNotifications:
		// Owner-only, never grantable (RTH-FR-003 / BR-4).
		if req.Subject.isService() || req.Subject.EffectiveUser() == req.Topic.Ident {
			return Decision{Allow: true, Reason: "allowed"}
		}
		return Decision{Allow: false, Reason: "not_owner"}

	case topics.SchemeChat:
		// Session ownership from the agent-runtime Redis projection.
		if req.Subject.isService() {
			return Decision{Allow: true, Reason: "allowed"}
		}
		owner, ok := a.sessions.SessionOwner(ctx, req.Tenant, req.Topic.Ident)
		if ok && owner == req.Subject.EffectiveUser() {
			return Decision{Allow: true, Reason: "allowed"}
		}
		return Decision{Allow: false, Reason: "not_session_owner"}

	case topics.SchemeAgentRun:
		// Agent-run token stream (agent-runtime publishes here per ART-FR-070).
		// The subscription key is already tenant-scoped from the verified JWT
		// (BR-3), and the run id is an unguessable UUID capability the caller
		// only learns from its own chat_completions response — the same
		// structural-capability model as notifications/chat. So any
		// authenticated principal in the tenant may attach to a run stream it
		// holds the id for; cross-tenant is prevented by the tenant key.
		return Decision{Allow: true, Reason: "allowed"}

	case topics.SchemeRunStatus:
		urn := req.Topic.Ident
		// Structural cross-tenant guard: a URN naming another tenant is treated
		// as not-found (BR-3 / RTH-FR-012), identical to a nonexistent URN.
		if tt := topics.URNTenant(urn); tt != "" && tt != req.Tenant {
			return Decision{Allow: false, Reason: "cross_tenant"}
		}
		return a.opaCheck(ctx, req, ActionRunStatusRead, urn)

	case topics.SchemeProposal:
		urn := topics.ProposalURN(req.Tenant, req.Topic.Ident)
		return a.opaCheck(ctx, req, ActionProposalRead, urn)

	case topics.SchemeList:
		// Tenant-wide list broadcast (task #80). Gated on the same tenant-scoped
		// realtime.run_status.read capability as run-status (a member who may
		// watch live run status may watch live list status) — no new RBAC
		// action/reseed, and the privilege class is identical. There is no
		// per-resource URN (the ident is a type slug); the tenant subscription
		// key already isolates cross-tenant, and the payload carries only row
		// status the caller re-authorizes by refetching its RBAC-scoped list.
		return a.opaCheck(ctx, req, ActionRunStatusRead, "")

	default:
		return Decision{Allow: false, Reason: "INVALID_TOPIC"}
	}
}

func (a *OPAAuthorizer) opaCheck(ctx context.Context, req Request, action, urn string) Decision {
	in := opaclient.Input{
		Subject:     toOPASubject(req.Subject),
		Action:      action,
		ResourceURN: urn,
		Tenant:      req.Tenant,
	}
	dec, err := a.client.CheckWithRedis(ctx, a.loader, in)
	if err != nil {
		// Fail closed (MASTER-FR-012; failure-mode matrix: OPA/Redis down →
		// new subscribes fail-closed as TOPIC_FORBIDDEN).
		return Decision{Allow: false, Reason: "authz_unavailable"}
	}
	if dec.Allow {
		return Decision{Allow: true, Reason: "allowed"}
	}
	reason := dec.Reason
	if reason == "" {
		reason = "deny_default"
	}
	return Decision{Allow: false, Reason: reason}
}

// redisSessions reads chat-session ownership from the agent-runtime projection
// key scheme rt:session:<tenant>/<session_id> -> owner sub.
type redisSessions struct{ r *redisx.Client }

func (s *redisSessions) SessionOwner(ctx context.Context, tenant, sessionID string) (string, bool) {
	v, ok, err := s.r.Get(ctx, fmt.Sprintf("rt:session:%s/%s", tenant, sessionID))
	if err != nil || !ok {
		return "", false
	}
	return v, true
}
