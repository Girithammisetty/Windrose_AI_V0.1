// Package chain maintains the per-tenant-per-day tamper-evidence hash chain
// (AUD-FR-050): chain_hash = SHA-256(prev || event_id || payload_digest ||
// occurred_at), sequenced by a per-(tenant,day) monotonic counter. Correctness
// rests on three guarantees:
//
//   - Idempotent assignment (HIGH-1): the chain position for an event_id is
//     recorded durably BEFORE the ClickHouse insert, so a retry after a transient
//     ClickHouse failure (BR-6) reuses the SAME seq and re-attempts an idempotent
//     insert — never a phantom gap (AC-11).
//   - Distributed single-writer (HIGH-2): a Redis lock per (tenant, ingest-day)
//     serializes advances across replicas, so a tenant's events arriving on
//     different topic partitions/instances of the multi-topic ingest group can
//     never race the counter/head (BR-10).
//   - ClickHouse-anchored recovery: a cold counter reseeds from the durable
//     ClickHouse tip (max chain_seq), so Redis eviction/restart cannot regress or
//     duplicate the sequence.
//
// Ordering is the ingest sequence, not occurred_at (BR-2): the chain day is the
// UTC ingest day.
package chain

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"

	"github.com/windrose-ai/audit-service/internal/domain"
	"github.com/windrose-ai/audit-service/internal/pgstore"
	"github.com/windrose-ai/go-common/redisx"
)

// ChainTipper returns the durable chain tip (max seq + its hash) for a day.
// Satisfied by *chstore.Store.
type ChainTipper interface {
	ChainTip(ctx context.Context, tenant uuid.UUID, chainDate string) (uint64, string, bool, error)
}

// Manager appends events to the chain.
type Manager struct {
	redis *redisx.Client
	pg    *pgstore.Store
	tip   ChainTipper
	now   func() time.Time

	lockTTL time.Duration
	keyTTL  time.Duration
}

// New builds a Manager over real Redis + Postgres + the ClickHouse tip anchor.
func New(r *redisx.Client, pg *pgstore.Store, tip ChainTipper) *Manager {
	return &Manager{
		redis: r, pg: pg, tip: tip,
		now:     func() time.Time { return time.Now().UTC() },
		lockTTL: 15 * time.Second,
		keyTTL:  8 * 24 * time.Hour,
	}
}

// Link is the chain position assigned to an event.
type Link struct {
	ChainDate string `json:"date"`
	Seq       uint64 `json:"seq"`
	Hash      string `json:"hash"`
}

var releaseScript = redis.NewScript(`
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
end
return 0`)

// Append assigns (idempotently) the next chain position for an event.
func (m *Manager) Append(ctx context.Context, tenant, eventID uuid.UUID, payloadDigest string, occurredAt time.Time) (Link, error) {
	date := m.now().Format("2006-01-02")
	assignKey := fmt.Sprintf("audit:chain:assign:%s:%s", tenant, eventID)

	// Fast path: this event already has an assigned position → reuse it (HIGH-1).
	if link, ok, err := m.getAssignment(ctx, assignKey); err != nil {
		return Link{}, err
	} else if ok {
		return link, nil
	}

	// Distributed single-writer lock per (tenant, ingest-day) (HIGH-2).
	lockKey := fmt.Sprintf("audit:chain:lock:%s:%s", tenant, date)
	token := uuid.NewString()
	if err := m.acquire(ctx, lockKey, token); err != nil {
		return Link{}, err
	}
	defer func() { _ = releaseScript.Run(ctx, m.redis.R, []string{lockKey}, token).Err() }()

	// Re-check under the lock (another writer may have assigned it meanwhile).
	if link, ok, err := m.getAssignment(ctx, assignKey); err != nil {
		return Link{}, err
	} else if ok {
		return link, nil
	}

	seqKey := fmt.Sprintf("audit:chain:seq:%s:%s", tenant, date)
	headKey := fmt.Sprintf("audit:chain:head:%s:%s", tenant, date)

	// Seed cold counters from the DURABLE ClickHouse tip (authoritative), so a
	// phantom Redis seq from a prior failed insert cannot outrun the store.
	exists, err := m.redis.Exists(ctx, seqKey)
	if err != nil {
		return Link{}, fmt.Errorf("chain seq check: %w", err)
	}
	if !exists {
		seedSeq, seedHead, err := m.seed(ctx, tenant, date)
		if err != nil {
			return Link{}, err
		}
		if err := m.redis.Set(ctx, seqKey, seedSeq, m.keyTTL); err != nil {
			return Link{}, err
		}
		if err := m.redis.Set(ctx, headKey, seedHead, m.keyTTL); err != nil {
			return Link{}, err
		}
	}

	seq, err := m.redis.R.Incr(ctx, seqKey).Result()
	if err != nil {
		return Link{}, fmt.Errorf("chain seq incr: %w", err)
	}
	_ = m.redis.R.Expire(ctx, seqKey, m.keyTTL)

	prev, _, err := m.redis.Get(ctx, headKey)
	if err != nil {
		return Link{}, fmt.Errorf("chain head read: %w", err)
	}
	if prev == "" {
		prev = domain.GenesisHash(tenant, date)
	}
	hash := domain.ChainHash(prev, eventID, payloadDigest, occurredAt)
	link := Link{ChainDate: date, Seq: uint64(seq), Hash: hash}

	// Commit the position durably BEFORE the head advances and before the caller
	// inserts to ClickHouse: a retry of this exact event now reuses this link.
	if err := m.putAssignment(ctx, assignKey, link); err != nil {
		return Link{}, fmt.Errorf("chain assignment persist: %w", err)
	}
	if err := m.redis.Set(ctx, headKey, hash, m.keyTTL); err != nil {
		return Link{}, err
	}
	// Postgres checkpoint is best-effort (sealed tracking + unsealed listing);
	// it is NOT authoritative for the sequence, so a transient PG error must not
	// fail ingest or advance the seq on retry.
	if err := m.pg.UpsertChainHead(ctx, tenant, date, hash, uint64(seq)); err != nil {
		// swallowed: recovery reseeds from the ClickHouse tip, not from PG.
		_ = err
	}
	return link, nil
}

// seed computes the cold-start (seq, head) for a day from the durable store.
func (m *Manager) seed(ctx context.Context, tenant uuid.UUID, date string) (uint64, string, error) {
	if m.tip != nil {
		seq, hash, ok, err := m.tip.ChainTip(ctx, tenant, date)
		if err != nil {
			return 0, "", fmt.Errorf("chain tip: %w", err)
		}
		if ok {
			return seq, hash, nil
		}
	}
	return 0, domain.GenesisHash(tenant, date), nil
}

func (m *Manager) getAssignment(ctx context.Context, key string) (Link, bool, error) {
	raw, ok, err := m.redis.Get(ctx, key)
	if err != nil {
		return Link{}, false, err
	}
	if !ok {
		return Link{}, false, nil
	}
	var link Link
	if json.Unmarshal([]byte(raw), &link) != nil {
		return Link{}, false, nil
	}
	return link, true, nil
}

func (m *Manager) putAssignment(ctx context.Context, key string, link Link) error {
	b, _ := json.Marshal(link)
	return m.redis.Set(ctx, key, string(b), m.keyTTL)
}

// acquire spins on a Redis SETNX lock until held or ctx is cancelled.
func (m *Manager) acquire(ctx context.Context, key, token string) error {
	backoff := 5 * time.Millisecond
	for {
		ok, err := m.redis.R.SetNX(ctx, key, token, m.lockTTL).Result()
		if err != nil {
			return fmt.Errorf("chain lock: %w", err)
		}
		if ok {
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(backoff):
		}
		if backoff < 200*time.Millisecond {
			backoff *= 2
		}
	}
}

// VerifyResult is the outcome of a chain replay (AUD-FR-051).
type VerifyResult struct {
	Valid         bool    `json:"valid"`
	EventsChecked uint64  `json:"events_checked"`
	ChainHead     string  `json:"chain_head"`
	ManifestMatch bool    `json:"manifest_match"`
	FirstMismatch *uint64 `json:"first_mismatch_seq,omitempty"`
	Sealed        bool    `json:"sealed"`
}

// Verify recomputes the chain for (tenant, date) from the stored rows and
// compares to each row's stored chain_hash and the sealed head (AUD-FR-051). It
// also detects a broken sequence (gap/duplicate). Any mutation of
// payload_digest/occurred_at/ordering surfaces as a mismatch.
func Verify(rows []domain.Record, tenant uuid.UUID, date, sealedHead string) VerifyResult {
	res := VerifyResult{Valid: true}
	prev := domain.GenesisHash(tenant, date)
	var expectedSeq uint64 = 1
	for _, r := range rows {
		res.EventsChecked++
		if r.ChainSeq != expectedSeq {
			// Gap or duplicate in the sequence — the chain is not contiguous.
			seq := r.ChainSeq
			res.Valid = false
			res.FirstMismatch = &seq
			res.ChainHead = prev
			return res
		}
		want := domain.ChainHash(prev, r.EventID, r.PayloadDigest, r.OccurredAt)
		if want != r.ChainHash {
			seq := r.ChainSeq
			res.Valid = false
			res.FirstMismatch = &seq
			res.ChainHead = prev
			return res
		}
		prev = r.ChainHash
		expectedSeq++
	}
	res.ChainHead = prev
	if sealedHead != "" {
		res.ManifestMatch = prev == sealedHead
		if !res.ManifestMatch {
			res.Valid = false
		}
	}
	return res
}
