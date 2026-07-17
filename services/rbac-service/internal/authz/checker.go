package authz

import (
	"context"
	"log/slog"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/rbac-service/internal/projection"
	"github.com/windrose-ai/rbac-service/internal/store"
)

// Checker is the SQL ground-truth fallback path (RBC-FR-045): it loads a
// fresh snapshot, flattens it, evaluates it through the SAME Decide code the
// projection path uses (guaranteeing identical semantics), and warms the
// user's Redis keys so subsequent decisions go through OPA again.
type Checker struct {
	Store  *store.Store
	Writer *projection.RedisWriter // nil = no warming (tests)
	Lock   *projection.UserLock    // RBC-FR-048 per-user mutex; nil = no locking
	// OnFallback is invoked per fallback evaluation (metrics: sustained
	// fallback rate > 0.1% alerts).
	OnFallback func()
}

// Check evaluates an input against SQL ground truth and warms the projection.
func (c *Checker) Check(ctx context.Context, in Input) (Decision, error) {
	if c.OnFallback != nil {
		c.OnFallback()
	}
	tenant, err := uuid.Parse(in.Tenant)
	if err != nil {
		return Decision{Allowed: false, Reason: ReasonTenantMismatch}, nil
	}
	user := in.EffectiveUser()

	// The warm write must load its snapshot under the per-user lock so it never
	// interleaves with a concurrent worker recompute (RBC-FR-048). If the lock
	// is busy we still return a correct decision from an unlocked snapshot but
	// skip warming — the holder will warm it.
	warm := c.Writer != nil
	if warm && c.Lock != nil {
		token, ok, lerr := c.Lock.AcquireWait(ctx, in.Tenant, user, 500*time.Millisecond)
		if lerr != nil {
			return Decision{Allowed: false, Reason: ReasonDenyDefault}, lerr
		}
		if ok {
			defer c.Lock.Release(context.WithoutCancel(ctx), in.Tenant, user, token)
		} else {
			warm = false
		}
	}

	snap, err := c.Store.LoadSnapshot(ctx, tenant, user)
	if err != nil {
		return Decision{Allowed: false, Reason: ReasonDenyDefault}, err
	}
	flat := projection.Flatten(snap)
	reader := projection.NewFlatReader(flat, snap.Catalog, snap.ArchivedWorkspaceIDs)
	// Tenant autonomous enablement comes from the projection meta key when
	// available; SQL fallback has no tenant-flag table yet, so autonomous
	// agents deny on the fallback path (fail closed).
	d, err := Decide(ctx, in, reader)
	if err != nil {
		return d, err
	}

	// Warm keys (RBC-FR-045: fallback warms the key; RBC-FR-047 re-arms TTL).
	if warm {
		if werr := c.Writer.WriteUser(ctx, flat); werr != nil {
			slog.Warn("authz fallback: projection warm failed", "err", werr)
		} else {
			archived := make([]string, 0, len(snap.ArchivedWorkspaceIDs))
			for _, id := range snap.ArchivedWorkspaceIDs {
				archived = append(archived, id.String())
			}
			if werr := c.Writer.WriteArchivedWorkspaces(ctx, in.Tenant, archived, flat.Version); werr != nil {
				slog.Warn("authz fallback: archived_ws warm failed", "err", werr)
			}
			if werr := c.Writer.PublishInvalidate(ctx, in.Tenant, []string{flat.UserID}); werr != nil {
				slog.Warn("authz fallback: invalidate publish failed", "err", werr)
			}
		}
	}
	return d, nil
}
