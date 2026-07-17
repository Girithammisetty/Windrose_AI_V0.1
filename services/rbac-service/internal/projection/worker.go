package projection

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/google/uuid"
)

// SnapshotLoader is implemented by the store; kept as an interface so the
// worker is unit-testable and the projection package stays store-agnostic.
type SnapshotLoader interface {
	LoadSnapshot(ctx context.Context, tenant uuid.UUID, userID string) (Snapshot, error)
	ClaimDirtyRows(ctx context.Context, workerID string, batch int, visibility time.Duration) ([]DirtyBatch, error)
	DeleteDirtyRows(ctx context.Context, ids []int64) error
	ArchivedWorkspaceIDs(ctx context.Context, tenant uuid.UUID) ([]uuid.UUID, error)
}

// DirtyBatch mirrors store.DirtyClaim without importing the store package.
type DirtyBatch struct {
	TenantID       uuid.UUID
	UserID         string
	IDs            []int64
	OldestEnqueued time.Time
}

// StalenessRecorder receives the end-to-end staleness measurement per
// recompute (enqueue -> keys written); RBC-FR-042: ≤ 5s p99, alerted.
type StalenessRecorder interface {
	Observe(d time.Duration)
}

// NopStaleness discards observations.
type NopStaleness struct{}

func (NopStaleness) Observe(time.Duration) {}

// Worker is the projection recompute worker (RBC-FR-042/048):
//
//   - claims dirty rows with SKIP LOCKED + a visibility timeout (crashed
//     workers' claims are reclaimed — BR-8, at-least-once);
//   - recompute is idempotent and per-user: all of a user's pending rows are
//     claimed and collapsed into one recompute;
//   - concurrent recomputes of the same user converge via versioned
//     last-writer-wins in the Redis writer;
//   - after writing, publishes perm.invalidate per tenant and records
//     staleness from the oldest enqueued marker.
type Worker struct {
	ID         string
	Loader     SnapshotLoader
	Writer     *RedisWriter
	Lock       *UserLock // RBC-FR-048 per-user recompute mutex (nil = disabled)
	Staleness  StalenessRecorder
	Interval   time.Duration // poll interval when queue is empty
	Batch      int
	Visibility time.Duration
	LockBudget time.Duration // max wait to acquire a user's lock before deferring
	Log        *slog.Logger
}

// errLockBusy signals a user is being recomputed by another worker; the caller
// leaves the dirty rows for a later pass rather than deleting them.
var errLockBusy = errors.New("projection: user recompute lock busy")

func NewWorker(id string, loader SnapshotLoader, writer *RedisWriter) *Worker {
	return &Worker{
		ID:         id,
		Loader:     loader,
		Writer:     writer,
		Staleness:  NopStaleness{},
		Interval:   250 * time.Millisecond,
		Batch:      256,
		Visibility: 30 * time.Second,
		LockBudget: 2 * time.Second,
		Log:        slog.Default(),
	}
}

// Run polls until ctx is cancelled.
func (w *Worker) Run(ctx context.Context) {
	for {
		n, err := w.ProcessOnce(ctx)
		if err != nil && ctx.Err() == nil {
			w.Log.Error("projection worker pass failed", "err", err)
		}
		if ctx.Err() != nil {
			return
		}
		if n == 0 {
			select {
			case <-ctx.Done():
				return
			case <-time.After(w.Interval):
			}
		}
	}
}

// ProcessOnce claims and processes one batch; returns users recomputed.
func (w *Worker) ProcessOnce(ctx context.Context) (int, error) {
	claims, err := w.Loader.ClaimDirtyRows(ctx, w.ID, w.Batch, w.Visibility)
	if err != nil {
		return 0, err
	}
	if len(claims) == 0 {
		return 0, nil
	}
	invalidate := map[uuid.UUID][]string{}
	archivedDone := map[uuid.UUID]bool{}
	processed := 0
	for _, c := range claims {
		if err := w.recomputeUser(ctx, c, archivedDone); err != nil {
			// Leave rows claimed; the visibility timeout re-queues them.
			if !errors.Is(err, errLockBusy) {
				w.Log.Error("recompute failed", "tenant", c.TenantID, "user", c.UserID, "err", err)
			}
			continue
		}
		processed++
		invalidate[c.TenantID] = append(invalidate[c.TenantID], c.UserID)
		w.Staleness.Observe(time.Since(c.OldestEnqueued))
	}
	for tenant, users := range invalidate {
		if err := w.Writer.PublishInvalidate(ctx, tenant.String(), users); err != nil {
			w.Log.Warn("perm.invalidate publish failed", "tenant", tenant, "err", err)
		}
	}
	return processed, nil
}

func (w *Worker) recomputeUser(ctx context.Context, c DirtyBatch, archivedDone map[uuid.UUID]bool) error {
	// Serialize recompute per user and load the snapshot UNDER the lock, so its
	// version is monotonic with respect to the write order (RBC-FR-048): no two
	// workers can interleave load/write for the same user.
	if w.Lock != nil {
		token, ok, err := w.Lock.AcquireWait(ctx, c.TenantID.String(), c.UserID, w.LockBudget)
		if err != nil {
			return err
		}
		if !ok {
			return errLockBusy
		}
		defer w.Lock.Release(context.WithoutCancel(ctx), c.TenantID.String(), c.UserID, token)
	}
	snap, err := w.Loader.LoadSnapshot(ctx, c.TenantID, c.UserID)
	if err != nil {
		return err
	}
	flat := Flatten(snap)
	if err := w.Writer.WriteUser(ctx, flat); err != nil {
		return err
	}
	// Maintain the tenant-level archived_ws key once per tenant per pass.
	if !archivedDone[c.TenantID] {
		archived := make([]string, 0, len(snap.ArchivedWorkspaceIDs))
		for _, id := range snap.ArchivedWorkspaceIDs {
			archived = append(archived, id.String())
		}
		if err := w.Writer.WriteArchivedWorkspaces(ctx, c.TenantID.String(), archived, snap.Version); err != nil {
			return err
		}
		archivedDone[c.TenantID] = true
	}
	return w.Loader.DeleteDirtyRows(ctx, c.IDs)
}
