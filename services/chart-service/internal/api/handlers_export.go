package api

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/chart-service/internal/authz"
	"github.com/windrose-ai/chart-service/internal/domain"
	"github.com/windrose-ai/chart-service/internal/events"
	"github.com/windrose-ai/chart-service/internal/export"
)

const signedURLTTL = 15 * time.Minute

type exportReq struct {
	Format  string                `json:"format"`
	Width   int                   `json:"width"`
	Height  int                   `json:"height"`
	Theme   string                `json:"theme"`
	Request domain.ResolveRequest `json:"request"`
}

// handleExport starts an async CSV/PNG export (CHART-FR-041 / AC-11).
func (s *Server) handleExport(w http.ResponseWriter, r *http.Request) {
	_, tenant, ok := s.claims(w, r)
	if !ok {
		return
	}
	chart, err := s.loadChartAuthorized(w, r, tenant, authz.ActionChartExport)
	if err != nil {
		return
	}
	var in exportReq
	if !decodeBody(w, r, &in) {
		return
	}
	if in.Format != "csv" && in.Format != "png" {
		writeErr(w, r, domain.EValidation("format must be csv or png"))
		return
	}
	// Cap: 5 concurrent exports/tenant (CHART-FR-041).
	n, err := s.Store.ConcurrentExports(r.Context(), tenant)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if n >= 5 {
		writeErr(w, r, &domain.Error{Status: http.StatusTooManyRequests, Code: domain.CodeExportLimit, Message: "5 concurrent exports/tenant reached"})
		return
	}
	reqSnap, _ := json.Marshal(in.Request) // BR-13: snapshot filters/variables
	op := &domain.Operation{ID: newID(), ChartID: &chart.ID, Kind: "export", Format: in.Format,
		Request: reqSnap, CreatedBy: subject(r)}
	if err := s.Store.CreateOperation(r.Context(), op, tenant); err != nil {
		writeErr(w, r, err)
		return
	}
	// Run in the background against a detached context (survives the request).
	token := bearerToken(r)
	go s.runExport(tenant, op.ID, chart, in, token)

	writeData(w, http.StatusAccepted, map[string]any{"operation_id": op.ID.String()})
}

// runExport resolves data and writes the artifact. CSV is fully real; PNG is
// infra-gated on the headless renderer sidecar.
func (s *Server) runExport(tenant, opID uuid.UUID, chart *domain.Chart, in exportReq, token string) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	_ = s.Store.UpdateOperation(ctx, tenant, opID, "running", "", "", "", nil)

	if in.Format == "png" && s.PNGRenderer == "" {
		_ = s.Store.UpdateOperation(ctx, tenant, opID, "failed", "", "", "PNG_RENDERER_UNAVAILABLE: headless renderer sidecar not configured", nil)
		return
	}

	// CSV: resolve full un-truncated data (raw mode, high internal page).
	req := in.Request
	agg := req.AggregatedDefault()
	req.Aggregated = &agg
	req.Limit = 1_000_000
	res, err := s.Resolver.Resolve(ctx, token, chart, req)
	if err != nil {
		_ = s.Store.UpdateOperation(ctx, tenant, opID, "failed", "", "", err.Error(), nil)
		return
	}
	if in.Format == "png" {
		// A configured renderer would receive the shaped data + dimensions here.
		_ = s.Store.UpdateOperation(ctx, tenant, opID, "failed", "", "", "PNG rendering not implemented in this build (renderer sidecar contract documented)", nil)
		return
	}
	data, err := export.WriteCSV(res.Columns, res.Rows)
	if err != nil {
		_ = s.Store.UpdateOperation(ctx, tenant, opID, "failed", "", "", err.Error(), nil)
		return
	}
	key := tenant.String() + "/" + opID.String() + ".csv"
	url, expires, err := s.Exports.Put(ctx, key, data, signedURLTTL)
	if err != nil {
		_ = s.Store.UpdateOperation(ctx, tenant, opID, "failed", "", "", err.Error(), nil)
		return
	}
	urn := events.URN(tenant, "operation", opID.String())
	_ = s.Store.UpdateOperation(ctx, tenant, opID, "completed", url, urn, "", &expires)
}

// handleGetOperation returns operation status/artifact (CHART-FR-041).
func (s *Server) handleGetOperation(w http.ResponseWriter, r *http.Request) {
	_, tenant, ok := s.claims(w, r)
	if !ok {
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErr(w, r, domain.ENotFound("operation not found"))
		return
	}
	op, err := s.Store.GetOperation(r.Context(), tenant, id)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, op)
}

// handleDownloadExport serves a signed artifact (HMAC-validated, not JWT).
func (s *Server) handleDownloadExport(w http.ResponseWriter, r *http.Request) {
	key := strings.TrimPrefix(chi.URLParam(r, "*"), "/")
	q := r.URL.Query()
	exp, _ := strconv.ParseInt(q.Get("exp"), 10, 64)
	data, err := s.Exports.Read(key, exp, q.Get("sig"))
	if err != nil {
		writeErr(w, r, domain.ENotFound("artifact not found or link expired"))
		return
	}
	w.Header().Set("Content-Type", "text/csv; charset=utf-8")
	w.Header().Set("Content-Disposition", "attachment")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(data)
}
