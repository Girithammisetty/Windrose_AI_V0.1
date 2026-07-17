package results

import (
	"bufio"
	"encoding/csv"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/query-service/internal/engine"
)

// ErrGone marks results that were never written or already GC'd
// (QRY-FR-062: 24h retention; the history row persists).
var ErrGone = errors.New("results expired or not found")

// Part sizing: a part seals when either bound is hit, keeping the write
// buffer far below the 64MB per-execution cap (QRY-FR-060).
const (
	partMaxRows  = 10_000
	partMaxBytes = 4 << 20 // 4MB
)

// Manifest describes one execution's sealed result set.
type Manifest struct {
	Columns   []engine.Column `json:"columns"`
	Parts     []PartInfo      `json:"parts"`
	TotalRows int64           `json:"total_rows"`
	Warnings  []string        `json:"warnings,omitempty"`
	CreatedAt time.Time       `json:"created_at"`
}

// PartInfo is one sealed chunk.
type PartInfo struct {
	File string `json:"file"`
	Rows int64  `json:"rows"`
}

// Store is the filesystem-backed result store, layout
// <root>/results/<tenant>/<execution_id>/{manifest.json,part-N.jsonl}
// (tenant-prefixed per QRY-FR-060; object-storage backend is a deploy-time
// swap behind this same type).
type Store struct {
	Root string
	// Now is injectable for retention tests (AC-13).
	Now func() time.Time
}

func NewStore(root string) *Store { return &Store{Root: root, Now: time.Now} }

func (s *Store) dir(tenant, execID uuid.UUID) string {
	return filepath.Join(s.Root, "results", tenant.String(), execID.String())
}

// URI returns the logical result location recorded in history.
func (s *Store) URI(tenant, execID uuid.UUID) string {
	return fmt.Sprintf("results/%s/%s", tenant, execID)
}

// ---- Writer -----------------------------------------------------------------

// Writer streams rows into sealed parts with a bounded buffer. It
// implements engine.Sink behind the broker's ceiling-enforcing wrapper.
type Writer struct {
	store  *Store
	dir    string
	cols   []engine.Column
	types  []string
	buf    []json.RawMessage
	bufB   int
	parts  []PartInfo
	rows   int64
	bytes  int64
	warns  map[string]bool
	sealed bool
}

// NewWriter creates the result directory and returns a streaming writer.
func (s *Store) NewWriter(tenant, execID uuid.UUID) (*Writer, error) {
	dir := s.dir(tenant, execID)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	return &Writer{store: s, dir: dir, warns: map[string]bool{}}, nil
}

func (w *Writer) Start(cols []engine.Column) error {
	w.cols = cols
	w.types = make([]string, len(cols))
	for i, c := range cols {
		w.types[i] = c.Type
	}
	return nil
}

// Row applies the edge type mapping and buffers the encoded row; the buffer
// flushes into a sealed part at the size bounds (QRY-FR-060: never fully
// materialized in memory).
func (w *Writer) Row(vals []any) error {
	mapped := make([]any, len(vals))
	for i, v := range vals {
		t := ""
		if i < len(w.types) {
			t = w.types[i]
		}
		mv, warn := MapValue(v, t)
		mapped[i] = mv
		if warn != "" && !w.warns[warn] {
			w.warns[warn] = true
		}
	}
	line, err := json.Marshal(mapped)
	if err != nil {
		return err
	}
	w.buf = append(w.buf, line)
	w.bufB += len(line) + 1
	w.rows++
	w.bytes += int64(len(line)) + 1
	if len(w.buf) >= partMaxRows || w.bufB >= partMaxBytes {
		return w.flush()
	}
	return nil
}

// Bytes is the total encoded result size so far (result-byte ceiling).
func (w *Writer) Bytes() int64 { return w.bytes }

// Rows is the row count so far (result-row ceiling).
func (w *Writer) Rows() int64 { return w.rows }

func (w *Writer) flush() error {
	if len(w.buf) == 0 {
		return nil
	}
	name := fmt.Sprintf("part-%05d.jsonl", len(w.parts))
	f, err := os.Create(filepath.Join(w.dir, name))
	if err != nil {
		return err
	}
	bw := bufio.NewWriter(f)
	for _, line := range w.buf {
		if _, err := bw.Write(line); err != nil {
			f.Close()
			return err
		}
		if err := bw.WriteByte('\n'); err != nil {
			f.Close()
			return err
		}
	}
	if err := bw.Flush(); err != nil {
		f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	w.parts = append(w.parts, PartInfo{File: name, Rows: int64(len(w.buf))})
	w.buf = w.buf[:0]
	w.bufB = 0
	return nil
}

// Seal flushes the tail part and writes the manifest — the state-machine
// edge streaming_results → succeeded requires the manifest (BRD §4.2).
func (w *Writer) Seal() error {
	if w.sealed {
		return nil
	}
	if err := w.flush(); err != nil {
		return err
	}
	if w.cols == nil {
		w.cols = []engine.Column{}
	}
	var warns []string
	for warn := range w.warns {
		warns = append(warns, warn)
	}
	m := Manifest{Columns: w.cols, Parts: w.parts, TotalRows: w.rows, Warnings: warns, CreatedAt: w.store.Now().UTC()}
	b, err := json.Marshal(m)
	if err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(w.dir, "manifest.json"), b, 0o644); err != nil {
		return err
	}
	w.sealed = true
	return nil
}

// Abort removes a partial result directory (failed/cancelled runs).
func (w *Writer) Abort() { _ = os.RemoveAll(w.dir) }

// ---- Reader -----------------------------------------------------------------

// Page is one page of edge-JSON rows.
type Page struct {
	Columns    []engine.Column
	Rows       [][]any
	NextCursor string
	HasMore    bool
	TotalRows  int64
	Warnings   []string
}

// Manifest loads an execution's manifest; ErrGone after GC (BR-9, AC-13).
func (s *Store) Manifest(tenant, execID uuid.UUID) (*Manifest, error) {
	b, err := os.ReadFile(filepath.Join(s.dir(tenant, execID), "manifest.json"))
	if err != nil {
		return nil, ErrGone
	}
	var m Manifest
	if err := json.Unmarshal(b, &m); err != nil {
		return nil, ErrGone
	}
	return &m, nil
}

// ReadPage serves one page from sealed parts (QRY-FR-061). First-page reads
// never touch an engine (BR-14) — everything comes from the store.
func (s *Store) ReadPage(tenant, execID uuid.UUID, cursor Cursor, limit int) (*Page, error) {
	m, err := s.Manifest(tenant, execID)
	if err != nil {
		return nil, err
	}
	page := &Page{Columns: m.Columns, TotalRows: m.TotalRows, Warnings: m.Warnings}
	dir := s.dir(tenant, execID)
	part, skip := cursor.Part, cursor.Row
	for part < len(m.Parts) && len(page.Rows) < limit {
		rows, err := readPart(filepath.Join(dir, m.Parts[part].File))
		if err != nil {
			return nil, ErrGone
		}
		for skip < len(rows) && len(page.Rows) < limit {
			page.Rows = append(page.Rows, rows[skip])
			skip++
		}
		if skip >= len(rows) {
			part++
			skip = 0
		}
	}
	if part < len(m.Parts) {
		page.HasMore = true
		page.NextCursor = Cursor{Part: part, Row: skip}.Encode()
	}
	return page, nil
}

func readPart(path string) ([][]any, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var rows [][]any
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 16<<20)
	for sc.Scan() {
		var row []any
		if err := json.Unmarshal(sc.Bytes(), &row); err != nil {
			return nil, err
		}
		rows = append(rows, row)
	}
	return rows, sc.Err()
}

// ---- Export (QRY-FR-062) ----------------------------------------------------

// ExportCSV streams the full result set into a CSV file and returns its
// path. Parquet export is a Should-tier stub at the API layer.
func (s *Store) ExportCSV(tenant, execID uuid.UUID) (string, error) {
	m, err := s.Manifest(tenant, execID)
	if err != nil {
		return "", err
	}
	dir := filepath.Join(s.Root, "exports", tenant.String())
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	path := filepath.Join(dir, execID.String()+".csv")
	f, err := os.Create(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	cw := csv.NewWriter(f)
	header := make([]string, len(m.Columns))
	for i, c := range m.Columns {
		header[i] = c.Name
	}
	if err := cw.Write(header); err != nil {
		return "", err
	}
	resDir := s.dir(tenant, execID)
	for _, p := range m.Parts {
		rows, err := readPart(filepath.Join(resDir, p.File))
		if err != nil {
			return "", err
		}
		rec := make([]string, len(m.Columns))
		for _, row := range rows {
			for i := range rec {
				if i < len(row) && row[i] != nil {
					rec[i] = fmt.Sprint(row[i])
				} else {
					rec[i] = ""
				}
			}
			if err := cw.Write(rec); err != nil {
				return "", err
			}
		}
	}
	cw.Flush()
	return path, cw.Error()
}

// ---- Retention GC (QRY-FR-062) ----------------------------------------------

// GC removes result directories older than maxAge by manifest created_at.
// Returns bytes freed (metric result_gc_bytes_total).
func (s *Store) GC(maxAge time.Duration) (int64, error) {
	root := filepath.Join(s.Root, "results")
	tenants, err := os.ReadDir(root)
	if err != nil {
		if os.IsNotExist(err) {
			return 0, nil
		}
		return 0, err
	}
	cutoff := s.Now().Add(-maxAge)
	var freed int64
	for _, td := range tenants {
		execs, err := os.ReadDir(filepath.Join(root, td.Name()))
		if err != nil {
			continue
		}
		for _, ed := range execs {
			dir := filepath.Join(root, td.Name(), ed.Name())
			b, err := os.ReadFile(filepath.Join(dir, "manifest.json"))
			var createdAt time.Time
			if err == nil {
				var m Manifest
				if json.Unmarshal(b, &m) == nil {
					createdAt = m.CreatedAt
				}
			}
			if createdAt.IsZero() {
				if info, err := ed.Info(); err == nil {
					createdAt = info.ModTime()
				}
			}
			if createdAt.Before(cutoff) {
				freed += dirSize(dir)
				_ = os.RemoveAll(dir)
			}
		}
	}
	return freed, nil
}

func dirSize(dir string) int64 {
	var n int64
	_ = filepath.Walk(dir, func(_ string, info os.FileInfo, err error) error {
		if err == nil && !info.IsDir() {
			n += info.Size()
		}
		return nil
	})
	return n
}
