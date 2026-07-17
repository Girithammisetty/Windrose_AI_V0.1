package results

import (
	"fmt"
	"math"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/query-service/internal/engine"
)

// QRY-FR-063 edge type mapping.
func TestMapValue(t *testing.T) {
	v, w := MapValue(nil, "string")
	assert.Nil(t, v)
	assert.Empty(t, w)

	v, _ = MapValue(int64(42), "integer")
	assert.Equal(t, int64(42), v)

	// int64 beyond 2^53 → string (lossless guarantee)
	v, _ = MapValue(int64(9007199254740993), "bigint")
	assert.Equal(t, "9007199254740993", v)
	v, _ = MapValue(int64(-9007199254740993), "bigint")
	assert.Equal(t, "-9007199254740993", v)
	v, _ = MapValue(int64(9007199254740991), "bigint")
	assert.Equal(t, int64(9007199254740991), v, "2^53-1 still a number")

	// NaN/±Inf → null + warning (V1 clients silently saw 0)
	for _, f := range []float64{math.NaN(), math.Inf(1), math.Inf(-1)} {
		v, w = MapValue(f, "float")
		assert.Nil(t, v)
		assert.Equal(t, WarnNaNReplaced, w)
	}
	v, _ = MapValue(3.14, "float")
	assert.Equal(t, 3.14, v)

	// decimal stays string upstream (engine canonicalizes)
	v, _ = MapValue("1284211.50", "decimal")
	assert.Equal(t, "1284211.50", v)

	v, _ = MapValue(true, "boolean")
	assert.Equal(t, true, v)

	// dates and timestamps
	ts := time.Date(2026, 6, 1, 12, 30, 45, 0, time.UTC)
	v, _ = MapValue(ts, "date")
	assert.Equal(t, "2026-06-01", v)
	v, _ = MapValue(ts, "timestamp")
	assert.Equal(t, "2026-06-01T12:30:45Z", v)

	// binary → base64
	v, _ = MapValue([]byte{0x01, 0x02}, "binary")
	assert.Equal(t, "AQI=", v)

	// nested list/struct preserved with recursive mapping
	v, w = MapValue([]any{int64(1), math.NaN()}, "list")
	assert.Equal(t, []any{int64(1), nil}, v)
	assert.Equal(t, WarnNaNReplaced, w)
	v, _ = MapValue(map[string]any{"k": int64(2)}, "struct")
	assert.Equal(t, map[string]any{"k": int64(2)}, v)
}

func TestCursorRoundTrip(t *testing.T) {
	c := Cursor{Part: 3, Row: 512}
	got, err := DecodeCursor(c.Encode())
	require.NoError(t, err)
	assert.Equal(t, c, got)

	got, err = DecodeCursor("")
	require.NoError(t, err)
	assert.Equal(t, Cursor{}, got)

	_, err = DecodeCursor("garbage!!")
	require.Error(t, err)
}

func writeRows(t *testing.T, s *Store, tenant, exec uuid.UUID, n int) {
	t.Helper()
	w, err := s.NewWriter(tenant, exec)
	require.NoError(t, err)
	require.NoError(t, w.Start([]engine.Column{{Name: "id", Type: "integer"}, {Name: "val", Type: "string"}}))
	for i := 0; i < n; i++ {
		require.NoError(t, w.Row([]any{int64(i), fmt.Sprintf("row-%d", i)}))
	}
	require.NoError(t, w.Seal())
}

// QRY-FR-060/061: chunked parts, stable cursors, full pagination sweep.
func TestStoreChunkedPagination(t *testing.T) {
	s := NewStore(t.TempDir())
	tenant, exec := uuid.New(), uuid.New()
	const total = 25000 // spans 3 parts at 10k rows/part
	writeRows(t, s, tenant, exec, total)

	m, err := s.Manifest(tenant, exec)
	require.NoError(t, err)
	assert.Equal(t, int64(total), m.TotalRows)
	assert.GreaterOrEqual(t, len(m.Parts), 3, "rows must be chunked into sealed parts")

	seen := 0
	cursor := Cursor{}
	var pages int
	for {
		page, err := s.ReadPage(tenant, exec, cursor, 4000)
		require.NoError(t, err)
		for _, row := range page.Rows {
			// rows arrive in insertion order with stable offsets (BR-9)
			assert.EqualValues(t, seen, row[0].(float64))
			seen++
		}
		pages++
		if !page.HasMore {
			break
		}
		cursor, err = DecodeCursor(page.NextCursor)
		require.NoError(t, err)
	}
	assert.Equal(t, total, seen)
	assert.Equal(t, 7, pages)
}

// BR-9: cursors are stable — re-reading the same cursor yields identical
// rows.
func TestStoreCursorStability(t *testing.T) {
	s := NewStore(t.TempDir())
	tenant, exec := uuid.New(), uuid.New()
	writeRows(t, s, tenant, exec, 100)

	p1, err := s.ReadPage(tenant, exec, Cursor{Part: 0, Row: 50}, 10)
	require.NoError(t, err)
	p2, err := s.ReadPage(tenant, exec, Cursor{Part: 0, Row: 50}, 10)
	require.NoError(t, err)
	assert.Equal(t, p1.Rows, p2.Rows)
}

func TestStoreGoneAfterGC(t *testing.T) {
	s := NewStore(t.TempDir())
	old := time.Now().Add(-25 * time.Hour)
	s.Now = func() time.Time { return old }
	tenant, exec := uuid.New(), uuid.New()
	writeRows(t, s, tenant, exec, 10) // manifest created_at = now-25h

	s.Now = time.Now
	freed, err := s.GC(24 * time.Hour)
	require.NoError(t, err)
	assert.Greater(t, freed, int64(0))

	_, err = s.ReadPage(tenant, exec, Cursor{}, 10)
	assert.ErrorIs(t, err, ErrGone)
	_, err = s.Manifest(tenant, exec)
	assert.ErrorIs(t, err, ErrGone)
}

func TestStoreGCKeepsFreshResults(t *testing.T) {
	s := NewStore(t.TempDir())
	tenant, exec := uuid.New(), uuid.New()
	writeRows(t, s, tenant, exec, 10)
	_, err := s.GC(24 * time.Hour)
	require.NoError(t, err)
	_, err = s.Manifest(tenant, exec)
	require.NoError(t, err, "fresh results survive GC")
}

func TestExportCSV(t *testing.T) {
	s := NewStore(t.TempDir())
	tenant, exec := uuid.New(), uuid.New()
	writeRows(t, s, tenant, exec, 5)
	path, err := s.ExportCSV(tenant, exec)
	require.NoError(t, err)
	assert.FileExists(t, path)
}

// Abort removes partials; nothing readable remains.
func TestWriterAbort(t *testing.T) {
	s := NewStore(t.TempDir())
	tenant, exec := uuid.New(), uuid.New()
	w, err := s.NewWriter(tenant, exec)
	require.NoError(t, err)
	require.NoError(t, w.Start([]engine.Column{{Name: "a", Type: "integer"}}))
	require.NoError(t, w.Row([]any{int64(1)}))
	w.Abort()
	_, err = s.Manifest(tenant, exec)
	assert.ErrorIs(t, err, ErrGone)
}

// Zero-row results still seal with columns intact.
func TestWriterEmptyResult(t *testing.T) {
	s := NewStore(t.TempDir())
	tenant, exec := uuid.New(), uuid.New()
	w, err := s.NewWriter(tenant, exec)
	require.NoError(t, err)
	require.NoError(t, w.Start([]engine.Column{{Name: "a", Type: "integer"}}))
	require.NoError(t, w.Seal())
	page, err := s.ReadPage(tenant, exec, Cursor{}, 10)
	require.NoError(t, err)
	assert.Empty(t, page.Rows)
	assert.False(t, page.HasMore)
	assert.Len(t, page.Columns, 1)
}
