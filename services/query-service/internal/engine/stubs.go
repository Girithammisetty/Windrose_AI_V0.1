package engine

import (
	"context"

	"github.com/windrose-ai/query-service/internal/domain"
)

// Warehouse is the per-cloud warehouse adapter (Athena/BigQuery/Synapse per
// cell cloud, QRY-FR-040). COMPILING STUB.
//
// TODO(QRY-FR-040): one implementation per cell cloud behind this same
// interface; all three use driver-level bound parameters (Athena
// ExecutionParameters, BigQuery QueryParameter, Synapse sp_executesql) —
// never string splicing (QRY-FR-003) — and map cancellation to
// StopQueryExecution / jobs.cancel / KILL QUERY (QRY-FR-045).
type Warehouse struct {
	Cloud string // aws | azure | gcp
	Up    bool
}

func (w *Warehouse) Name() string                   { return NameWarehouse }
func (w *Warehouse) Healthy(_ context.Context) bool { return w.Up }
func (w *Warehouse) Execute(_ context.Context, _ Query, _ Sink) (Stats, error) {
	return Stats{}, domain.ENotImplemented("warehouse adapter is a compiling stub (TODO QRY-FR-040)")
}
