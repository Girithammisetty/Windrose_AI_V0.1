//go:build integration

package integration

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/datacern-ai/audit-service/internal/domain"
	"github.com/datacern-ai/audit-service/internal/export"
)

// TestSealReconcile_RecoversDayMissingFromChainHeads reproduces the exact
// BRD 58 SEC-2 incident shape: real events land durably in ClickHouse for a
// (tenant, day), but chain.Manager.Append's Postgres checkpoint write never
// happened for it (simulated the natural way -- by never calling
// UpsertChainHead, since that write is best-effort and this is precisely
// what "best-effort failed" looks like afterward). Before the reconcile
// fix, this day is invisible to PG.ListUnsealedDays forever, so it is NEVER
// exported -- confirmed directly below. The reconciler must find it via
// ClickHouse, recreate its checkpoint, and actually seal it.
func TestSealReconcile_RecoversDayMissingFromChainHeads(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()

	tenant := uuid.New()
	pastDate := time.Now().UTC().AddDate(0, 0, -3).Format("2006-01-02")
	occurredAt, err := time.Parse("2006-01-02", pastDate)
	if err != nil {
		t.Fatalf("parse pastDate: %v", err)
	}
	occurredAt = occurredAt.Add(12 * time.Hour)

	eventID := uuid.New()
	payloadDigest := domain.SHA256Hex([]byte("seal-reconcile-payload"))
	hash := domain.ChainHash(domain.GenesisHash(tenant, pastDate), eventID, payloadDigest, occurredAt)

	rec := domain.Record{
		EventID: eventID, EventType: "case.assigned", SourceTopic: "case.events.v1",
		TenantID: tenant, ActorType: "user", ActorID: "u-1",
		ResourceURN: "wr:" + tenant.String() + ":case:case/1", Action: "assign",
		OccurredAt: occurredAt, IngestedAt: occurredAt,
		PayloadDigest: payloadDigest, PayloadJSON: `{"ok":true}`,
		ChainDate: pastDate, ChainSeq: 1, ChainHash: hash,
	}
	if err := h.ch.InsertBatch(ctx, []domain.Record{rec}); err != nil {
		t.Fatalf("seed clickhouse row: %v", err)
	}

	// Precondition: NO chain_heads checkpoint exists for this (tenant, date) at
	// all -- this is the exact "best-effort write never happened" state.
	if ch, err := h.pg.GetChainHead(ctx, tenant, pastDate); err != nil {
		t.Fatalf("get chain head: %v", err)
	} else if ch != nil {
		t.Fatalf("expected no chain_heads row to exist yet, got %+v", ch)
	}

	// Reproduces the bug directly: with no chain_heads row, this day is
	// invisible to the ORIGINAL (pre-SEC-2) scheduler input.
	today := time.Now().UTC().Format("2006-01-02")
	unsealed, err := h.pg.ListUnsealedDays(ctx, today)
	if err != nil {
		t.Fatalf("list unsealed days: %v", err)
	}
	for _, d := range unsealed {
		if d.TenantID == tenant && d.ChainDate == pastDate {
			t.Fatalf("this day should NOT appear in ListUnsealedDays before reconciliation -- that's the bug")
		}
	}

	// Run the reconciler.
	sched := &export.Scheduler{Exporter: h.exporter, PG: h.pg}
	sched.ReconcileAndExport(ctx)

	// The checkpoint must now exist (self-healed from ClickHouse's tip) AND
	// be sealed (the export actually ran).
	ch, err := h.pg.GetChainHead(ctx, tenant, pastDate)
	if err != nil {
		t.Fatalf("get chain head after reconcile: %v", err)
	}
	if ch == nil {
		t.Fatal("expected the reconciler to have recreated the chain_heads checkpoint")
	}
	if ch.SealedAt == nil {
		t.Fatal("expected the reconciler to have sealed the day (manifest exported)")
	}
	if ch.HeadHash != hash {
		t.Fatalf("recovered head hash mismatch: got %s want %s", ch.HeadHash, hash)
	}

	// And a real manifest actually landed.
	man, err := h.pg.LatestManifest(ctx, tenant, pastDate)
	if err != nil {
		t.Fatalf("latest manifest: %v", err)
	}
	if man == nil || man.RowCount != 1 {
		t.Fatalf("expected a sealed manifest with 1 row, got %+v", man)
	}
}

// TestSealReconcile_IdempotentOnAlreadySealedDay confirms a second reconcile
// pass over an already-sealed day is a safe no-op (no duplicate manifest
// revision, no re-export).
func TestSealReconcile_IdempotentOnAlreadySealedDay(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()

	tenant := uuid.New()
	pastDate := time.Now().UTC().AddDate(0, 0, -2).Format("2006-01-02")
	occurredAt, _ := time.Parse("2006-01-02", pastDate)
	occurredAt = occurredAt.Add(6 * time.Hour)
	eventID := uuid.New()
	payloadDigest := domain.SHA256Hex([]byte("idempotent-payload"))
	hash := domain.ChainHash(domain.GenesisHash(tenant, pastDate), eventID, payloadDigest, occurredAt)

	rec := domain.Record{
		EventID: eventID, EventType: "case.assigned", TenantID: tenant,
		ActorType: "user", ActorID: "u-2",
		ResourceURN: "wr:" + tenant.String() + ":case:case/2", Action: "assign",
		OccurredAt: occurredAt, IngestedAt: occurredAt,
		PayloadDigest: payloadDigest, PayloadJSON: `{"ok":true}`,
		ChainDate: pastDate, ChainSeq: 1, ChainHash: hash,
	}
	if err := h.ch.InsertBatch(ctx, []domain.Record{rec}); err != nil {
		t.Fatalf("seed clickhouse row: %v", err)
	}

	sched := &export.Scheduler{Exporter: h.exporter, PG: h.pg}
	sched.ReconcileAndExport(ctx) // first pass: discovers + seals
	sched.ReconcileAndExport(ctx) // second pass: must be a no-op

	man, err := h.pg.LatestManifest(ctx, tenant, pastDate)
	if err != nil {
		t.Fatalf("latest manifest: %v", err)
	}
	if man == nil {
		t.Fatal("expected a sealed manifest after the first pass")
	}
	if man.Revision != 1 {
		t.Fatalf("expected exactly one manifest revision after two reconcile passes, got revision %d", man.Revision)
	}
}
