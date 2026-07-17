package store

import (
	"context"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/query-service/internal/domain"
	"github.com/windrose-ai/query-service/internal/events"
)

// Mem is the in-memory Store used by the unit tier. It applies the same
// tenant-isolation policy as Postgres RLS: every read/write is filtered by
// the tenant argument, so cross-tenant access yields ErrNotFound
// (MASTER-FR-003) exactly like the RLS-backed store.
type Mem struct {
	mu          sync.Mutex
	queries     map[uuid.UUID]*domain.SavedQuery
	versions    map[uuid.UUID][]*domain.SavedQueryVersion // by saved_query_id
	executions  map[uuid.UUID]*domain.Execution
	limits      map[uuid.UUID]*domain.TenantLimits
	idempotency map[string]memIdem // tenant|key
	outbox      []memOutboxRow
	nextOutbox  int64
	Now         func() time.Time
}

type memIdem struct {
	rec IdempotencyRecord
	at  time.Time
}

type memOutboxRow struct {
	id        int64
	env       events.Envelope
	published bool
}

// NewMem builds an empty in-memory store.
func NewMem() *Mem {
	return &Mem{
		queries:     map[uuid.UUID]*domain.SavedQuery{},
		versions:    map[uuid.UUID][]*domain.SavedQueryVersion{},
		executions:  map[uuid.UUID]*domain.Execution{},
		limits:      map[uuid.UUID]*domain.TenantLimits{},
		idempotency: map[string]memIdem{},
		nextOutbox:  1,
		Now:         time.Now,
	}
}

func (m *Mem) appendOutboxLocked(envs []events.Envelope) {
	for _, env := range envs {
		m.outbox = append(m.outbox, memOutboxRow{id: m.nextOutbox, env: env})
		m.nextOutbox++
	}
}

// ---- Saved queries ----------------------------------------------------------

func (m *Mem) CreateSavedQuery(_ context.Context, op domain.Op, sq *domain.SavedQuery, v *domain.SavedQueryVersion, envs []events.Envelope) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, q := range m.queries {
		if q.TenantID == op.Tenant && q.WorkspaceID == sq.WorkspaceID && q.DeletedAt == nil &&
			strings.EqualFold(q.Name, sq.Name) {
			return ErrNameConflict
		}
	}
	cq, cv := *sq, *v
	m.queries[sq.ID] = &cq
	m.versions[sq.ID] = append(m.versions[sq.ID], &cv)
	m.appendOutboxLocked(envs)
	return nil
}

func (m *Mem) GetSavedQuery(_ context.Context, tenant, id uuid.UUID) (*domain.SavedQuery, *domain.SavedQueryVersion, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	q, ok := m.queries[id]
	if !ok || q.TenantID != tenant || q.DeletedAt != nil {
		return nil, nil, ErrNotFound
	}
	cq := *q
	for _, v := range m.versions[id] {
		if v.VersionNo == q.CurrentVersionNo {
			cv := *v
			return &cq, &cv, nil
		}
	}
	return &cq, nil, nil
}

func (m *Mem) GetVersion(_ context.Context, tenant, id uuid.UUID, versionNo int) (*domain.SavedQueryVersion, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	q, ok := m.queries[id]
	if !ok || q.TenantID != tenant || q.DeletedAt != nil {
		return nil, ErrNotFound
	}
	for _, v := range m.versions[id] {
		if v.VersionNo == versionNo {
			cv := *v
			return &cv, nil
		}
	}
	return nil, ErrNotFound
}

func (m *Mem) ListSavedQueries(_ context.Context, tenant uuid.UUID, f SavedQueryFilter) (Page[*domain.SavedQuery], error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var all []*domain.SavedQuery
	for _, q := range m.queries {
		if q.TenantID != tenant || q.DeletedAt != nil {
			continue
		}
		if f.WorkspaceID != nil && q.WorkspaceID != *f.WorkspaceID {
			continue
		}
		cq := *q
		all = append(all, &cq)
	}
	sort.Slice(all, func(i, j int) bool { return all[i].ID.String() > all[j].ID.String() })
	return paginateByID(all, f.Cursor, ClampLimit(f.Limit), func(q *domain.SavedQuery) string { return q.ID.String() })
}

func (m *Mem) ListVersions(_ context.Context, tenant, id uuid.UUID, limit int, cursor string) (Page[*domain.SavedQueryVersion], error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	q, ok := m.queries[id]
	if !ok || q.TenantID != tenant || q.DeletedAt != nil {
		return Page[*domain.SavedQueryVersion]{}, ErrNotFound
	}
	var all []*domain.SavedQueryVersion
	for _, v := range m.versions[id] {
		cv := *v
		all = append(all, &cv)
	}
	sort.Slice(all, func(i, j int) bool { return all[i].VersionNo > all[j].VersionNo })
	return paginateByID(all, cursor, ClampLimit(limit), func(v *domain.SavedQueryVersion) string { return v.ID.String() })
}

func (m *Mem) UpdateSavedQuery(_ context.Context, op domain.Op, sq *domain.SavedQuery, v *domain.SavedQueryVersion, expectVersion int, envs []events.Envelope) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	cur, ok := m.queries[sq.ID]
	if !ok || cur.TenantID != op.Tenant || cur.DeletedAt != nil {
		return ErrNotFound
	}
	if cur.CurrentVersionNo != expectVersion {
		return ErrStaleVersion
	}
	for _, q := range m.queries {
		if q.ID != sq.ID && q.TenantID == op.Tenant && q.WorkspaceID == sq.WorkspaceID && q.DeletedAt == nil &&
			strings.EqualFold(q.Name, sq.Name) {
			return ErrNameConflict
		}
	}
	cq := *sq
	m.queries[sq.ID] = &cq
	if v != nil {
		cv := *v
		m.versions[sq.ID] = append(m.versions[sq.ID], &cv)
	}
	m.appendOutboxLocked(envs)
	return nil
}

func (m *Mem) SoftDeleteSavedQuery(_ context.Context, op domain.Op, id uuid.UUID, envs []events.Envelope) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	q, ok := m.queries[id]
	if !ok || q.TenantID != op.Tenant || q.DeletedAt != nil {
		return ErrNotFound
	}
	now := m.Now().UTC()
	q.DeletedAt = &now
	m.appendOutboxLocked(envs)
	return nil
}

// ---- Executions -------------------------------------------------------------

func (m *Mem) InsertExecution(_ context.Context, op domain.Op, e *domain.Execution, envs []events.Envelope) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	ce := *e
	m.executions[e.ID] = &ce
	m.appendOutboxLocked(envs)
	return nil
}

func (m *Mem) UpdateExecution(_ context.Context, tenant, id uuid.UUID, apply func(e *domain.Execution) ([]events.Envelope, error)) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	e, ok := m.executions[id]
	if !ok || e.TenantID != tenant {
		return ErrNotFound
	}
	envs, err := apply(e)
	if err != nil {
		return err
	}
	m.appendOutboxLocked(envs)
	return nil
}

func (m *Mem) GetExecution(_ context.Context, tenant, id uuid.UUID) (*domain.Execution, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	e, ok := m.executions[id]
	if !ok || e.TenantID != tenant {
		return nil, ErrNotFound
	}
	ce := *e
	return &ce, nil
}

func (m *Mem) ListExecutions(_ context.Context, tenant uuid.UUID, f ExecutionFilter) (Page[*domain.Execution], error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var all []*domain.Execution
	for _, e := range m.executions {
		if e.TenantID != tenant {
			continue
		}
		if f.Status != "" && e.Status != f.Status {
			continue
		}
		if f.User != "" && e.CreatedBy != f.User {
			continue
		}
		if f.SavedQueryID != nil && (e.SavedQueryID == nil || *e.SavedQueryID != *f.SavedQueryID) {
			continue
		}
		if f.Since != nil && e.CreatedAt.Before(*f.Since) {
			continue
		}
		ce := *e
		all = append(all, &ce)
	}
	if f.SortByCost {
		sort.Slice(all, func(i, j int) bool { return all[i].ActualScanBytes > all[j].ActualScanBytes })
	} else {
		sort.Slice(all, func(i, j int) bool { return all[i].ID.String() > all[j].ID.String() })
	}
	return paginateByID(all, f.Cursor, ClampLimit(f.Limit), func(e *domain.Execution) string { return e.ID.String() })
}

func (m *Mem) FindCacheHit(_ context.Context, tenant uuid.UUID, cacheKey string, since time.Time) (*domain.Execution, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var best *domain.Execution
	for _, e := range m.executions {
		if e.TenantID != tenant || e.CacheKey != cacheKey || e.Status != domain.StatusSucceeded ||
			e.CacheHit || e.ResultURI == "" || e.FinishedAt == nil || e.FinishedAt.Before(since) {
			continue
		}
		if best == nil || e.FinishedAt.After(*best.FinishedAt) {
			best = e
		}
	}
	if best == nil {
		return nil, ErrNotFound
	}
	ce := *best
	return &ce, nil
}

func (m *Mem) ActiveExecutions(_ context.Context, tenant uuid.UUID) ([]*domain.Execution, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []*domain.Execution
	for _, e := range m.executions {
		if e.TenantID == tenant && (e.Status == domain.StatusQueued || e.Status == domain.StatusRunning || e.Status == domain.StatusStreamingResults) {
			ce := *e
			out = append(out, &ce)
		}
	}
	return out, nil
}

func (m *Mem) QueryStats(_ context.Context, tenant uuid.UUID, since time.Time, limit int) ([]QueryStat, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	agg := map[string]*QueryStat{}
	for _, e := range m.executions {
		if e.TenantID != tenant || e.CreatedAt.Before(since) {
			continue
		}
		st, ok := agg[e.SQLFingerprint]
		if !ok {
			st = &QueryStat{SQLFingerprint: e.SQLFingerprint}
			agg[e.SQLFingerprint] = st
		}
		st.Executions++
		st.TotalScanBytes += e.ActualScanBytes
		if e.Status == domain.StatusFailed || e.Status == domain.StatusRejected || e.Status == domain.StatusCeilingExceeded {
			st.Failures++
		}
	}
	var out []QueryStat
	for _, st := range agg {
		out = append(out, *st)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].TotalScanBytes > out[j].TotalScanBytes })
	if limit > 0 && len(out) > limit {
		out = out[:limit]
	}
	return out, nil
}

// ---- Limits -----------------------------------------------------------------

func (m *Mem) GetTenantLimits(_ context.Context, tenant uuid.UUID) (*domain.TenantLimits, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	l, ok := m.limits[tenant]
	if !ok {
		return nil, nil
	}
	cl := *l
	return &cl, nil
}

func (m *Mem) PutTenantLimits(_ context.Context, op domain.Op, l *domain.TenantLimits) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	cl := *l
	cl.TenantID = op.Tenant
	m.limits[op.Tenant] = &cl
	return nil
}

// ---- Idempotency ------------------------------------------------------------

func (m *Mem) GetIdempotency(_ context.Context, tenant uuid.UUID, key string) (*IdempotencyRecord, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	rec, ok := m.idempotency[tenant.String()+"|"+key]
	if !ok || m.Now().Sub(rec.at) > 24*time.Hour {
		return nil, nil
	}
	r := rec.rec
	return &r, nil
}

func (m *Mem) PutIdempotency(_ context.Context, tenant uuid.UUID, key, _, _ string, status int, response []byte) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	k := tenant.String() + "|" + key
	if _, ok := m.idempotency[k]; ok {
		return nil // concurrent duplicates keep the first
	}
	m.idempotency[k] = memIdem{rec: IdempotencyRecord{Status: status, Response: append([]byte(nil), response...)}, at: m.Now()}
	return nil
}

// ---- Audit + outbox ---------------------------------------------------------

func (m *Mem) InsertAudit(_ context.Context, env events.Envelope) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.appendOutboxLocked([]events.Envelope{env})
	return nil
}

func (m *Mem) FetchUnpublished(_ context.Context, limit int) ([]events.OutboxRow, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []events.OutboxRow
	for i := range m.outbox {
		if m.outbox[i].published {
			continue
		}
		out = append(out, events.OutboxRow{ID: m.outbox[i].id, Envelope: m.outbox[i].env})
		if len(out) >= limit {
			break
		}
	}
	return out, nil
}

func (m *Mem) MarkPublished(_ context.Context, ids []int64) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	idSet := map[int64]bool{}
	for _, id := range ids {
		idSet[id] = true
	}
	for i := range m.outbox {
		if idSet[m.outbox[i].id] {
			m.outbox[i].published = true
		}
	}
	return nil
}

func (m *Mem) OutboxEventsByType(_ context.Context, tenant uuid.UUID, eventType string) ([]events.Envelope, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []events.Envelope
	for _, row := range m.outbox {
		if row.env.TenantID == tenant && row.env.EventType == eventType {
			out = append(out, row.env)
		}
	}
	return out, nil
}

func (m *Mem) Ping(context.Context) error { return nil }

// paginateByID implements cursor pagination over a pre-sorted slice using a
// string key: the cursor is the last returned key (MASTER-FR-022).
func paginateByID[T any](all []T, cursor string, limit int, key func(T) string) (Page[T], error) {
	start := 0
	if cursor != "" {
		for i, item := range all {
			if key(item) == cursor {
				start = i + 1
				break
			}
		}
	}
	end := start + limit
	if end > len(all) {
		end = len(all)
	}
	p := Page[T]{Data: all[start:end], HasMore: end < len(all)}
	if p.HasMore && len(p.Data) > 0 {
		p.NextCursor = key(p.Data[len(p.Data)-1])
	}
	return p, nil
}
