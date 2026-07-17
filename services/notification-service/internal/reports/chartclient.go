// Package reports implements scheduled dashboard-report subscriptions
// (NOTIF-FR-060, "Case Reports / Team Reports"): the domain's
// Temporal Schedule wiring, the chart-service data fetch, and the digest
// renderer. It reuses notification-service's existing real email channel
// (internal/channels/email) for delivery — no parallel sender.
package reports

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/google/uuid"
)

// ChartClient is a real HTTP client against chart-service's dashboard/chart
// data-fetch endpoints (CHART-FR-024/032) — the SAME endpoints the UI's
// dashboard view uses, not a reimplementation of chart resolution.
type ChartClient struct {
	BaseURL string
	HTTP    *http.Client
}

// NewChartClient builds a client for base (e.g. http://localhost:8320).
func NewChartClient(base string) *ChartClient {
	return &ChartClient{BaseURL: base, HTTP: &http.Client{Timeout: 30 * time.Second}}
}

type dashboardDTO struct {
	ID          uuid.UUID `json:"id"`
	Name        string    `json:"name"`
	WorkspaceID uuid.UUID `json:"workspace_id"`
}

type chartMetaDTO struct {
	ID        uuid.UUID `json:"id"`
	Name      string    `json:"name"`
	ChartType string    `json:"chart_type"`
}

type shapedResultDTO struct {
	Columns   []string `json:"columns"`
	Rows      [][]any  `json:"rows"`
	RowCount  int      `json:"row_count"`
	Truncated bool     `json:"truncated"`
}

type batchResultDTO struct {
	ChartID string           `json:"chart_id"`
	Data    *shapedResultDTO `json:"data,omitempty"`
	Error   *struct {
		Code    string `json:"code"`
		Message string `json:"message"`
	} `json:"error,omitempty"`
}

func (c *ChartClient) get(ctx context.Context, token, path string) ([]byte, int, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+path, nil)
	if err != nil {
		return nil, 0, err
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, 0, fmt.Errorf("chart-service unreachable: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	return raw, resp.StatusCode, nil
}

func (c *ChartClient) post(ctx context.Context, token, path string, body any) ([]byte, int, error) {
	b, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+path, bytes.NewReader(b))
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, 0, fmt.Errorf("chart-service unreachable: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	return raw, resp.StatusCode, nil
}

// ChartDigestItem is one chart's title + its REAL current data (or the error
// chart-service returned resolving it — surfaced, never fabricated).
type ChartDigestItem struct {
	Name      string
	ChartType string
	Columns   []string
	Rows      [][]any
	RowCount  int
	Truncated bool
	Error     string
}

// DashboardDigest is a dashboard's name plus every child chart's live data,
// exactly as chart-service resolves it today (CHART-FR-020/024) — the same
// path the dashboard UI uses, fetched via GET .../{id} + .../{id}/charts +
// POST .../{id}/data (batch, <=2 extra calls, matches BatchData's own budget).
type DashboardDigest struct {
	DashboardID   uuid.UUID
	DashboardName string
	Charts        []ChartDigestItem
}

// FetchDashboardDigest pulls a dashboard's metadata, its chart list (for
// titles), and a single batched data resolve — then merges them by chart_id.
func (c *ChartClient) FetchDashboardDigest(ctx context.Context, token string, dashboardID uuid.UUID) (*DashboardDigest, error) {
	raw, status, err := c.get(ctx, token, "/api/v1/dashboards/"+dashboardID.String())
	if err != nil {
		return nil, err
	}
	if status != http.StatusOK {
		return nil, fmt.Errorf("chart-service dashboard fetch status %d: %s", status, string(raw))
	}
	var dEnv struct {
		Data dashboardDTO `json:"data"`
	}
	if err := json.Unmarshal(raw, &dEnv); err != nil {
		return nil, fmt.Errorf("chart-service dashboard fetch: bad body: %w", err)
	}

	raw, status, err = c.get(ctx, token, "/api/v1/dashboards/"+dashboardID.String()+"/charts")
	if err != nil {
		return nil, err
	}
	if status != http.StatusOK {
		return nil, fmt.Errorf("chart-service chart list status %d: %s", status, string(raw))
	}
	var chEnv struct {
		Data []chartMetaDTO `json:"data"`
	}
	if err := json.Unmarshal(raw, &chEnv); err != nil {
		return nil, fmt.Errorf("chart-service chart list: bad body: %w", err)
	}
	names := map[string]chartMetaDTO{}
	for _, m := range chEnv.Data {
		names[m.ID.String()] = m
	}

	raw, status, err = c.post(ctx, token, "/api/v1/dashboards/"+dashboardID.String()+"/data", map[string]any{})
	if err != nil {
		return nil, err
	}
	if status != http.StatusOK {
		return nil, fmt.Errorf("chart-service dashboard data status %d: %s", status, string(raw))
	}
	var batchEnv struct {
		Data struct {
			Results []batchResultDTO `json:"results"`
		} `json:"data"`
	}
	if err := json.Unmarshal(raw, &batchEnv); err != nil {
		return nil, fmt.Errorf("chart-service dashboard data: bad body: %w", err)
	}

	digest := &DashboardDigest{DashboardID: dashboardID, DashboardName: dEnv.Data.Name}
	for _, res := range batchEnv.Data.Results {
		meta := names[res.ChartID]
		item := ChartDigestItem{Name: meta.Name, ChartType: meta.ChartType}
		if item.Name == "" {
			item.Name = res.ChartID
		}
		switch {
		case res.Error != nil:
			item.Error = res.Error.Message
		case res.Data != nil:
			item.Columns = res.Data.Columns
			item.Rows = res.Data.Rows
			item.RowCount = res.Data.RowCount
			item.Truncated = res.Data.Truncated
		}
		digest.Charts = append(digest.Charts, item)
	}
	return digest, nil
}
