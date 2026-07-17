package api

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/query-service/internal/datasets"
	"github.com/windrose-ai/query-service/internal/domain"
	"github.com/windrose-ai/query-service/internal/exec"
	"github.com/windrose-ai/query-service/internal/results"
)

// DatasetNamer resolves a dataset id to its logical name via dataset-service's
// public API UNDER THE CALLER'S TOKEN — the row fetch runs entirely with the
// end user's real permissions (no confused deputy).
type DatasetNamer interface {
	Name(ctx context.Context, bearer string, datasetID string) (string, error)
}

// HTTPDatasetNamer is the real dataset-service-backed namer.
type HTTPDatasetNamer struct {
	BaseURL string
	Client  *http.Client
}

// NewHTTPDatasetNamer builds the dataset-service client used by /rows.
func NewHTTPDatasetNamer(baseURL string) *HTTPDatasetNamer {
	return &HTTPDatasetNamer{BaseURL: strings.TrimRight(baseURL, "/"), Client: &http.Client{Timeout: 5 * time.Second}}
}

func (h *HTTPDatasetNamer) Name(ctx context.Context, bearer, datasetID string) (string, error) {
	if h.BaseURL == "" {
		return "", domain.EValidation("DATASET_SERVICE_URL is not configured")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, h.BaseURL+"/api/v1/datasets/"+datasetID, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("Authorization", "Bearer "+bearer)
	resp, err := h.Client.Do(req)
	if err != nil {
		return "", fmt.Errorf("dataset-service: %w", err)
	}
	defer resp.Body.Close()
	switch resp.StatusCode {
	case http.StatusOK:
		var out struct {
			Data struct {
				Name string `json:"name"`
			} `json:"data"`
		}
		if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
			return "", fmt.Errorf("dataset-service decode: %w", err)
		}
		if out.Data.Name == "" {
			return "", domain.EDatasetNotFound("dataset has no name")
		}
		return out.Data.Name, nil
	case http.StatusNotFound:
		return "", domain.EDatasetNotFound("dataset not found")
	case http.StatusForbidden, http.StatusUnauthorized:
		return "", domain.EPermissionDenied("caller may not read the case's source dataset")
	default:
		return "", fmt.Errorf("dataset-service: unexpected status %d", resp.StatusCode)
	}
}

// parseDatasetURN extracts (tenant, datasetID) from the platform dataset URN
// `wr:<tenant>:dataset:dataset/<id>` (dataset-service urn.py format).
func parseDatasetURN(urn string) (uuid.UUID, string, bool) {
	parts := strings.SplitN(urn, ":", 4)
	if len(parts) != 4 || parts[0] != "wr" || parts[2] != "dataset" {
		return uuid.Nil, "", false
	}
	tenant, err := uuid.Parse(parts[1])
	if err != nil {
		return uuid.Nil, "", false
	}
	rid, ok := strings.CutPrefix(parts[3], "dataset/")
	if !ok || rid == "" {
		return uuid.Nil, "", false
	}
	return tenant, rid, true
}

// handleGetRow implements GET /api/v1/rows?dataset_urn=&version=&row_pk=
// (CASE-FR-001 live row fetch): it resolves the URN to a dataset name with the
// caller's token, then runs `SELECT * ... WHERE <pk> = :row_pk LIMIT 1`
// through the exact same plan/broker path as /sql/run (tenant guard, safe
// binding, ceilings, history). The PK column defaults to the dataset's first
// column (dataset-service exposes no PK metadata yet); pk_column overrides.
func (s *Server) handleGetRow(w http.ResponseWriter, r *http.Request) {
	op, ok := opFrom(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("invalid claims"))
		return
	}
	q := r.URL.Query()
	urn, rowPK := q.Get("dataset_urn"), q.Get("row_pk")
	if urn == "" || rowPK == "" {
		writeErr(w, r, domain.EValidation("dataset_urn and row_pk are required"))
		return
	}
	urnTenant, datasetID, ok := parseDatasetURN(urn)
	if !ok {
		writeErr(w, r, domain.EValidation("dataset_urn must be wr:<tenant>:dataset:dataset/<id>"))
		return
	}
	if urnTenant != op.Tenant {
		s.notFound(w, r) // MASTER-FR-003: cross-tenant indistinguishable from missing
		return
	}
	version := 0
	if v := q.Get("version"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 0 {
			writeErr(w, r, domain.EValidation("version must be a non-negative integer"))
			return
		}
		version = n
	}

	bearer := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	name, err := s.Datasets.Name(r.Context(), bearer, datasetID)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if strings.ContainsAny(name, "'{}") {
		writeErr(w, r, domain.EValidation("dataset name is not addressable"))
		return
	}

	// Resolve metadata to learn the (ordered) columns for PK selection.
	meta, err := s.Broker.Resolver.Resolve(r.Context(), op.Tenant, name, version)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if len(meta.Columns) == 0 {
		writeErr(w, r, domain.EDatasetNotFound("dataset has no column metadata"))
		return
	}
	pkCol := q.Get("pk_column")
	if pkCol == "" {
		pkCol = meta.Columns[0].Name
	} else {
		found := false
		for _, c := range meta.Columns {
			if strings.EqualFold(c.Name, pkCol) {
				pkCol, found = c.Name, true
				break
			}
		}
		if !found {
			writeErr(w, r, domain.EValidation("pk_column is not a column of the dataset"))
			return
		}
	}

	ref := fmt.Sprintf("{{dataset('%s')}}", name)
	if version > 0 {
		ref = fmt.Sprintf("{{dataset('%s', version=%d)}}", name, version)
	}
	sql := fmt.Sprintf("SELECT * FROM %s WHERE %s = :row_pk LIMIT 1", ref, datasets.QuoteIdent(pkCol))
	pkJSON, _ := json.Marshal(rowPK)
	rr := exec.RunRequest{
		PlanRequest: exec.PlanRequest{
			Op:      op,
			SQLText: sql,
			Decls:   []domain.VariableDecl{{Name: "row_pk", Type: domain.VarString}},
			Values:  map[string]json.RawMessage{"row_pk": pkJSON},
			Limit:   1,
		},
		Mode:     "sync",
		UseCache: true,
	}
	if ws := ClaimsFrom(r.Context()).WorkspaceID; ws != "" {
		if id, err := uuid.Parse(ws); err == nil {
			rr.WorkspaceID = id
		}
	}
	e, err := s.Broker.Run(r.Context(), rr)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if e.Status != domain.StatusSucceeded {
		msg := "row fetch did not complete"
		if e.Error != nil {
			msg = e.Error.Message
		}
		writeErr(w, r, &domain.Error{Code: domain.CodeInternal, HTTP: http.StatusBadGateway, Message: msg})
		return
	}
	backingID, ok := resultExecID(e)
	if !ok {
		writeErr(w, r, domain.EGone("results expired", map[string]string{"re_run_hint": "retry the row fetch"}))
		return
	}
	page, err := s.Results.ReadPage(op.Tenant, backingID, results.Cursor{}, 1)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if len(page.Rows) == 0 {
		writeErr(w, r, domain.ENotFound())
		return
	}
	row := map[string]any{}
	for i, c := range page.Columns {
		if i < len(page.Rows[0]) {
			row[c.Name] = page.Rows[0][i]
		}
	}
	writeData(w, http.StatusOK, row)
}
