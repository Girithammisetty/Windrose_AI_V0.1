package search

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"

	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/domain"
)

// Params is a parsed list/search request (CASE-FR-042).
type Params struct {
	Q                   string
	Statuses            []string // expanded from filter[status] incl. open/closed pseudo-filters
	AssigneeID          string
	Severity            string
	DispositionCategory string
	QueryURN            string
	Due                 string // overdue|today|week
	Facets              []string
	Limit               int
	Cursor              string
	Now                 func() interface{}
}

// Result is the search response (CASE-FR-042).
type Result struct {
	Docs        []map[string]any
	Facets      map[string]map[string]int64
	NextCursor  string
	HasMore     bool
	TookMS      int
}

// ExpandStatus maps a filter[status] value onto concrete statuses, honoring the
// V1 pseudo-filters open={draft,in_progress} and closed={resolved,closed}.
func ExpandStatus(v string) []string {
	switch v {
	case "open":
		return []string{domain.StatusDraft.String(), domain.StatusInProgress.String()}
	case "closed":
		return []string{domain.StatusResolved.String(), domain.StatusClosed.String()}
	case "":
		return nil
	default:
		return []string{v}
	}
}

// Search runs the query against the tenant alias. Every query carries the
// tenant_id filter in addition to the alias filter (AC-13).
func (c *Client) Search(ctx context.Context, tenant uuid.UUID, p Params) (*Result, error) {
	if p.Limit <= 0 {
		p.Limit = 50
	}
	filters := []any{map[string]any{"term": map[string]any{"tenant_id": tenant.String()}}}
	if len(p.Statuses) > 0 {
		filters = append(filters, map[string]any{"terms": map[string]any{"status": toAnySlice(p.Statuses)}})
	}
	if p.AssigneeID != "" {
		filters = append(filters, map[string]any{"term": map[string]any{"assigned_to_id": p.AssigneeID}})
	}
	if p.Severity != "" {
		filters = append(filters, map[string]any{"term": map[string]any{"severity": p.Severity}})
	}
	if p.DispositionCategory != "" {
		filters = append(filters, map[string]any{"term": map[string]any{"disposition_category": p.DispositionCategory}})
	}
	if p.QueryURN != "" {
		filters = append(filters, map[string]any{"term": map[string]any{"source_query_urns": p.QueryURN}})
	}
	switch p.Due {
	case "overdue":
		filters = append(filters, map[string]any{"range": map[string]any{"due_date": map[string]any{"lt": "now"}}})
	case "today":
		filters = append(filters, map[string]any{"range": map[string]any{"due_date": map[string]any{"gte": "now/d", "lte": "now/d"}}})
	case "week":
		filters = append(filters, map[string]any{"range": map[string]any{"due_date": map[string]any{"gte": "now", "lte": "now+7d"}}})
	}

	query := map[string]any{"bool": map[string]any{"filter": filters}}
	if p.Q != "" {
		query["bool"].(map[string]any)["must"] = []any{
			map[string]any{"multi_match": map[string]any{
				"query": p.Q, "fields": []string{"description", "comment_text"},
			}},
		}
	}

	reqBody := map[string]any{
		"size":  p.Limit,
		"query": query,
		"sort":  []any{map[string]any{"created_at": "desc"}, map[string]any{"case_number": "desc"}},
	}
	if p.Cursor != "" {
		after, err := decodeCursor(p.Cursor)
		if err != nil {
			return nil, fmt.Errorf("bad cursor: %w", err)
		}
		reqBody["search_after"] = after
	}
	if len(p.Facets) > 0 {
		aggs := map[string]any{}
		for _, f := range p.Facets {
			field := facetField(f)
			if field == "" {
				continue
			}
			aggs[f] = map[string]any{"terms": map[string]any{"field": field, "size": 50}}
		}
		reqBody["aggs"] = aggs
	}

	b, _ := json.Marshal(reqBody)
	path := "/" + indexAlias(tenant) + "/_search"
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, path, bytes.NewReader(b))
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.os.Perform(req)
	if err != nil {
		return nil, err
	}
	defer drain(resp)
	if resp.StatusCode >= 300 {
		return nil, fmt.Errorf("search: status %d: %s", resp.StatusCode, body(resp))
	}

	var raw struct {
		Took int `json:"took"`
		Hits struct {
			Hits []struct {
				Source map[string]any `json:"_source"`
				Sort   []any          `json:"sort"`
			} `json:"hits"`
		} `json:"hits"`
		Aggregations map[string]struct {
			Buckets []struct {
				Key      any `json:"key"`
				DocCount int64 `json:"doc_count"`
			} `json:"buckets"`
		} `json:"aggregations"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&raw); err != nil {
		return nil, err
	}

	res := &Result{TookMS: raw.Took, Facets: map[string]map[string]int64{}}
	var lastSort []any
	for _, h := range raw.Hits.Hits {
		res.Docs = append(res.Docs, h.Source)
		lastSort = h.Sort
	}
	if len(res.Docs) == p.Limit && lastSort != nil {
		res.HasMore = true
		res.NextCursor = encodeCursor(lastSort)
	}
	for name, agg := range raw.Aggregations {
		m := map[string]int64{}
		for _, bk := range agg.Buckets {
			m[fmt.Sprint(bk.Key)] = bk.DocCount
		}
		res.Facets[name] = m
	}
	return res, nil
}

func facetField(f string) string {
	switch f {
	case "status", "severity":
		return f
	case "assignee":
		return "assigned_to_id"
	case "disposition_category":
		return "disposition_category"
	default:
		return ""
	}
}

func toAnySlice(ss []string) []any {
	out := make([]any, len(ss))
	for i, s := range ss {
		out[i] = s
	}
	return out
}

// CollectIDs resolves a filter to case ids by paging the tenant index with
// search_after, up to capN (CASE-FR-030 filter-based bulk, 5,000 cap). Reads the
// id field from _source; the belt-and-braces tenant filter is always applied.
func (c *Client) CollectIDs(ctx context.Context, tenant uuid.UUID, p Params, capN int) ([]string, error) {
	p.Facets = nil
	p.Limit = 500
	var ids []string
	for len(ids) < capN {
		res, err := c.Search(ctx, tenant, p)
		if err != nil {
			return nil, err
		}
		for _, d := range res.Docs {
			if id, _ := d["id"].(string); id != "" {
				ids = append(ids, id)
				if len(ids) >= capN {
					return ids, nil
				}
			}
		}
		if !res.HasMore || res.NextCursor == "" {
			break
		}
		p.Cursor = res.NextCursor
	}
	return ids, nil
}
