package api

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"time"

	"github.com/google/uuid"
)

// RowFetcher fetches the live full row for a case from query-service
// (CASE-FR-001, GET ?with_row=true). The caller's bearer token is forwarded:
// query-service authorizes the fetch as the END USER (query.execution.execute
// + dataset read), so there is no confused-deputy service credential. A fetch
// failure never fails the case read: the handler returns the case with
// row:null + row_error (BR-5).
type RowFetcher interface {
	FetchRow(ctx context.Context, bearer string, tenant uuid.UUID, datasetURN, datasetVersion, rowPK string) (map[string]any, error)
}

// HTTPRowFetcher calls query-service's row-fetch endpoint. This is the real
// runtime adapter; when query-service is unreachable it returns an error which
// the handler surfaces as row_error (never a fake row).
type HTTPRowFetcher struct {
	BaseURL string
	client  *http.Client
}

// NewHTTPRowFetcher builds the real query-service-backed fetcher.
func NewHTTPRowFetcher(baseURL string) *HTTPRowFetcher {
	return &HTTPRowFetcher{BaseURL: baseURL, client: &http.Client{Timeout: 15 * time.Second}}
}

func (f *HTTPRowFetcher) FetchRow(ctx context.Context, bearer string, tenant uuid.UUID, datasetURN, datasetVersion, rowPK string) (map[string]any, error) {
	if f.BaseURL == "" {
		return nil, fmt.Errorf("query-service not configured")
	}
	u := fmt.Sprintf("%s/api/v1/rows?dataset_urn=%s&version=%s&row_pk=%s",
		f.BaseURL, url.QueryEscape(datasetURN), url.QueryEscape(datasetVersion), url.QueryEscape(rowPK))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, err
	}
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}
	resp, err := f.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("row fetch status %d", resp.StatusCode)
	}
	var out struct {
		Data map[string]any `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, err
	}
	return out.Data, nil
}

// SnapshotStore persists the closure row snapshot (CASE-FR-006, AC-8). The
// production adapter is MinIO/S3 (snapshots/<tenant>/<case>.json.gz); the
// filesystem adapter below writes the same gzip bytes durably to a local object
// root and is protocol-equivalent for local verification.
type SnapshotStore interface {
	Put(ctx context.Context, tenant, caseID uuid.UUID, row map[string]any) (ref string, err error)
	Get(ctx context.Context, ref string) (map[string]any, error)
	// PutBytes writes an arbitrary object (gzipped CSV export) and returns its
	// ref; GetBytes reads it back for the download endpoint (CASE-FR-044).
	PutBytes(ctx context.Context, key string, data []byte) (ref string, err error)
	GetBytes(ctx context.Context, ref string) ([]byte, error)
}

// EvidenceStore persists case evidence attachment bytes in object storage
// (task #77). The production adapter is MinIO/S3 (internal/blob); the pointer +
// metadata rows live in Postgres (case_evidence). Kept separate from
// SnapshotStore so evidence can use its own bucket/lifecycle.
type EvidenceStore interface {
	Put(ctx context.Context, key string, data []byte, contentType string) error
	Get(ctx context.Context, key string) ([]byte, error)
}

// FSSnapshotStore writes gzip snapshots to a local object root.
type FSSnapshotStore struct {
	Root string
}

// NewFSSnapshotStore builds a filesystem snapshot store rooted at root.
func NewFSSnapshotStore(root string) *FSSnapshotStore { return &FSSnapshotStore{Root: root} }

func (s *FSSnapshotStore) Put(_ context.Context, tenant, caseID uuid.UUID, row map[string]any) (string, error) {
	rel := filepath.Join("snapshots", tenant.String(), caseID.String()+".json.gz")
	full := filepath.Join(s.Root, rel)
	if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
		return "", err
	}
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	if err := json.NewEncoder(gz).Encode(row); err != nil {
		return "", err
	}
	if err := gz.Close(); err != nil {
		return "", err
	}
	if err := os.WriteFile(full, buf.Bytes(), 0o644); err != nil {
		return "", err
	}
	return rel, nil
}

func (s *FSSnapshotStore) Get(_ context.Context, ref string) (map[string]any, error) {
	b, err := os.ReadFile(filepath.Join(s.Root, ref))
	if err != nil {
		return nil, err
	}
	gz, err := gzip.NewReader(bytes.NewReader(b))
	if err != nil {
		return nil, err
	}
	defer gz.Close()
	var out map[string]any
	if err := json.NewDecoder(gz).Decode(&out); err != nil {
		return nil, err
	}
	return out, nil
}

// PutBytes writes raw object bytes under exports/<key> (CASE-FR-044 CSV export).
func (s *FSSnapshotStore) PutBytes(_ context.Context, key string, data []byte) (string, error) {
	rel := filepath.Join("exports", key)
	full := filepath.Join(s.Root, rel)
	if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
		return "", err
	}
	if err := os.WriteFile(full, data, 0o644); err != nil {
		return "", err
	}
	return rel, nil
}

// GetBytes reads back an object written by PutBytes.
func (s *FSSnapshotStore) GetBytes(_ context.Context, ref string) ([]byte, error) {
	return os.ReadFile(filepath.Join(s.Root, ref))
}
