// Package search is case-service's real OpenSearch adapter (CASE-FR-040..043).
// It speaks the OpenSearch REST wire protocol via the official opensearch-go
// client against a real cluster (deploy: http://localhost:9200) — there is no
// in-memory fake in the runtime path. Postgres remains the source of truth; the
// projection is eventual (≤5s, CASE-FR-041) and fed from case events. Every
// query carries the tenant filter (belt and braces with the tenant alias filter,
// AC-13). Doc writes are externally versioned by case_version so stale updates
// are discarded.
package search

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"
	opensearch "github.com/opensearch-project/opensearch-go/v3"

	"github.com/windrose-ai/case-service/internal/domain"
)

// Client is the real OpenSearch adapter.
type Client struct {
	os *opensearch.Client
}

// New dials the OpenSearch cluster at addr (e.g. http://localhost:9200).
func New(addr string) (*Client, error) {
	c, err := opensearch.NewClient(opensearch.Config{Addresses: []string{addr}})
	if err != nil {
		return nil, err
	}
	return &Client{os: c}, nil
}

// Ping checks cluster reachability (readyz).
func (c *Client) Ping(ctx context.Context) error {
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, "/", nil)
	resp, err := c.os.Perform(req)
	if err != nil {
		return err
	}
	defer drain(resp)
	if resp.StatusCode >= 300 {
		return fmt.Errorf("opensearch ping: status %d", resp.StatusCode)
	}
	return nil
}

// indexAlias is the tenant-facing alias (CASE-FR-040).
func indexAlias(tenant uuid.UUID) string { return "cases-" + tenant.String() }

// physicalIndex is a concrete index behind the alias (CASE-FR-043 swap).
func physicalIndex(tenant uuid.UUID, gen string) string {
	return "cases-" + tenant.String() + "-" + gen
}

const indexMapping = `{
  "mappings": {"properties": {
    "tenant_id": {"type": "keyword"}, "workspace_id": {"type": "keyword"},
    "case_number": {"type": "long"}, "status": {"type": "keyword"}, "severity": {"type": "keyword"},
    "assigned_to_id": {"type": "keyword"}, "disposition_code": {"type": "keyword"}, "disposition_category": {"type": "keyword"},
    "due_date": {"type": "date"}, "resolved_at": {"type": "date"}, "created_at": {"type": "date"}, "updated_at": {"type": "date"},
    "dataset_urn": {"type": "keyword"}, "source_query_urns": {"type": "keyword"},
    "description": {"type": "text"}, "comment_text": {"type": "text"},
    "display_projection": {"type": "flat_object"}, "custom_fields": {"type": "flat_object"},
    "case_version": {"type": "long"}
  }},
  "settings": {"index.number_of_shards": 1, "index.refresh_interval": "1s"}
}`

// EnsureIndex creates the tenant index + alias if absent (idempotent). The
// alias carries a tenant_id filter so it is impossible to read another tenant's
// docs through it (AC-13).
func (c *Client) EnsureIndex(ctx context.Context, tenant uuid.UUID) error {
	alias := indexAlias(tenant)
	if exists, err := c.aliasExists(ctx, alias); err != nil {
		return err
	} else if exists {
		return nil
	}
	idx := physicalIndex(tenant, "v1")
	if err := c.createIndex(ctx, idx); err != nil {
		return err
	}
	return c.putAlias(ctx, idx, alias, tenant)
}

func (c *Client) createIndex(ctx context.Context, idx string) error {
	req, _ := http.NewRequestWithContext(ctx, http.MethodPut, "/"+idx, strings.NewReader(indexMapping))
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.os.Perform(req)
	if err != nil {
		return err
	}
	defer drain(resp)
	if resp.StatusCode >= 300 {
		// A concurrent creator is fine; any other 4xx (e.g. a bad mapping) must
		// surface rather than be swallowed into a later "no such index" alias error.
		b := body(resp)
		if strings.Contains(b, "resource_already_exists_exception") {
			return nil
		}
		return fmt.Errorf("create index %s: status %d: %s", idx, resp.StatusCode, b)
	}
	return nil
}

func (c *Client) aliasExists(ctx context.Context, alias string) (bool, error) {
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, "/_alias/"+alias, nil)
	resp, err := c.os.Perform(req)
	if err != nil {
		return false, err
	}
	defer drain(resp)
	return resp.StatusCode == http.StatusOK, nil
}

func (c *Client) putAlias(ctx context.Context, idx, alias string, tenant uuid.UUID) error {
	action := map[string]any{"actions": []any{
		map[string]any{"add": map[string]any{
			"index": idx, "alias": alias,
			"filter": map[string]any{"term": map[string]any{"tenant_id": tenant.String()}},
		}},
	}}
	return c.aliasAction(ctx, action)
}

func (c *Client) aliasAction(ctx context.Context, action map[string]any) error {
	b, _ := json.Marshal(action)
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, "/_aliases", bytes.NewReader(b))
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.os.Perform(req)
	if err != nil {
		return err
	}
	defer drain(resp)
	if resp.StatusCode >= 300 {
		return fmt.Errorf("alias action: status %d: %s", resp.StatusCode, body(resp))
	}
	return nil
}

// Doc is the projected search document (CASE-FR-040). The id is indexed into
// _source (not only _id metadata) so list/search results and filter→ids
// resolution can read it back.
type Doc struct {
	ID                  string            `json:"id"`
	TenantID            string            `json:"tenant_id"`
	WorkspaceID         string            `json:"workspace_id"`
	CaseNumber          int64             `json:"case_number"`
	Status              string            `json:"status"`
	Severity            string            `json:"severity"`
	// AssigneeID serializes as assigned_to_id — the SAME field name the REST
	// detail view uses (handlers_cases.go caseView), so list/search rows and
	// case detail agree and the bff's Case.assignee hydration works for both.
	// (Previously "assignee_id"; existing indexes need POST /admin/reindex —
	// it rebuilds a fresh generation with this mapping and swaps the alias.)
	AssigneeID          string            `json:"assigned_to_id,omitempty"`
	DispositionCode     string            `json:"disposition_code,omitempty"`
	DispositionCategory string            `json:"disposition_category,omitempty"`
	DueDate             *time.Time        `json:"due_date,omitempty"`
	ResolvedAt          *time.Time        `json:"resolved_at,omitempty"`
	CreatedAt           time.Time         `json:"created_at"`
	UpdatedAt           time.Time         `json:"updated_at"`
	DatasetURN          string            `json:"dataset_urn"`
	SourceQueryURNs     []string          `json:"source_query_urns"`
	Description         string            `json:"description,omitempty"`
	CommentText         string            `json:"comment_text,omitempty"`
	DisplayProjection   map[string]string `json:"display_projection"`
	CustomFields        map[string]any    `json:"custom_fields"`
	CaseVersion         int               `json:"case_version"`
}

// DocFromCase projects a case (+ comment text) into a search doc (CASE-FR-041).
func DocFromCase(c *domain.Case, commentText string) Doc {
	d := Doc{
		ID: c.ID.String(), TenantID: c.TenantID.String(), WorkspaceID: c.WorkspaceID.String(),
		CaseNumber: c.CaseNumber, Status: c.Status.String(), Severity: c.Severity,
		CreatedAt: c.CreatedAt, UpdatedAt: c.UpdatedAt, DatasetURN: c.DatasetURN,
		SourceQueryURNs: c.SourceQueryURNs, Description: c.Description, CommentText: commentText,
		DisplayProjection: c.DisplayProjection, CustomFields: c.CustomFields, CaseVersion: c.CaseVersion,
		DueDate: &c.DueDate, ResolvedAt: c.ResolvedAt,
	}
	if c.AssignedToID != nil {
		d.AssigneeID = c.AssignedToID.String()
	}
	if d.DisplayProjection == nil {
		d.DisplayProjection = map[string]string{}
	}
	if d.CustomFields == nil {
		d.CustomFields = map[string]any{}
	}
	return d
}

// IndexDoc upserts a doc into the tenant alias with external versioning by
// case_version (CASE-FR-041). A 409 (a newer version already indexed) is
// treated as success — the stale write is correctly discarded.
func (c *Client) IndexDoc(ctx context.Context, tenant uuid.UUID, d Doc) error {
	if err := c.EnsureIndex(ctx, tenant); err != nil {
		return err
	}
	b, _ := json.Marshal(d)
	path := fmt.Sprintf("/%s/_doc/%s?version=%d&version_type=external", indexAlias(tenant), d.ID, d.CaseVersion)
	req, _ := http.NewRequestWithContext(ctx, http.MethodPut, path, bytes.NewReader(b))
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.os.Perform(req)
	if err != nil {
		return err
	}
	defer drain(resp)
	if resp.StatusCode == http.StatusConflict {
		return nil // newer version already present; discard stale write
	}
	if resp.StatusCode >= 300 {
		return fmt.Errorf("index doc: status %d: %s", resp.StatusCode, body(resp))
	}
	return nil
}

// IndexDocInto upserts into a specific physical index (reindex path).
func (c *Client) IndexDocInto(ctx context.Context, idx string, d Doc) error {
	b, _ := json.Marshal(d)
	path := fmt.Sprintf("/%s/_doc/%s?version=%d&version_type=external", idx, d.ID, d.CaseVersion)
	req, _ := http.NewRequestWithContext(ctx, http.MethodPut, path, bytes.NewReader(b))
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.os.Perform(req)
	if err != nil {
		return err
	}
	defer drain(resp)
	if resp.StatusCode == http.StatusConflict {
		return nil
	}
	if resp.StatusCode >= 300 {
		return fmt.Errorf("index doc into %s: status %d: %s", idx, resp.StatusCode, body(resp))
	}
	return nil
}

// Refresh forces a refresh so writes are immediately searchable (tests only —
// runtime relies on the 1s refresh_interval, well inside the 5s window).
func (c *Client) Refresh(ctx context.Context, tenant uuid.UUID) error {
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, "/"+indexAlias(tenant)+"/_refresh", nil)
	resp, err := c.os.Perform(req)
	if err != nil {
		return err
	}
	defer drain(resp)
	return nil
}

// Reindex rebuilds a tenant's index into a fresh generation and atomically
// swaps the alias (CASE-FR-043). docs is the full current projection.
func (c *Client) Reindex(ctx context.Context, tenant uuid.UUID, docs []Doc) error {
	gen := "v" + fmt.Sprint(time.Now().UnixNano())
	idx := physicalIndex(tenant, gen)
	if err := c.createIndex(ctx, idx); err != nil {
		return err
	}
	for _, d := range docs {
		if err := c.IndexDocInto(ctx, idx, d); err != nil {
			return err
		}
	}
	alias := indexAlias(tenant)
	// Atomic swap: remove alias from all indices, add to the new one.
	action := map[string]any{"actions": []any{
		map[string]any{"remove": map[string]any{"index": "cases-" + tenant.String() + "-*", "alias": alias}},
		map[string]any{"add": map[string]any{"index": idx, "alias": alias,
			"filter": map[string]any{"term": map[string]any{"tenant_id": tenant.String()}}}},
	}}
	return c.aliasAction(ctx, action)
}

func drain(resp *http.Response) {
	_, _ = io.Copy(io.Discard, resp.Body)
	_ = resp.Body.Close()
}

func body(resp *http.Response) string {
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 2048))
	return string(b)
}

func encodeCursor(sort []any) string {
	b, _ := json.Marshal(sort)
	return base64.RawURLEncoding.EncodeToString(b)
}

func decodeCursor(cur string) ([]any, error) {
	raw, err := base64.RawURLEncoding.DecodeString(cur)
	if err != nil {
		return nil, err
	}
	var out []any
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	return out, nil
}
