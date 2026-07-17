package api

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/query-service/internal/domain"
	"github.com/windrose-ai/query-service/internal/exec"
	"github.com/windrose-ai/query-service/internal/results"
	"github.com/windrose-ai/query-service/internal/store"
)

// executionResource shapes an execution for the API (QRY-FR-080).
func executionResource(e *domain.Execution, broker *exec.Broker) map[string]any {
	res := map[string]any{
		"execution_id": e.ID,
		"id":           e.ID,
		"status":       e.Status,
		"caller_class": e.CallerClass,
		"engine":       e.Engine,
		"cache_hit":    e.CacheHit,
		"created_by":   e.CreatedBy,
		"created_at":   e.CreatedAt,
		"trace_id":     e.TraceID,
		"plan": map[string]any{
			"engine":               e.Engine,
			"estimated_scan_bytes": e.EstimatedScanBytes,
			"ceiling_verdict":      "ok",
		},
		"stats": map[string]any{
			"actual_scan_bytes": e.ActualScanBytes,
			"result_rows":       e.ResultRows,
			"result_bytes":      e.ResultBytes,
			"duration_ms":       e.DurationMS,
		},
	}
	if e.SavedQueryID != nil {
		res["saved_query_id"] = e.SavedQueryID
	}
	if e.QueryVersionNo != nil {
		res["query_version_no"] = e.QueryVersionNo
	}
	if e.RoutingReason != nil {
		res["routing_reason"] = e.RoutingReason
	}
	if e.Ceilings != nil {
		res["ceilings"] = e.Ceilings
	}
	if len(e.Warnings) > 0 {
		res["warnings"] = e.Warnings
	}
	if e.Error != nil {
		res["error"] = e.Error
	}
	if e.BoundParams != nil {
		res["bound_params"] = e.BoundParams
	}
	if e.StartedAt != nil {
		res["started_at"] = e.StartedAt
	}
	if e.FinishedAt != nil {
		res["finished_at"] = e.FinishedAt
	}
	if e.Status == domain.StatusQueued {
		pos := 0
		if broker != nil {
			pos = broker.QueuePosition(e.TenantID, e.ID)
		}
		if pos == 0 && e.QueuePosition != nil {
			pos = *e.QueuePosition
		}
		res["queue_position"] = pos
	}
	return res
}

func (s *Server) handleListExecutions(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	q := r.URL.Query()
	f := store.ExecutionFilter{
		Status: q.Get("status"),
		User:   q.Get("user"),
		Cursor: q.Get("cursor"),
	}
	f.Limit, _ = strconv.Atoi(q.Get("limit"))
	if sq := q.Get("saved_query_id"); sq != "" {
		id, err := uuid.Parse(sq)
		if err != nil {
			writeErr(w, r, domain.EValidation("invalid saved_query_id"))
			return
		}
		f.SavedQueryID = &id
	}
	if since := q.Get("since"); since != "" {
		t, err := time.Parse(time.RFC3339, since)
		if err != nil {
			writeErr(w, r, domain.EValidation("invalid since (RFC3339 required)"))
			return
		}
		f.Since = &t
	}
	if sort := q.Get("sort"); sort == "-cost" {
		f.SortByCost = true
	}
	page, err := s.Store.ListExecutions(r.Context(), op.Tenant, f)
	if err != nil {
		writeErr(w, r, storeErr(err))
		return
	}
	out := store.Page[map[string]any]{NextCursor: page.NextCursor, HasMore: page.HasMore}
	for _, e := range page.Data {
		out.Data = append(out.Data, executionResource(e, s.Broker))
	}
	writePage(w, out)
}

func (s *Server) handleGetExecution(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := s.parseID(w, r)
	if !ok {
		return
	}
	e, err := s.Store.GetExecution(r.Context(), op.Tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	res := executionResource(e, s.Broker)
	res["sql_text"] = e.SQLText
	writeData(w, http.StatusOK, res)
}

// resultExecID extracts the backing execution id from a result URI —
// cache hits point at the original execution's results (QRY-FR-046).
func resultExecID(e *domain.Execution) (uuid.UUID, bool) {
	if e.ResultURI == "" {
		return uuid.Nil, false
	}
	parts := strings.Split(e.ResultURI, "/")
	id, err := uuid.Parse(parts[len(parts)-1])
	if err != nil {
		return uuid.Nil, false
	}
	return id, true
}

func (s *Server) handleResults(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := s.parseID(w, r)
	if !ok {
		return
	}
	e, err := s.Store.GetExecution(r.Context(), op.Tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	if e.Status != domain.StatusSucceeded {
		writeErr(w, r, domain.EConflict("results not ready: execution is "+e.Status))
		return
	}
	if accept := r.Header.Get("Accept"); strings.Contains(accept, "application/vnd.apache.arrow.stream") {
		// Arrow internal transport stub (QRY-FR-061; deviation in README).
		writeErr(w, r, domain.ENotImplemented("arrow stream transport pending; use paginated JSON"))
		return
	}
	backingID, ok := resultExecID(e)
	if !ok {
		writeErr(w, r, domain.EGone("results expired", map[string]string{"re_run_hint": "re-run the query to regenerate results"}))
		return
	}
	limit := 1000
	if l := r.URL.Query().Get("limit"); l != "" {
		n, err := strconv.Atoi(l)
		if err != nil || n <= 0 || n > 10000 { // QRY-FR-061: limit ≤ 10 000
			writeErr(w, r, domain.EValidation("limit must be in [1, 10000]"))
			return
		}
		limit = n
	}
	cursor, err := results.DecodeCursor(r.URL.Query().Get("cursor"))
	if err != nil {
		writeErr(w, r, domain.EValidation("invalid cursor"))
		return
	}
	page, err := s.Results.ReadPage(op.Tenant, backingID, cursor, limit)
	if err != nil {
		if errors.Is(err, results.ErrGone) {
			// BR-9: after GC → 410 with re_run_hint; history row persists.
			writeErr(w, r, domain.EGone("results expired", map[string]string{"re_run_hint": "re-run the query to regenerate results"}))
			return
		}
		writeErr(w, r, err)
		return
	}
	rows := page.Rows
	if rows == nil {
		rows = [][]any{}
	}
	writeData(w, http.StatusOK, map[string]any{
		"columns": page.Columns,
		"rows":    rows,
		"page":    PageInfo{NextCursor: page.NextCursor, HasMore: page.HasMore},
		"stats": map[string]any{
			"result_rows":       e.ResultRows,
			"actual_scan_bytes": e.ActualScanBytes,
			"duration_ms":       e.DurationMS,
			"engine":            e.Engine,
			"cache_hit":         e.CacheHit,
		},
		"warnings": page.Warnings,
	})
}

func (s *Server) handleCancel(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := s.parseID(w, r)
	if !ok {
		return
	}
	e, err := s.Broker.Cancel(r.Context(), op, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, executionResource(e, s.Broker))
}

// ---- Export (QRY-FR-062) ----------------------------------------------------

type exportReq struct {
	Format string `json:"format"`
}

type downloadToken struct {
	Tenant  uuid.UUID `json:"t"`
	ExecID  uuid.UUID `json:"e"`
	Expires int64     `json:"x"`
}

func (s *Server) signToken(t downloadToken) string {
	payload, _ := json.Marshal(t)
	mac := hmac.New(sha256.New, s.ExportSecret)
	mac.Write(payload)
	return base64.RawURLEncoding.EncodeToString(payload) + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

func (s *Server) verifyToken(token string) (*downloadToken, bool) {
	parts := strings.SplitN(token, ".", 2)
	if len(parts) != 2 {
		return nil, false
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil {
		return nil, false
	}
	sig, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return nil, false
	}
	mac := hmac.New(sha256.New, s.ExportSecret)
	mac.Write(payload)
	if !hmac.Equal(sig, mac.Sum(nil)) {
		return nil, false
	}
	var t downloadToken
	if err := json.Unmarshal(payload, &t); err != nil {
		return nil, false
	}
	if time.Now().Unix() > t.Expires {
		return nil, false
	}
	return &t, true
}

func (s *Server) handleExport(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := s.parseID(w, r)
	if !ok {
		return
	}
	var req exportReq
	if !decodeBody(w, r, &req) {
		return
	}
	e, err := s.Store.GetExecution(r.Context(), op.Tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	if e.Status != domain.StatusSucceeded {
		writeErr(w, r, domain.EConflict("results not ready: execution is "+e.Status))
		return
	}
	switch req.Format {
	case "csv":
	case "parquet":
		// Should-tier stub (CONVENTIONS definition of done).
		// TODO(QRY-FR-062): parquet export via Arrow writer.
		writeErr(w, r, domain.ENotImplemented("parquet export pending; use csv"))
		return
	default:
		writeErr(w, r, domain.EValidation("format must be csv or parquet"))
		return
	}
	backingID, ok := resultExecID(e)
	if !ok {
		writeErr(w, r, domain.EGone("results expired", map[string]string{"re_run_hint": "re-run the query"}))
		return
	}
	if _, err := s.Results.Manifest(op.Tenant, backingID); err != nil {
		writeErr(w, r, domain.EGone("results expired", map[string]string{"re_run_hint": "re-run the query"}))
		return
	}
	expires := time.Now().Add(24 * time.Hour) // V1 download parity
	token := s.signToken(downloadToken{Tenant: op.Tenant, ExecID: backingID, Expires: expires.Unix()})
	writeData(w, http.StatusCreated, map[string]any{
		"format":     "csv",
		"url":        "/api/v1/downloads/" + token,
		"expires_at": expires.UTC().Format(time.RFC3339),
	})
}

// handleDownload serves a signed export link (pre-authenticated by
// signature, QRY-FR-062).
func (s *Server) handleDownload(w http.ResponseWriter, r *http.Request) {
	t, ok := s.verifyToken(chi.URLParam(r, "token"))
	if !ok {
		writeErr(w, r, domain.EGone("download link expired or invalid", nil))
		return
	}
	path, err := s.Results.ExportCSV(t.Tenant, t.ExecID)
	if err != nil {
		writeErr(w, r, domain.EGone("results expired", map[string]string{"re_run_hint": "re-run the query"}))
		return
	}
	w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=%q", filepath.Base(path)))
	w.Header().Set("Content-Type", "text/csv")
	http.ServeFile(w, r, path)
}
