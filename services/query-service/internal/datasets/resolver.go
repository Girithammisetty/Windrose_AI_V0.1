// Package datasets resolves {{dataset(...)}} references to fully qualified
// physical tables via dataset-service (QRY-FR-005). Resolution supplies the
// engine-quoted identifier (BR-1: never user-typed strings), size stats for
// cost estimation (QRY-FR-041) and column PII tags for history redaction
// (BR-12).
package datasets

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/query-service/internal/domain"
)

// Column is one dataset column with its profile tag (BR-12).
type Column struct {
	Name   string `json:"name"`
	Type   string `json:"type"`
	PIITag string `json:"pii_tag,omitempty"` // e.g. "pii:email"; "" if none
}

// Meta is the resolved dataset metadata.
type Meta struct {
	Name          string   `json:"name"`
	Version       int      `json:"version"`
	URN           string   `json:"urn"`
	PhysicalIdent string   `json:"physical_ident"` // engine-quoted, e.g. "main"."orders_v3"
	Namespace     string   `json:"namespace"`      // lowercased schema or catalog.schema
	SizeBytes     int64    `json:"size_bytes"`
	RowCount      int64    `json:"row_count"`
	Columns       []Column `json:"columns"`
	Deprecated    bool     `json:"deprecated"`
	// SourceURIs are the physical object-store files backing this dataset
	// (e.g. s3://.../data/xxx.parquet), returned by dataset-service /resolve
	// so query-service can materialize them into DuckDB (QRY-FR-005).
	SourceURIs []string `json:"source_uris"`
	// SourceFormat is the file format of SourceURIs, e.g. "parquet".
	SourceFormat string `json:"source_format"`
}

// Resolver resolves a logical dataset reference for a tenant. version 0
// means latest. A deleted/unknown dataset returns domain.EDatasetNotFound
// (BR-4: V1 silently broke, we fail loudly).
type Resolver interface {
	Resolve(ctx context.Context, tenant uuid.UUID, name string, version int) (*Meta, error)
}

// QuoteIdent double-quotes one identifier part safely (engine-quoted
// identifiers per BR-1; doubling embedded quotes).
func QuoteIdent(parts ...string) string {
	quoted := make([]string, len(parts))
	for i, p := range parts {
		quoted[i] = `"` + strings.ReplaceAll(p, `"`, `""`) + `"`
	}
	return strings.Join(quoted, ".")
}

// ---- Static resolver (tests / local dev) -----------------------------------

// Static is an in-memory Resolver keyed by tenant and "name@version"
// ("name@0" = latest).
type Static struct {
	mu   sync.RWMutex
	data map[uuid.UUID]map[string]*Meta
}

func NewStatic() *Static { return &Static{data: map[uuid.UUID]map[string]*Meta{}} }

// Put registers a dataset version; it also becomes "latest" when
// latest=true.
func (s *Static) Put(tenant uuid.UUID, m Meta, latest bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	byName := s.data[tenant]
	if byName == nil {
		byName = map[string]*Meta{}
		s.data[tenant] = byName
	}
	cp := m
	byName[fmt.Sprintf("%s@%d", strings.ToLower(m.Name), m.Version)] = &cp
	if latest {
		cp2 := m
		byName[fmt.Sprintf("%s@0", strings.ToLower(m.Name))] = &cp2
	}
}

// Delete removes a dataset entirely (dataset.deleted consumption).
func (s *Static) Delete(tenant uuid.UUID, name string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	prefix := strings.ToLower(name) + "@"
	for k := range s.data[tenant] {
		if strings.HasPrefix(k, prefix) {
			delete(s.data[tenant], k)
		}
	}
}

func (s *Static) Resolve(_ context.Context, tenant uuid.UUID, name string, version int) (*Meta, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	m, ok := s.data[tenant][fmt.Sprintf("%s@%d", strings.ToLower(name), version)]
	if !ok {
		return nil, domain.EDatasetNotFound(fmt.Sprintf("dataset %q (version %d) not found", name, version))
	}
	cp := *m
	return &cp, nil
}

// ---- HTTP resolver (dataset-service client) --------------------------------

// HTTP resolves via dataset-service's REST API with an ETag-aware cache
// (QRY-FR-005). Cross-service calls in tests use fakes, never live services
// (CONVENTIONS: contracts).
type HTTP struct {
	BaseURL string
	Client  *http.Client

	mu    sync.RWMutex
	cache map[string]httpCacheEntry
}

type httpCacheEntry struct {
	etag    string
	meta    *Meta
	fetched time.Time
}

// httpCacheMaxEntries bounds the resolution cache so it can't grow without
// limit across distinct (tenant, dataset, version) tuples — the 30s freshness
// window alone never evicts, so a long-lived process would otherwise leak one
// entry per unique dataset version ever resolved.
const httpCacheMaxEntries = 2048

func NewHTTP(baseURL string) *HTTP {
	return &HTTP{BaseURL: strings.TrimRight(baseURL, "/"), Client: &http.Client{Timeout: 5 * time.Second}, cache: map[string]httpCacheEntry{}}
}

func (h *HTTP) Resolve(ctx context.Context, tenant uuid.UUID, name string, version int) (*Meta, error) {
	key := tenant.String() + "/" + strings.ToLower(name) + "@" + fmt.Sprint(version)
	h.mu.RLock()
	entry, cached := h.cache[key]
	h.mu.RUnlock()
	if cached && time.Since(entry.fetched) < 30*time.Second {
		cp := *entry.meta
		return &cp, nil
	}
	url := fmt.Sprintf("%s/api/v1/datasets/resolve?name=%s&version=%d&tenant=%s", h.BaseURL, name, version, tenant.String())
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	if cached && entry.etag != "" {
		req.Header.Set("If-None-Match", entry.etag)
	}
	resp, err := h.Client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("dataset-service: %w", err)
	}
	defer resp.Body.Close()
	switch resp.StatusCode {
	case http.StatusNotModified:
		h.touch(key, entry)
		cp := *entry.meta
		return &cp, nil
	case http.StatusOK:
		var m Meta
		if err := json.NewDecoder(resp.Body).Decode(&m); err != nil {
			return nil, fmt.Errorf("dataset-service decode: %w", err)
		}
		h.mu.Lock()
		h.evictIfFullLocked(key)
		h.cache[key] = httpCacheEntry{etag: resp.Header.Get("ETag"), meta: &m, fetched: time.Now()}
		h.mu.Unlock()
		return &m, nil
	case http.StatusNotFound:
		return nil, domain.EDatasetNotFound(fmt.Sprintf("dataset %q (version %d) not found", name, version))
	default:
		return nil, fmt.Errorf("dataset-service: unexpected status %d", resp.StatusCode)
	}
}

func (h *HTTP) touch(key string, entry httpCacheEntry) {
	entry.fetched = time.Now()
	h.mu.Lock()
	h.evictIfFullLocked(key)
	h.cache[key] = entry
	h.mu.Unlock()
}

// evictIfFullLocked drops the oldest-fetched entry when the cache is at
// capacity and `key` is not already present, bounding memory against unbounded
// (tenant, dataset, version) cardinality. The caller must hold h.mu.
func (h *HTTP) evictIfFullLocked(key string) {
	if len(h.cache) < httpCacheMaxEntries {
		return
	}
	if _, ok := h.cache[key]; ok {
		return // replacing an existing key doesn't grow the map
	}
	var oldestKey string
	var oldest time.Time
	first := true
	for k, e := range h.cache {
		if first || e.fetched.Before(oldest) {
			oldestKey, oldest, first = k, e.fetched, false
		}
	}
	delete(h.cache, oldestKey)
}
