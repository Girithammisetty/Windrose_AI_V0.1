// Package export implements chart data/image export (CHART-FR-041). CSV is
// fully real: full un-truncated resolved data, RFC 4180, UTF-8 BOM, streamed to
// a real object store (local MinIO-compatible filesystem store in dev) behind a
// short-lived HMAC-signed URL. PNG rendering requires a headless-browser
// renderer sidecar and is documented as infra-gated (see README); its adapter
// returns a clear PNG_RENDERER_UNAVAILABLE operation failure when the sidecar
// is not configured, never a fake image.
package export

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/csv"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"time"
)

// WriteCSV writes columns + rows in RFC 4180 with a UTF-8 BOM (CHART-FR-041).
func WriteCSV(columns []string, rows [][]any) ([]byte, error) {
	var buf bytes.Buffer
	buf.Write([]byte{0xEF, 0xBB, 0xBF}) // UTF-8 BOM
	w := csv.NewWriter(&buf)
	if len(columns) > 0 {
		if err := w.Write(columns); err != nil {
			return nil, err
		}
	}
	for _, row := range rows {
		rec := make([]string, len(row))
		for i, cell := range row {
			rec[i] = cellString(cell)
		}
		if err := w.Write(rec); err != nil {
			return nil, err
		}
	}
	w.Flush()
	if err := w.Error(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

func cellString(v any) string {
	switch t := v.(type) {
	case nil:
		return ""
	case string:
		return t
	case float64:
		return strconv.FormatFloat(t, 'f', -1, 64)
	case bool:
		return strconv.FormatBool(t)
	default:
		return fmt.Sprintf("%v", t)
	}
}

// ObjectStore stores an export artifact and returns a time-limited URL.
type ObjectStore interface {
	Put(ctx context.Context, key string, data []byte, ttl time.Duration) (url string, expires time.Time, err error)
}

// FSStore is a real local object store (dev equivalent of MinIO/S3). It writes
// artifacts under Root and returns a signed URL served by the service's
// GET /api/v1/exports/{key} endpoint. The signature+expiry are HMAC-verified.
type FSStore struct {
	Root      string
	PublicURL string // base URL of this service, e.g. http://localhost:8087
	Secret    []byte
}

// NewFSStore builds an FSStore.
func NewFSStore(root, publicURL string, secret []byte) *FSStore {
	_ = os.MkdirAll(root, 0o755)
	return &FSStore{Root: root, PublicURL: publicURL, Secret: secret}
}

// Put writes data and returns a signed, expiring URL.
func (s *FSStore) Put(_ context.Context, key string, data []byte, ttl time.Duration) (string, time.Time, error) {
	path := filepath.Join(s.Root, key)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return "", time.Time{}, err
	}
	if err := os.WriteFile(path, data, 0o644); err != nil {
		return "", time.Time{}, err
	}
	expires := time.Now().Add(ttl)
	sig := s.Sign(key, expires.Unix())
	url := fmt.Sprintf("%s/api/v1/exports/%s?exp=%d&sig=%s", s.PublicURL, key, expires.Unix(), sig)
	return url, expires, nil
}

// Read returns an artifact if the signature and expiry are valid.
func (s *FSStore) Read(key string, exp int64, sig string) ([]byte, error) {
	if time.Now().Unix() > exp {
		return nil, fmt.Errorf("expired")
	}
	if !hmac.Equal([]byte(sig), []byte(s.Sign(key, exp))) {
		return nil, fmt.Errorf("bad signature")
	}
	return os.ReadFile(filepath.Join(s.Root, filepath.Clean("/"+key)))
}

// Sign computes the HMAC-SHA256 signature for key+exp.
func (s *FSStore) Sign(key string, exp int64) string {
	m := hmac.New(sha256.New, s.Secret)
	_, _ = fmt.Fprintf(m, "%s|%d", key, exp)
	return hex.EncodeToString(m.Sum(nil))
}
