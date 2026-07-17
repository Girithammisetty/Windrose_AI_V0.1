package exec

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/query-service/internal/datasets"
	"github.com/windrose-ai/query-service/internal/domain"
	"github.com/windrose-ai/query-service/internal/engine"
	"github.com/windrose-ai/query-service/internal/events"
	"github.com/windrose-ai/query-service/internal/results"
	"github.com/windrose-ai/query-service/internal/store"
)

// Broker owns the execution lifecycle (BRD §4.2 state machine).
type Broker struct {
	Store    store.Store
	Resolver datasets.Resolver
	Engines  *engine.Registry
	Results  *results.Store
	Slots    *SlotManager

	// Estimate overrides the default dataset-stats estimator (tests, and
	// later EXPLAIN-backed estimators per engine).
	Estimate EstimateFn
	// ExtraNamespaces adds tenant-allowed namespaces beyond those of the
	// resolved datasets (BR-2).
	ExtraNamespaces func(uuid.UUID) map[string]bool
	// AutoMaterializeSchemas enables the semantic auto-materialization path
	// (QRY-FR-005) for the given (lowercased) schemas: physical tables in these
	// schemas are resolved by name and their source parquet materialized into
	// the engine. Empty (default) disables the path entirely.
	AutoMaterializeSchemas map[string]bool
	// CacheTTL for the result cache (QRY-FR-046), default 15 min.
	CacheTTL time.Duration
	// DisableCache turns the result cache off globally.
	DisableCache bool
	// WatchdogGrace is the service-side backstop past the runtime ceiling
	// (BR-6), default 30s.
	WatchdogGrace time.Duration
	Now           func() time.Time

	mu        sync.Mutex
	runs      map[uuid.UUID]*runHandle
	suspended map[uuid.UUID]bool
	wg        sync.WaitGroup
}

type runHandle struct {
	tenant uuid.UUID
	cancel context.CancelFunc

	mu     sync.Mutex
	reason string
}

func (h *runHandle) setReason(r string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.reason == "" {
		h.reason = r
	}
}

func (h *runHandle) getReason() string {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.reason
}

// Abort/kill reasons.
const (
	reasonCancel         = "cancel"
	reasonSuspended      = "suspended"
	reasonDatasetDeleted = "dataset_deleted"
	reasonRuntimeCeiling = "runtime_ceiling"
	reasonScanCeiling    = "scan_ceiling"
	reasonResultRows     = "result_rows_ceiling"
	reasonResultBytes    = "result_bytes_ceiling"
)

func (b *Broker) now() time.Time {
	if b.Now != nil {
		return b.Now()
	}
	return time.Now()
}

func (b *Broker) estimateFn() EstimateFn {
	if b.Estimate != nil {
		return b.Estimate
	}
	return defaultEstimate
}

func (b *Broker) cacheTTL() time.Duration {
	if b.CacheTTL > 0 {
		return b.CacheTTL
	}
	return 15 * time.Minute
}

func (b *Broker) watchdogGrace() time.Duration {
	if b.WatchdogGrace > 0 {
		return b.WatchdogGrace
	}
	return 30 * time.Second
}

func (b *Broker) registerRun(id uuid.UUID, h *runHandle) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.runs == nil {
		b.runs = map[uuid.UUID]*runHandle{}
	}
	b.runs[id] = h
}

func (b *Broker) unregisterRun(id uuid.UUID) {
	b.mu.Lock()
	defer b.mu.Unlock()
	delete(b.runs, id)
}

func (b *Broker) handleFor(id uuid.UUID) *runHandle {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.runs[id]
}

// IsSuspended reports tenant suspension (identity.events tenant.suspended).
func (b *Broker) IsSuspended(tenant uuid.UUID) bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.suspended[tenant]
}

// Wait blocks until all in-flight executions finish (shutdown, tests).
func (b *Broker) Wait() { b.wg.Wait() }

// RunRequest is a run/dry-run submission.
type RunRequest struct {
	PlanRequest
	WorkspaceID  uuid.UUID
	SavedQueryID *uuid.UUID
	VersionNo    *int
	Mode         string // sync | async ("" = async)
	UseCache     bool
}

// capsFor derives the tenant's concurrency caps (QRY-FR-044).
func capsFor(limits *domain.TenantLimits) Caps {
	c := Caps{Slots: domain.DefaultTenantSlots, AgentSlots: domain.DefaultAgentSubSlots, QueueDepth: domain.MaxQueueDepth}
	if limits != nil && limits.ConcurrentSlots != nil {
		c.Slots = *limits.ConcurrentSlots
	}
	return c
}

// newExecution builds the history row from a request and plan (QRY-FR-080).
func (b *Broker) newExecution(req RunRequest, plan *Plan, status string) *domain.Execution {
	e := &domain.Execution{
		ID:             domain.NewID(),
		TenantID:       req.Op.Tenant,
		WorkspaceID:    req.WorkspaceID,
		SavedQueryID:   req.SavedQueryID,
		QueryVersionNo: req.VersionNo,
		SQLText:        req.SQLText,
		CallerClass:    req.Op.Caller,
		Status:         status,
		CreatedBy:      req.Op.UserID,
		TraceID:        req.Op.TraceID,
		CreatedAt:      b.now().UTC(),
	}
	if req.Op.ViaAgent != nil {
		e.ViaAgent = map[string]any{"agent_id": req.Op.ViaAgent.AgentID, "version": req.Op.ViaAgent.Version}
	}
	if plan != nil {
		e.SQLFingerprint = plan.Classification.Fingerprint
		e.BoundParams = plan.RedactedParams
		e.Engine = plan.Route.Engine
		r := plan.Route.Reason
		e.RoutingReason = &r
		e.EstimatedScanBytes = plan.Estimate.ScanBytes
		c := plan.Ceilings
		e.Ceilings = &c
		e.Warnings = append([]string(nil), plan.Warnings...)
		e.DatasetURNs = plan.DatasetURNs
		e.CacheKey = plan.CacheKey
	}
	return e
}

func execErrorOf(err error) *domain.ExecError {
	if de, ok := domain.AsError(err); ok {
		return &domain.ExecError{Code: de.Code, Message: de.Message, Details: de.Details}
	}
	return &domain.ExecError{Code: domain.CodeInternal, Message: err.Error()}
}

// recordRejected persists a planning failure in history (QRY-FR-080:
// failures recorded too) and emits execution.failed.
func (b *Broker) recordRejected(ctx context.Context, req RunRequest, planErr error) {
	e := b.newExecution(req, nil, domain.StatusRejected)
	e.Error = execErrorOf(planErr)
	env := events.NewEnvelope(events.EvExecutionFailed, req.Op, events.ExecutionURN(req.Op.Tenant, e.ID),
		map[string]any{"error_code": e.Error.Code})
	if err := b.Store.InsertExecution(ctx, req.Op, e, []events.Envelope{env}); err != nil {
		slog.Warn("record rejected execution failed", "err", err)
	}
}

// DryRun plans without executing (QRY-FR-041) and records the dry-run in
// history (QRY-FR-080). A ceiling breach returns 422 with the estimate.
func (b *Broker) DryRun(ctx context.Context, req RunRequest) (*Plan, error) {
	req.UseCache = false
	plan, err := b.buildPlan(ctx, req.PlanRequest)
	if err != nil {
		b.recordRejected(ctx, req, err)
		return nil, err
	}
	if plan.CeilingVerdict == "exceeded" {
		cerr := domain.ECostCeiling("estimated cost exceeds ceiling", plan.CeilingDetail)
		e := b.newExecution(req, plan, domain.StatusRejected)
		e.Error = execErrorOf(cerr)
		e.Warnings = append(e.Warnings, WarnDryRun)
		env := events.NewEnvelope(events.EvExecutionFailed, req.Op, events.ExecutionURN(req.Op.Tenant, e.ID),
			map[string]any{"error_code": domain.CodeCostCeilingExceeded})
		if err := b.Store.InsertExecution(ctx, req.Op, e, []events.Envelope{env}); err != nil {
			slog.Warn("record dry-run failed", "err", err)
		}
		return plan, cerr
	}
	e := b.newExecution(req, plan, domain.StatusSucceeded)
	e.Warnings = append(e.Warnings, WarnDryRun)
	if err := b.Store.InsertExecution(ctx, req.Op, e, nil); err != nil {
		slog.Warn("record dry-run failed", "err", err)
	}
	return plan, nil
}

// Run brokers one execution (QRY-FR-043): sync only when the plan is small
// and a slot is instantly free (BR-5), async otherwise (202 + status).
func (b *Broker) Run(ctx context.Context, req RunRequest) (*domain.Execution, error) {
	if b.IsSuspended(req.Op.Tenant) {
		return nil, domain.EPermissionDenied("tenant is suspended; new executions are blocked")
	}
	plan, err := b.buildPlan(ctx, req.PlanRequest)
	if err != nil {
		b.recordRejected(ctx, req, err)
		return nil, err
	}
	if plan.CeilingVerdict == "exceeded" {
		cerr := domain.ECostCeiling("estimated cost exceeds ceiling", plan.CeilingDetail)
		b.recordRejected(ctx, req, cerr)
		metricCeilingRejections.WithLabelValues("max_scan_bytes").Inc()
		return nil, cerr
	}

	// Result cache (QRY-FR-046): key pins dataset versions, TTL 15 min.
	if req.UseCache && !b.DisableCache {
		if hit, err := b.Store.FindCacheHit(ctx, req.Op.Tenant, plan.CacheKey, b.now().Add(-b.cacheTTL())); err == nil && hit != nil {
			e := b.newExecution(req, plan, domain.StatusSucceeded)
			e.CacheHit = true
			e.Engine = hit.Engine
			e.RoutingReason = &domain.RoutingReason{Rule: "cache", Detail: "served from result cache"}
			e.ResultURI = hit.ResultURI
			e.ResultRows = hit.ResultRows
			e.ResultBytes = hit.ResultBytes
			e.ActualScanBytes = 0 // no engine contact
			now := b.now().UTC()
			e.StartedAt = &now
			e.FinishedAt = &now
			env := events.NewEnvelope(events.EvExecutionSucceeded, req.Op, events.ExecutionURN(req.Op.Tenant, e.ID),
				map[string]any{"actual_scan_bytes": int64(0), "result_rows": e.ResultRows, "duration_ms": int64(0), "cache_hit": true})
			if err := b.Store.InsertExecution(ctx, req.Op, e, []events.Envelope{env}); err != nil {
				return nil, err
			}
			metricCacheHits.Inc()
			return e, nil
		}
	}

	limits, err := b.Store.GetTenantLimits(ctx, req.Op.Tenant)
	if err != nil {
		return nil, err
	}
	caps := capsFor(limits)
	agent := req.Op.Caller == domain.CallerAgent
	execID := domain.NewID()

	if req.Mode == "sync" {
		// Sync admission (QRY-FR-043): plan must say small.
		if plan.Estimate.ScanBytes > domain.SyncMaxEstimatedScanBytes {
			return nil, domain.EUseAsync("plan too large for sync mode; submit async")
		}
		grant, err := b.Slots.Acquire(req.Op.Tenant, execID, req.Op.UserID, agent, caps, false)
		if err != nil {
			return nil, domain.EUseAsync("no slot instantly available; submit async") // BR-5
		}
		e := b.newExecution(req, plan, domain.StatusRunning)
		e.ID = execID
		now := b.now().UTC()
		e.StartedAt = &now
		env := b.startedEnvelope(req.Op, e)
		if err := b.Store.InsertExecution(ctx, req.Op, e, []events.Envelope{env}); err != nil {
			b.Slots.Release(req.Op.Tenant, execID)
			return nil, err
		}
		b.wg.Add(1)
		b.runExecution(ctx, req.Op, e, plan)
		_ = grant
		return b.Store.GetExecution(ctx, req.Op.Tenant, execID)
	}

	// Async admission (QRY-FR-044).
	grant, err := b.Slots.Acquire(req.Op.Tenant, execID, req.Op.UserID, agent, caps, true)
	if err != nil {
		if errors.Is(err, ErrQueueFull) {
			// Queue overflow → 429, no row transition (BRD §4.2 guard).
			return nil, domain.ERateLimited("tenant execution queue is full; retry later")
		}
		return nil, err
	}
	status := domain.StatusRunning
	var queuePos *int
	var envs []events.Envelope
	if grant.Pos > 0 {
		status = domain.StatusQueued
		p := grant.Pos
		queuePos = &p
	}
	e := b.newExecution(req, plan, status)
	e.ID = execID
	e.QueuePosition = queuePos
	if status == domain.StatusRunning {
		now := b.now().UTC()
		e.StartedAt = &now
		envs = append(envs, b.startedEnvelope(req.Op, e))
	} else {
		metricQueueDepth.WithLabelValues(req.Op.Tenant.String()).Set(float64(len(b.Slots.QueuedIDs(req.Op.Tenant))))
	}
	if err := b.Store.InsertExecution(ctx, req.Op, e, envs); err != nil {
		b.Slots.Release(req.Op.Tenant, execID)
		return nil, err
	}
	b.wg.Add(1)
	go b.waitAndRun(req.Op, e, plan, grant)
	return e, nil
}

func (b *Broker) startedEnvelope(op domain.Op, e *domain.Execution) events.Envelope {
	payload := map[string]any{"engine": e.Engine, "caller_class": string(e.CallerClass)}
	if e.SavedQueryID != nil {
		payload["saved_query_id"] = e.SavedQueryID.String()
	}
	return events.NewEnvelope(events.EvExecutionStarted, op, events.ExecutionURN(op.Tenant, e.ID), payload)
}

// waitAndRun waits for a queued slot then runs (async path).
func (b *Broker) waitAndRun(op domain.Op, e *domain.Execution, plan *Plan, grant *Grant) {
	if grant.Pos > 0 {
		select {
		case <-grant.Ready:
			ctx := context.Background()
			err := b.Store.UpdateExecution(ctx, op.Tenant, e.ID, func(row *domain.Execution) ([]events.Envelope, error) {
				if !domain.CanTransition(row.Status, domain.StatusRunning) {
					return nil, domain.EConflict("illegal transition " + row.Status + "→running")
				}
				row.Status = domain.StatusRunning
				row.QueuePosition = nil
				now := b.now().UTC()
				row.StartedAt = &now
				return []events.Envelope{b.startedEnvelope(op, row)}, nil
			})
			if err != nil {
				// Row moved to a terminal state (e.g. cancelled) while the
				// slot was granted: free the slot and stop.
				b.Slots.Release(op.Tenant, e.ID)
				b.wg.Done()
				return
			}
		case <-grant.Aborted:
			reason := grant.AbortReason()
			ctx := context.Background()
			to := domain.StatusCancelled
			var execErr *domain.ExecError
			evType := events.EvExecutionCancelled
			payload := map[string]any{}
			if reason == reasonDatasetDeleted {
				to = domain.StatusFailed
				execErr = &domain.ExecError{Code: domain.CodeDatasetNotFound, Message: "a referenced dataset was deleted while queued"}
				evType = events.EvExecutionFailed
				payload["error_code"] = domain.CodeDatasetNotFound
			}
			_ = b.Store.UpdateExecution(ctx, op.Tenant, e.ID, func(row *domain.Execution) ([]events.Envelope, error) {
				if !domain.CanTransition(row.Status, to) {
					return nil, domain.EConflict("illegal transition")
				}
				row.Status = to
				row.QueuePosition = nil
				row.Error = execErr
				now := b.now().UTC()
				row.FinishedAt = &now
				return []events.Envelope{events.NewEnvelope(evType, op, events.ExecutionURN(op.Tenant, row.ID), payload)}, nil
			})
			b.wg.Done()
			return
		}
	}
	b.runExecution(context.Background(), op, e, plan)
}

// capSink enforces result-size ceilings while streaming into the store
// (QRY-FR-042/060) and drives the running→streaming_results transition.
type capSink struct {
	w          *results.Writer
	ceilings   domain.Ceilings
	onFirstRow func()
	onBreach   func(kind string)
	started    bool
}

func (s *capSink) Start(cols []engine.Column) error { return s.w.Start(cols) }

func (s *capSink) Row(vals []any) error {
	if err := s.w.Row(vals); err != nil {
		return err
	}
	if !s.started {
		s.started = true
		if s.onFirstRow != nil {
			s.onFirstRow()
		}
	}
	if s.w.Rows() > s.ceilings.MaxResultRows {
		s.onBreach(reasonResultRows)
		return fmt.Errorf("max_result_rows ceiling exceeded")
	}
	if s.w.Bytes() > s.ceilings.MaxResultBytes {
		s.onBreach(reasonResultBytes)
		return fmt.Errorf("max_result_bytes ceiling exceeded")
	}
	return nil
}

// runExecution drives one admitted execution to a terminal state. It always
// releases the slot and calls wg.Done.
func (b *Broker) runExecution(parent context.Context, op domain.Op, e *domain.Execution, plan *Plan) {
	defer b.wg.Done()
	defer b.Slots.Release(op.Tenant, e.ID)

	ctx, cancel := context.WithCancel(context.WithoutCancel(parent))
	defer cancel()
	h := &runHandle{tenant: op.Tenant, cancel: cancel}
	b.registerRun(e.ID, h)
	defer b.unregisterRun(e.ID)
	// Close the register/suspend race: SuspendTenant either saw this handle
	// or set the flag before we registered — check it now.
	if b.IsSuspended(op.Tenant) {
		h.setReason(reasonSuspended)
		cancel()
	}

	dbCtx := context.Background()

	eng, ok := b.Engines.Get(plan.Route.Engine)
	if !ok {
		b.finish(dbCtx, op, e.ID, domain.StatusFailed, engine.Stats{}, 0, nil,
			&domain.ExecError{Code: domain.CodeEngineUnavailable, Message: "engine " + plan.Route.Engine + " not registered"}, plan)
		return
	}

	// Runtime ceiling + service-side watchdog backstop (BR-6, AC-8).
	maxRuntime := plan.Ceilings.MaxRuntime()
	ceilTimer := time.AfterFunc(maxRuntime, func() {
		h.setReason(reasonRuntimeCeiling)
		cancel()
	})
	defer ceilTimer.Stop()
	watchdog := time.AfterFunc(maxRuntime+b.watchdogGrace(), func() {
		h.setReason(reasonRuntimeCeiling)
		cancel()
	})
	defer watchdog.Stop()

	writer, werr := b.Results.NewWriter(op.Tenant, e.ID)
	if werr != nil {
		b.finish(dbCtx, op, e.ID, domain.StatusFailed, engine.Stats{}, 0, nil,
			&domain.ExecError{Code: domain.CodeInternal, Message: "result store: " + werr.Error()}, plan)
		return
	}
	sink := &capSink{
		w:        writer,
		ceilings: plan.Ceilings,
		onFirstRow: func() {
			_ = b.Store.UpdateExecution(dbCtx, op.Tenant, e.ID, func(row *domain.Execution) ([]events.Envelope, error) {
				if domain.CanTransition(row.Status, domain.StatusStreamingResults) {
					row.Status = domain.StatusStreamingResults
				}
				return nil, nil
			})
		},
		onBreach: func(kind string) { h.setReason(kind) },
	}

	started := b.now()
	stats, execErr := eng.Execute(ctx, engine.Query{ExecutionID: e.ID, SQL: plan.ExecSQL, Args: plan.Rewritten.Args, Tables: plan.Materializations}, sink)
	durMS := b.now().Sub(started).Milliseconds()

	// BR-8: estimated-vs-actual drift — kill verdict even post-hoc.
	if execErr == nil && stats.ScanBytes > plan.Ceilings.MaxScanBytes {
		h.setReason(reasonScanCeiling)
		execErr = fmt.Errorf("actual scan bytes exceeded ceiling")
	}

	reason := h.getReason()
	switch {
	case execErr == nil:
		if err := writer.Seal(); err != nil {
			b.finish(dbCtx, op, e.ID, domain.StatusFailed, stats, durMS, writer,
				&domain.ExecError{Code: domain.CodeInternal, Message: "seal results: " + err.Error()}, plan)
			return
		}
		b.finish(dbCtx, op, e.ID, domain.StatusSucceeded, stats, durMS, writer, nil, plan)
	case reason == reasonCancel || reason == reasonSuspended:
		writer.Abort()
		b.finish(dbCtx, op, e.ID, domain.StatusCancelled, stats, durMS, nil, nil, plan)
	case reason != "":
		writer.Abort()
		b.finish(dbCtx, op, e.ID, domain.StatusCeilingExceeded, stats, durMS, nil,
			&domain.ExecError{Code: domain.CodeCostCeilingExceeded, Message: "ceiling breached at runtime: " + reason, Details: map[string]any{"ceiling": reason}}, plan)
	default:
		writer.Abort()
		b.finish(dbCtx, op, e.ID, domain.StatusFailed, stats, durMS, nil, execErrorOf(execErr), plan)
	}
}

// finish records the terminal state and emits the matching event
// (MASTER-FR-034: same transaction as the row update).
func (b *Broker) finish(ctx context.Context, op domain.Op, id uuid.UUID, to string, stats engine.Stats, durMS int64, writer *results.Writer, execErr *domain.ExecError, plan *Plan) {
	err := b.Store.UpdateExecution(ctx, op.Tenant, id, func(row *domain.Execution) ([]events.Envelope, error) {
		if !domain.CanTransition(row.Status, to) {
			return nil, domain.EConflict("illegal transition " + row.Status + "→" + to)
		}
		row.Status = to
		row.ActualScanBytes = stats.ScanBytes
		row.DurationMS = durMS
		row.Error = execErr
		now := b.now().UTC()
		row.FinishedAt = &now
		urn := events.ExecutionURN(op.Tenant, id)
		var env events.Envelope
		switch to {
		case domain.StatusSucceeded:
			row.ResultRows = writer.Rows()
			row.ResultBytes = writer.Bytes()
			row.ResultURI = b.Results.URI(op.Tenant, id)
			env = events.NewEnvelope(events.EvExecutionSucceeded, op, urn, map[string]any{
				"actual_scan_bytes": stats.ScanBytes, "result_rows": row.ResultRows,
				"duration_ms": durMS, "cache_hit": false,
			})
		case domain.StatusCancelled:
			// Partial-cost accounting (US-8, AC-11).
			env = events.NewEnvelope(events.EvExecutionCancelled, op, urn, map[string]any{
				"actual_scan_bytes": stats.ScanBytes, "duration_ms": durMS,
			})
		case domain.StatusCeilingExceeded:
			ceiling := ""
			if execErr != nil {
				if d, ok := execErr.Details.(map[string]any); ok {
					ceiling, _ = d["ceiling"].(string)
				}
			}
			env = events.NewEnvelope(events.EvExecutionCeilingExceeded, op, urn, map[string]any{
				"ceiling": ceiling, "estimate": plan.Estimate.ScanBytes, "actual": stats.ScanBytes,
			})
		default:
			code := domain.CodeInternal
			if execErr != nil {
				code = execErr.Code
			}
			env = events.NewEnvelope(events.EvExecutionFailed, op, urn, map[string]any{"error_code": code})
		}
		return []events.Envelope{env}, nil
	})
	if err != nil {
		slog.Error("finish execution failed", "execution", id, "to", to, "err", err)
		return
	}
	if plan != nil {
		observeTerminal(plan.Route.Engine, to, string(op.Caller), stats.ScanBytes)
	}
	if to == domain.StatusCeilingExceeded && execErr != nil {
		if d, ok := execErr.Details.(map[string]any); ok {
			if c, _ := d["ceiling"].(string); c != "" {
				metricCeilingRejections.WithLabelValues(c).Inc()
			}
		}
	}
	metricQueueDepth.WithLabelValues(op.Tenant.String()).Set(float64(len(b.Slots.QueuedIDs(op.Tenant))))
}

// Cancel implements POST /executions/{id}/cancel (QRY-FR-045).
func (b *Broker) Cancel(ctx context.Context, op domain.Op, id uuid.UUID) (*domain.Execution, error) {
	e, err := b.Store.GetExecution(ctx, op.Tenant, id)
	if err != nil {
		return nil, err
	}
	if domain.IsTerminalStatus(e.Status) {
		return nil, domain.EConflict("execution is already terminal (" + e.Status + ")")
	}
	// Queued: remove from the queue and mark cancelled here — the waiting
	// goroutine's Aborted branch may also fire; both paths guard via the
	// state machine so exactly one wins.
	if b.Slots.Abort(op.Tenant, id, reasonCancel) {
		_ = b.Store.UpdateExecution(ctx, op.Tenant, id, func(row *domain.Execution) ([]events.Envelope, error) {
			if !domain.CanTransition(row.Status, domain.StatusCancelled) {
				return nil, domain.EConflict("illegal transition")
			}
			row.Status = domain.StatusCancelled
			row.QueuePosition = nil
			now := b.now().UTC()
			row.FinishedAt = &now
			return []events.Envelope{events.NewEnvelope(events.EvExecutionCancelled, op, events.ExecutionURN(op.Tenant, id), map[string]any{})}, nil
		})
		return b.Store.GetExecution(ctx, op.Tenant, id)
	}
	// Running: propagate the engine kill (≤5s per BR-6/AC-11) and wait for
	// the run loop to record the terminal state.
	deadline := b.now().Add(5 * time.Second)
	for {
		if h := b.handleFor(id); h != nil {
			h.setReason(reasonCancel)
			h.cancel()
		}
		cur, err := b.Store.GetExecution(ctx, op.Tenant, id)
		if err != nil {
			return nil, err
		}
		if domain.IsTerminalStatus(cur.Status) {
			return cur, nil
		}
		if b.now().After(deadline) {
			return cur, nil // kill signalled; status converges via run loop
		}
		time.Sleep(25 * time.Millisecond)
	}
}

// QueuePosition refreshes the live queue position for status reads.
func (b *Broker) QueuePosition(tenant, id uuid.UUID) int {
	return b.Slots.Position(tenant, id)
}

// SuspendTenant reacts to identity.events tenant.suspended (§6): cancel
// queued + running executions and block new ones.
func (b *Broker) SuspendTenant(ctx context.Context, tenant uuid.UUID) {
	b.mu.Lock()
	if b.suspended == nil {
		b.suspended = map[uuid.UUID]bool{}
	}
	b.suspended[tenant] = true
	var toCancel []*runHandle
	for _, h := range b.runs {
		if h.tenant == tenant {
			toCancel = append(toCancel, h)
		}
	}
	b.mu.Unlock()
	b.Slots.AbortAllQueued(tenant, reasonSuspended)
	for _, h := range toCancel {
		h.setReason(reasonSuspended)
		h.cancel()
	}
	_ = ctx
}

// ResumeTenant lifts a suspension (tenant.resumed).
func (b *Broker) ResumeTenant(tenant uuid.UUID) {
	b.mu.Lock()
	defer b.mu.Unlock()
	delete(b.suspended, tenant)
}

// HandleDatasetDeleted reacts to dataset.events dataset.deleted (§6): fail
// queued executions referencing the URN with DATASET_NOT_FOUND.
func (b *Broker) HandleDatasetDeleted(ctx context.Context, tenant uuid.UUID, urn string) {
	for _, id := range b.Slots.QueuedIDs(tenant) {
		e, err := b.Store.GetExecution(ctx, tenant, id)
		if err != nil {
			continue
		}
		for _, u := range e.DatasetURNs {
			if strings.EqualFold(u, urn) {
				b.Slots.Abort(tenant, id, reasonDatasetDeleted)
				break
			}
		}
	}
}
