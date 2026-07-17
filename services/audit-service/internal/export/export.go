// Package export builds daily WORM batches (AUD-FR-020..023): per tenant per
// UTC day it writes the day's audit rows as a zstd Parquet object plus a
// manifest.json under S3 Object-Lock, chaining manifests day-to-day and
// embedding the day's hash-chain head. A day is sealed only when the manifest
// lands. Re-runs after late events write a new revision (supplement) — sealed
// objects are never overwritten.
package export

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/parquet-go/parquet-go"

	"github.com/windrose-ai/audit-service/internal/chstore"
	"github.com/windrose-ai/audit-service/internal/domain"
	"github.com/windrose-ai/audit-service/internal/meta"
	"github.com/windrose-ai/audit-service/internal/pgstore"
	"github.com/windrose-ai/audit-service/internal/worm"
)

// Version is the exporter version stamped into manifests (AUD-FR-021).
const Version = "1.0.0"

// Exporter runs day exports.
type Exporter struct {
	CH   *chstore.Store
	PG   *pgstore.Store
	WORM *worm.Client
	Meta *meta.Emitter
}

// ManifestFile describes one exported object (AUD-FR-021).
type ManifestFile struct {
	Name           string `json:"name"`
	SHA256         string `json:"sha256"`
	Rows           int    `json:"rows"`
	OccurredAtMin  string `json:"occurred_at_min"`
	OccurredAtMax  string `json:"occurred_at_max"`
}

// Manifest is the sealed manifest document (AUD-FR-021).
type Manifest struct {
	TenantID           string         `json:"tenant_id"`
	Date               string         `json:"date"`
	Revision           int            `json:"revision"`
	Files              []ManifestFile `json:"files"`
	ChainHead          string         `json:"chain_head"`
	ChainSeqRange      [2]uint64      `json:"chain_seq_range"`
	PrevManifestSHA256 string         `json:"prev_manifest_sha256"`
	ExporterVersion    string         `json:"exporter_version"`
	SealedAt           string         `json:"sealed_at"`
}

// parquetRow is the Parquet schema for exported events (zstd per column).
type parquetRow struct {
	EventID         string `parquet:"event_id,zstd"`
	EventType       string `parquet:"event_type,zstd"`
	SourceTopic     string `parquet:"source_topic,zstd"`
	TenantID        string `parquet:"tenant_id,zstd"`
	ActorType       string `parquet:"actor_type,zstd"`
	ActorID         string `parquet:"actor_id,zstd"`
	ViaAgentID      string `parquet:"via_agent_id,zstd"`
	ViaAgentVersion string `parquet:"via_agent_version,zstd"`
	OboUserID       string `parquet:"obo_user_id,zstd"`
	ResourceURN     string `parquet:"resource_urn,zstd"`
	Action          string `parquet:"action,zstd"`
	OccurredAt      string `parquet:"occurred_at,zstd"`
	IngestedAt      string `parquet:"ingested_at,zstd"`
	TraceID         string `parquet:"trace_id,zstd"`
	PayloadDigest   string `parquet:"payload_digest,zstd"`
	PayloadJSON     string `parquet:"payload_json,zstd"`
	PayloadRef      string `parquet:"payload_ref,zstd"`
	BodyWithheld    bool   `parquet:"body_withheld"`
	ChainSeq        int64  `parquet:"chain_seq"`
	ChainHash       string `parquet:"chain_hash,zstd"`
}

// ExportDay exports one (tenant, chain_date). Returns nil when there is nothing
// to export. Idempotent: re-running after late events writes a new revision.
func (e *Exporter) ExportDay(ctx context.Context, tenant uuid.UUID, date string) (*pgstore.Manifest, error) {
	rows, err := e.CH.ChainScan(ctx, tenant, date)
	if err != nil {
		return nil, fmt.Errorf("chain scan: %w", err)
	}
	if len(rows) == 0 {
		return nil, nil
	}

	// Revision: next after the latest sealed manifest for the day (supplement).
	revision := 1
	if latest, err := e.PG.LatestManifest(ctx, tenant, date); err != nil {
		return nil, err
	} else if latest != nil {
		revision = latest.Revision + 1
	}

	pq, err := buildParquet(rows)
	if err != nil {
		return nil, fmt.Errorf("build parquet: %w", err)
	}
	fileName := fmt.Sprintf("events-%04d.parquet", revision)
	objKey := fmt.Sprintf("tenant=%s/date=%s/%s", tenant, date, fileName)
	fileSHA := domain.SHA256Hex(pq)
	if _, err := e.WORM.PutWORM(ctx, objKey, pq, "application/vnd.apache.parquet"); err != nil {
		return nil, err
	}

	// Prev manifest for the day-to-day chain (AUD-FR-021/052): previous day's
	// latest sealed manifest, else this day's prior revision.
	prevSHA := ""
	if prevDay := priorDate(date); prevDay != "" {
		if pm, err := e.PG.LatestManifest(ctx, tenant, prevDay); err == nil && pm != nil {
			prevSHA = pm.ManifestSHA256
		}
	}
	if prevSHA == "" && revision > 1 {
		if pm, err := e.PG.LatestManifest(ctx, tenant, date); err == nil && pm != nil {
			prevSHA = pm.ManifestSHA256
		}
	}

	head := rows[len(rows)-1].ChainHash
	if ch, err := e.PG.GetChainHead(ctx, tenant, date); err == nil && ch != nil {
		head = ch.HeadHash
	}
	minTS, maxTS := rows[0].OccurredAt, rows[0].OccurredAt
	for _, r := range rows {
		if r.OccurredAt.Before(minTS) {
			minTS = r.OccurredAt
		}
		if r.OccurredAt.After(maxTS) {
			maxTS = r.OccurredAt
		}
	}
	man := Manifest{
		TenantID: tenant.String(),
		Date:     date,
		Revision: revision,
		Files: []ManifestFile{{
			Name: fileName, SHA256: fileSHA, Rows: len(rows),
			OccurredAtMin: minTS.UTC().Format(time.RFC3339Nano),
			OccurredAtMax: maxTS.UTC().Format(time.RFC3339Nano),
		}},
		ChainHead:          head,
		ChainSeqRange:      [2]uint64{rows[0].ChainSeq, rows[len(rows)-1].ChainSeq},
		PrevManifestSHA256: prevSHA,
		ExporterVersion:    Version,
		SealedAt:           time.Now().UTC().Format(time.RFC3339Nano),
	}
	manBytes, err := json.MarshalIndent(man, "", " ")
	if err != nil {
		return nil, err
	}
	manSHA := domain.SHA256Hex(manBytes)
	manKey := fmt.Sprintf("tenant=%s/date=%s/manifest-r%04d.json", tenant, date, revision)
	// Manifest is written last; the day is "sealed" only when it lands.
	if _, err := e.WORM.PutWORM(ctx, manKey, manBytes, "application/json"); err != nil {
		return nil, err
	}

	rec := pgstore.Manifest{
		ID: uuid.New(), TenantID: tenant, ChainDate: date, Revision: revision,
		URI: e.WORM.URI(manKey), ManifestSHA256: manSHA, ChainHead: head,
		PrevManifestSHA: prevSHA, RowCount: uint64(len(rows)), Status: "sealed",
	}
	if err := e.PG.InsertManifest(ctx, rec); err != nil {
		return nil, err
	}
	if err := e.PG.SealChainHead(ctx, tenant, date); err != nil {
		return nil, err
	}
	if e.Meta != nil {
		e.Meta.ExportSealed(ctx, tenant, date, manSHA)
	}
	return &rec, nil
}

func buildParquet(rows []domain.Record) ([]byte, error) {
	prs := make([]parquetRow, 0, len(rows))
	for _, r := range rows {
		prs = append(prs, parquetRow{
			EventID: r.EventID.String(), EventType: r.EventType, SourceTopic: r.SourceTopic,
			TenantID: r.TenantID.String(), ActorType: r.ActorType, ActorID: r.ActorID,
			ViaAgentID: r.ViaAgentID, ViaAgentVersion: r.ViaAgentVersion, OboUserID: r.OboUserID,
			ResourceURN: r.ResourceURN, Action: r.Action,
			OccurredAt: r.OccurredAt.UTC().Format(time.RFC3339Nano),
			IngestedAt: r.IngestedAt.UTC().Format(time.RFC3339Nano),
			TraceID: r.TraceID, PayloadDigest: r.PayloadDigest, PayloadJSON: r.PayloadJSON,
			PayloadRef: r.PayloadRef, BodyWithheld: r.PayloadJSON == "",
			ChainSeq: int64(r.ChainSeq), ChainHash: r.ChainHash,
		})
	}
	var buf bytes.Buffer
	w := parquet.NewGenericWriter[parquetRow](&buf, parquet.Compression(&parquet.Zstd))
	if _, err := w.Write(prs); err != nil {
		return nil, err
	}
	if err := w.Close(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// priorDate returns date-1 (YYYY-MM-DD) or "" on parse failure.
func priorDate(date string) string {
	t, err := time.Parse("2006-01-02", date)
	if err != nil {
		return ""
	}
	return t.AddDate(0, 0, -1).Format("2006-01-02")
}

// Scheduler runs the daily export over all unsealed prior days (AUD-FR-020).
type Scheduler struct {
	Exporter *Exporter
	PG       *pgstore.Store
	Interval time.Duration
}

// Run periodically exports unsealed prior days until ctx is cancelled.
func (s *Scheduler) Run(ctx context.Context) {
	if s.Interval <= 0 {
		s.Interval = time.Hour
	}
	t := time.NewTicker(s.Interval)
	defer t.Stop()
	s.runOnce(ctx)
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			s.runOnce(ctx)
		}
	}
}

func (s *Scheduler) runOnce(ctx context.Context) {
	today := time.Now().UTC().Format("2006-01-02")
	days, err := s.PG.ListUnsealedDays(ctx, today)
	if err != nil {
		return
	}
	for _, d := range days {
		_, _ = s.Exporter.ExportDay(ctx, d.TenantID, d.ChainDate)
	}
}
