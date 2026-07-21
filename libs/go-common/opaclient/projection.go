package opaclient

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"

	"github.com/windrose-ai/go-common/redisx"
)

// ProjectionLoader reads the permissions_flat projection slice for one decision
// from Redis, using the rbac-service key scheme (RBC-FR-040). This is what a
// service's request path uses to feed OPA (MASTER-FR-012): O(1) key reads, no
// synchronous rbac call.
type ProjectionLoader struct {
	R *redisx.Client
}

// NewLoader builds a loader over a redisx client.
func NewLoader(r *redisx.Client) *ProjectionLoader { return &ProjectionLoader{R: r} }

// URNHash is the resource-grant key suffix: sha256(urn) hex, first 32 chars.
// Matches rbac domain.URNHash and the Rego crypto.sha256 substring.
func URNHash(urn string) string {
	sum := sha256.Sum256([]byte(urn))
	return hex.EncodeToString(sum[:])[:32]
}

// EffectiveUser resolves whose projection is read (OBO → original user).
func EffectiveUser(s Subject) string {
	if s.Typ == "agent_obo" && s.OboSub != "" {
		return s.OboSub
	}
	return s.ID
}

// Load builds the Projection facts for (tenant, action, workspace, resource)
// from Redis. Tombstoned subsidiary keys (deleted=true) read as absent.
func (l *ProjectionLoader) Load(ctx context.Context, in *Input) (Projection, error) {
	tenant := in.Tenant
	user := EffectiveUser(in.Subject)
	var p Projection

	// Catalog: action -> workspace_scoped (global key, no TTL).
	if raw, ok, err := l.R.Get(ctx, "perm:catalog:actions"); err != nil {
		return p, err
	} else if ok {
		var cv struct {
			Actions map[string]bool `json:"actions"`
		}
		if json.Unmarshal([]byte(raw), &cv) == nil {
			scoped, known := cv.Actions[in.Action]
			p.ActionKnown = known
			p.ActionScoped = scoped
		}
	}

	// Flags.
	if raw, ok, err := l.R.Get(ctx, key("perm:%s:%s:flags", tenant, user)); err != nil {
		return p, err
	} else if ok {
		var fv struct {
			Admin   bool     `json:"admin"`
			WsAdmin []string `json:"ws_admin"`
		}
		if json.Unmarshal([]byte(raw), &fv) == nil {
			p.Flags = Flags{Found: true, Admin: fv.Admin, WsAdmin: emptyIfNil(fv.WsAdmin)}
		}
	}

	// Tenant-scoped actions.
	if raw, ok, err := l.R.Get(ctx, key("perm:%s:%s:actions", tenant, user)); err != nil {
		return p, err
	} else if ok {
		var av struct {
			Actions []string `json:"actions"`
		}
		if json.Unmarshal([]byte(raw), &av) == nil {
			p.TenantActions = TenantActions{Found: true, Actions: emptyIfNil(av.Actions)}
		}
	}

	// Workspace entry for the request's workspace.
	if in.WorkspaceID != "" {
		if raw, ok, err := l.R.Get(ctx, fmt.Sprintf("perm:%s:%s:ws:%s", tenant, user, in.WorkspaceID)); err != nil {
			return p, err
		} else if ok {
			var wv struct {
				Actions  []string `json:"actions"`
				Archived bool     `json:"archived"`
				Deleted  bool     `json:"deleted"`
			}
			if json.Unmarshal([]byte(raw), &wv) == nil && !wv.Deleted {
				p.Workspace = WorkspaceFacts{Assigned: true, Actions: emptyIfNil(wv.Actions), Archived: wv.Archived}
			}
		}
		// Is this workspace archived at the tenant level (admin write block, BR-7)?
		if raw, ok, err := l.R.Get(ctx, fmt.Sprintf("perm:%s:archived_ws", tenant)); err != nil {
			return p, err
		} else if ok {
			var aw struct {
				Workspaces []string `json:"ws"`
			}
			if json.Unmarshal([]byte(raw), &aw) == nil {
				for _, w := range aw.Workspaces {
					if w == in.WorkspaceID {
						p.WorkspaceArchivedTenant = true
						break
					}
				}
			}
		}
	}

	// Resource grant for the request's URN.
	if in.ResourceURN != "" {
		h := URNHash(in.ResourceURN)
		if raw, ok, err := l.R.Get(ctx, fmt.Sprintf("perm:%s:%s:res:%s", tenant, user, h)); err != nil {
			return p, err
		} else if ok {
			var rv struct {
				Level    string `json:"level"`
				Archived bool   `json:"archived"`
				Deleted  bool   `json:"deleted"`
			}
			if json.Unmarshal([]byte(raw), &rv) == nil && !rv.Deleted {
				p.Resource = ResourceFacts{Found: true, Level: rv.Level, Archived: rv.Archived}
			}
		}
	}

	// Tenant meta: autonomous-agent enablement.
	if raw, ok, err := l.R.Get(ctx, fmt.Sprintf("perm:%s:meta", tenant)); err != nil {
		return p, err
	} else if ok {
		var mv struct {
			AutonomousEnabled bool `json:"autonomous_enabled"`
		}
		if json.Unmarshal([]byte(raw), &mv) == nil {
			p.AutonomousEnabled = mv.AutonomousEnabled
		}
	}

	return p, nil
}

// CheckWithRedis loads the projection from Redis then evaluates it against OPA
// — the full request-path decision (MASTER-FR-012). When the projection came
// back empty (a Redis miss — cold cache after a restart/failover/flush, not a
// genuine "no grant") and a fallback is configured (EnableMissFallback,
// RBC-FR-045), this re-checks against rbac-service's SQL ground truth instead
// of returning the empty-projection deny — see withMissFallback.
func (c *Client) CheckWithRedis(ctx context.Context, l *ProjectionLoader, in Input) (Decision, error) {
	p, err := l.Load(ctx, &in)
	if err != nil {
		return Decision{}, err
	}
	in.Projection = p
	dec, err := c.Check(ctx, in)
	if err != nil {
		return dec, err
	}
	return c.withMissFallback(ctx, in, dec), nil
}

func key(f, tenant, user string) string { return fmt.Sprintf(f, tenant, user) }

func emptyIfNil(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}
