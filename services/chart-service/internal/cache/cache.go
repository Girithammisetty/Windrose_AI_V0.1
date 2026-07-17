// Package cache is chart-service's real Redis result cache (CHART-FR-030..033).
// It stores gzip-compressed shaped responses keyed by
// chart_version+variables+filters+page, maintains a reverse index
// src:{urn}→set<chart_id> for event-driven invalidation, computes strong
// ETags, and guards misses with a Redis singleflight lock. It speaks the real
// Redis wire protocol via go-common/redisx — no in-memory mode in the runtime.
package cache

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"time"

	"github.com/windrose-ai/chart-service/internal/domain"
	"github.com/windrose-ai/go-common/redisx"
)

// TTL and size limits (CHART-FR-030).
const (
	ResultTTL    = time.Hour
	MaxValueSize = 1 << 20 // 1MB gzip'd → skip cache above this
	lockTTL      = 30 * time.Second
)

// Redis is the real Redis-backed cache.
type Redis struct{ c *redisx.Client }

// NewRedis wraps a redisx client.
func NewRedis(c *redisx.Client) *Redis { return &Redis{c: c} }

// KeyInput carries the fields folded into the cache key digest.
type KeyInput struct {
	Variables  map[string]any  `json:"variables"`
	Filters    []domain.Filter `json:"filters"`
	Aggregated bool            `json:"aggregated"`
	Page       string          `json:"page"`
}

// Key builds the full cache key (CHART-FR-030).
func Key(tenant, chartID string, version int, in KeyInput) string {
	return fmt.Sprintf("chart:%s:%s:%d:%s", tenant, chartID, version, digest(in))
}

// ETag is the strong validator = the key digest (CHART-FR-032).
func ETag(tenant, chartID string, version int, in KeyInput) string {
	return `W/"` + digest(in) + fmt.Sprintf("-%d", version) + `"`
}

func digest(in KeyInput) string {
	b := canonicalJSON(in)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

// canonicalJSON produces a deterministic JSON encoding (sorted keys).
func canonicalJSON(in KeyInput) []byte {
	// sort filters for determinism.
	fs := append([]domain.Filter(nil), in.Filters...)
	sort.Slice(fs, func(i, j int) bool {
		return fs[i].Field+fs[i].Op < fs[j].Field+fs[j].Op
	})
	in.Filters = fs
	// json.Marshal sorts map keys, so variables encode deterministically.
	b, _ := json.Marshal(in)
	return b
}

// Get returns the cached shaped result for key, ok=false on miss.
func (r *Redis) Get(ctx context.Context, key string) (*domain.ShapedResult, bool, error) {
	raw, ok, err := r.c.Get(ctx, key)
	if err != nil || !ok {
		return nil, false, err
	}
	dec, err := gunzip([]byte(raw))
	if err != nil {
		return nil, false, nil // treat corrupt as miss
	}
	var res domain.ShapedResult
	if err := json.Unmarshal(dec, &res); err != nil {
		return nil, false, nil
	}
	return &res, true, nil
}

// Set stores a shaped result (gzip) and adds the reverse index entries.
func (r *Redis) Set(ctx context.Context, key, tenant, chartID string, srcURNs []string, res *domain.ShapedResult) error {
	body, _ := json.Marshal(res)
	gz := gzipBytes(body)
	if len(gz) > MaxValueSize {
		return nil // skip cache (metric would be logged by caller)
	}
	if err := r.c.Set(ctx, key, gz, ResultTTL); err != nil {
		return err
	}
	// reverse index: src:{urn} → set<chart_id>, and a per-chart key index for
	// pattern-free invalidation.
	pipe := r.c.R.Pipeline()
	pipe.SAdd(ctx, chartKeyIndex(tenant, chartID), key)
	pipe.Expire(ctx, chartKeyIndex(tenant, chartID), ResultTTL)
	for _, urn := range srcURNs {
		pipe.SAdd(ctx, srcIndex(urn), tenant+"|"+chartID)
	}
	_, err := pipe.Exec(ctx)
	return err
}

// InvalidateChart deletes all cached keys for a chart (CHART-FR-031).
func (r *Redis) InvalidateChart(ctx context.Context, tenant, chartID string) error {
	idx := chartKeyIndex(tenant, chartID)
	keys, err := r.c.R.SMembers(ctx, idx).Result()
	if err != nil {
		return err
	}
	keys = append(keys, idx)
	return r.c.Del(ctx, keys...)
}

// ChartsForURN returns tenant|chart pairs referencing urn (reverse index).
func (r *Redis) ChartsForURN(ctx context.Context, urn string) ([]string, error) {
	return r.c.R.SMembers(ctx, srcIndex(urn)).Result()
}

// AcquireLock takes a singleflight lock for key (CHART-FR-033). ok=true means
// the caller is the leader and must release it.
func (r *Redis) AcquireLock(ctx context.Context, key string) (bool, error) {
	return r.c.R.SetNX(ctx, "lock:"+key, 1, lockTTL).Result()
}

// ReleaseLock frees a singleflight lock.
func (r *Redis) ReleaseLock(ctx context.Context, key string) error {
	return r.c.Del(ctx, "lock:"+key)
}

func chartKeyIndex(tenant, chartID string) string { return "chartkeys:" + tenant + ":" + chartID }
func srcIndex(urn string) string                  { return "src:" + urn }

func gzipBytes(b []byte) []byte {
	var buf bytes.Buffer
	w := gzip.NewWriter(&buf)
	_, _ = w.Write(b)
	_ = w.Close()
	return buf.Bytes()
}

func gunzip(b []byte) ([]byte, error) {
	rr, err := gzip.NewReader(bytes.NewReader(b))
	if err != nil {
		return nil, err
	}
	defer func() { _ = rr.Close() }()
	return io.ReadAll(rr)
}
