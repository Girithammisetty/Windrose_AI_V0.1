package api

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/csv"
	"net/http"
	"strconv"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/domain"
	"github.com/windrose-ai/case-service/internal/events"
	"github.com/windrose-ai/case-service/internal/search"
	"github.com/windrose-ai/case-service/internal/store"
)

type bulkReq struct {
	Operation string            `json:"operation"`
	CaseIDs   []string          `json:"case_ids"`
	Filter    map[string]string `json:"filter"`
	Params    map[string]any    `json:"params"`
}

type bulkFailure struct {
	ID      string `json:"id"`
	Code    string `json:"code"`
	Message string `json:"message"`
}

// handleBulk applies a bulk operation with partial-failure semantics
// (CASE-FR-030/031, AC-6/AC-7). Id-based (≤500) runs synchronously; filter-based
// resolves the filter to ids server-side (≤5,000) and runs async (202). A
// per-tenant concurrency gate caps 5 in-flight bulk ops (CASE-FR-032).
func (s *Server) handleBulk(w http.ResponseWriter, r *http.Request) {
	op, ok := opFrom(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("bad claims"))
		return
	}
	var req bulkReq
	if !decodeBody(w, r, &req) {
		return
	}
	if len(req.CaseIDs) > 500 {
		writeErr(w, r, domain.EBatchTooLarge("max 500 ids per bulk call"))
		return
	}
	// Filter-based async path (CASE-FR-030).
	if len(req.CaseIDs) == 0 {
		if len(req.Filter) == 0 {
			writeErr(w, r, domain.EValidation("either case_ids (≤500) or a filter is required", nil))
			return
		}
		s.handleBulkByFilter(w, r, op, req)
		return
	}
	release, ok := s.acquireBulkSlot(r.Context(), op.Tenant)
	if !ok {
		writeErr(w, r, &domain.Error{Code: domain.CodeRateLimited, HTTP: http.StatusTooManyRequests, Message: "too many concurrent bulk operations (max 5 per tenant)"})
		return
	}
	defer release()
	apply, err := s.bulkItemFunc(r.Context(), op, req)
	if err != nil {
		writeErr(w, r, err)
		return
	}

	var succeeded []string
	var failed []bulkFailure
	for _, raw := range req.CaseIDs {
		id, perr := uuid.Parse(raw)
		if perr != nil {
			failed = append(failed, bulkFailure{ID: raw, Code: domain.CodeNotFound, Message: "invalid id"})
			continue
		}
		if err := apply(id); err != nil {
			de := mapStoreErr(err)
			if d, ok := domain.AsError(err); ok {
				de = d
			}
			if de == nil {
				de = domain.EInternal(err.Error())
			}
			failed = append(failed, bulkFailure{ID: raw, Code: de.Code, Message: de.Message})
			continue
		}
		succeeded = append(succeeded, raw)
	}

	status := http.StatusOK
	if len(succeeded) == 0 {
		status = http.StatusUnprocessableEntity
	}
	writeJSON(w, status, map[string]any{"succeeded": succeeded, "failed": failed})
}

// bulkItemFunc returns a per-item apply function for the operation.
func (s *Server) bulkItemFunc(ctx context.Context, op domain.Op, req bulkReq) (func(uuid.UUID) error, error) {
	switch req.Operation {
	case "assign":
		raw, _ := req.Params["assignee_id"].(string)
		assignee, err := uuid.Parse(raw)
		if err != nil {
			return nil, domain.EValidation("params.assignee_id required", nil)
		}
		return func(id uuid.UUID) error {
			now := time.Now().UTC()
			_, err := s.Store.MutateCase(ctx, op, id, nil, func(c *domain.Case) (store.Mutation, error) {
				if err := c.Assign(assignee, now); err != nil {
					return store.Mutation{}, err
				}
				urn := events.CaseURN(op.Tenant, c.ID)
				policy, _ := s.Store.GetSLAPolicy(ctx, op.Tenant, c.WorkspaceID)
				return store.Mutation{
					Activities: []domain.Activity{mkActivity(op, events.EvAssigned, nil, map[string]any{"assignee": assignee.String()})},
					Events:     []events.Envelope{events.NewEnvelope(events.EvAssigned, op, urn, map[string]any{"case_number": c.CaseNumber, "assignee": assignee.String()})},
					Timers:     store.TimerPlan{Set: []store.Timer{{Kind: "warn", FireAt: c.DueDate.Add(-policy.WarnBefore)}, {Kind: "due", FireAt: c.DueDate}}},
				}, nil
			})
			return err
		}, nil
	case "unassign":
		return func(id uuid.UUID) error {
			_, err := s.Store.MutateCase(ctx, op, id, nil, func(c *domain.Case) (store.Mutation, error) {
				if err := c.Unassign(); err != nil {
					return store.Mutation{}, err
				}
				urn := events.CaseURN(op.Tenant, c.ID)
				return store.Mutation{
					Activities: []domain.Activity{mkActivity(op, events.EvUnassigned, nil, map[string]any{"reason": domain.ReasonManual})},
					Events:     []events.Envelope{events.NewEnvelope(events.EvUnassigned, op, urn, map[string]any{"case_number": c.CaseNumber, "reason": domain.ReasonManual})},
					Timers:     store.TimerPlan{Cancel: true},
				}, nil
			})
			return err
		}, nil
	case "set_severity":
		sev, _ := req.Params["severity"].(string)
		if !domain.ValidSeverity(sev) {
			return nil, domain.EValidation("params.severity invalid", nil)
		}
		return func(id uuid.UUID) error {
			_, err := s.Store.MutateCase(ctx, op, id, nil, func(c *domain.Case) (store.Mutation, error) {
				old := c.Severity
				c.Severity = sev
				urn := events.CaseURN(op.Tenant, c.ID)
				return store.Mutation{
					Activities: []domain.Activity{mkActivity(op, events.EvSeverityChanged, map[string]any{"severity": old}, map[string]any{"severity": sev})},
					Events:     []events.Envelope{events.NewEnvelope(events.EvSeverityChanged, op, urn, map[string]any{"case_number": c.CaseNumber, "severity": sev})},
				}, nil
			})
			return err
		}, nil
	case "resolve":
		raw, _ := req.Params["disposition_id"].(string)
		note, _ := req.Params["resolution_note"].(string)
		dispID, err := uuid.Parse(raw)
		if err != nil {
			return nil, domain.EDispositionRequired()
		}
		disp, err := s.Store.GetDisposition(ctx, op.Tenant, dispID)
		if err != nil {
			return nil, domain.EDispositionRequired()
		}
		return func(id uuid.UUID) error {
			_, err := s.Store.MutateCase(ctx, op, id, nil, func(c *domain.Case) (store.Mutation, error) {
				return s.resolveMutation(op, c, disp, note, "")
			})
			return err
		}, nil
	case "add_comment":
		body, _ := req.Params["body"].(string)
		if body == "" {
			return nil, domain.EValidation("params.body required", nil)
		}
		return func(id uuid.UUID) error {
			_, err := s.Store.AddComment(ctx, op, id, body)
			return err
		}, nil
	default:
		return nil, domain.EValidation("unknown bulk operation: "+req.Operation, nil)
	}
}

type exportReq struct {
	Filter map[string]string `json:"filter"`
	Format string            `json:"format"`
}

// handleExport starts an async CSV export (CASE-FR-044). Returns 202 with an
// operation id; the worker reads authoritative values from Postgres, writes a
// gzipped CSV to object storage, and records a time-limited download URL.
func (s *Server) handleExport(w http.ResponseWriter, r *http.Request) {
	op, ok := opFrom(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("bad claims"))
		return
	}
	ws, ok := workspaceFromClaims(r)
	if !ok {
		writeErr(w, r, domain.EValidation("workspace_id claim required", nil))
		return
	}
	var req exportReq
	if !decodeBody(w, r, &req) {
		return
	}
	release, ok := s.acquireBulkSlot(r.Context(), op.Tenant)
	if !ok {
		writeErr(w, r, &domain.Error{Code: domain.CodeRateLimited, HTTP: http.StatusTooManyRequests, Message: "too many concurrent bulk/export operations (max 5 per tenant)"})
		return
	}
	opID := domain.NewID()
	rec := &store.Operation{ID: opID, TenantID: op.Tenant, WorkspaceID: ws, Kind: "export", Status: "running", CreatedBy: op.Actor.ID}
	if err := s.Store.CreateOperation(r.Context(), rec); err != nil {
		release()
		writeErr(w, r, err)
		return
	}
	statuses := statusesFromFilter(req.Filter["status"])
	go func() {
		defer release()
		ctx := context.Background()
		cases, err := s.Store.ExportCases(ctx, op.Tenant, ws, statuses, 50000)
		if err != nil {
			_ = s.Store.UpdateOperation(ctx, op.Tenant, opID, "failed", 0, 0, map[string]any{"error": err.Error()})
			return
		}
		csvGz := gzipCSV(cases)
		ref, err := s.Snapshots.PutBytes(ctx, op.Tenant.String()+"/"+opID.String()+".csv.gz", csvGz)
		if err != nil {
			_ = s.Store.UpdateOperation(ctx, op.Tenant, opID, "failed", 0, 0, map[string]any{"error": err.Error()})
			return
		}
		_ = s.Store.UpdateOperation(ctx, op.Tenant, opID, "succeeded", len(cases), 0, map[string]any{
			"row_count":    len(cases),
			"object_ref":   ref,
			"download_url": "/api/v1/operations/" + opID.String() + "/download",
			"expires_at":   time.Now().Add(15 * time.Minute).UTC(),
		})
	}()
	writeData(w, http.StatusAccepted, map[string]any{"operation_id": opID})
}

// handleDownloadExport streams the gzipped CSV produced by an export operation
// (CASE-FR-044). Auth-gated and tenant-scoped via the operation lookup.
func (s *Server) handleDownloadExport(w http.ResponseWriter, r *http.Request) {
	c := ClaimsFrom(r.Context())
	tenant, _ := c.Tenant()
	id, err := uuid.Parse(chiURLParam(r, "id"))
	if err != nil {
		s.notFound(w, r)
		return
	}
	o, err := s.Store.GetOperation(r.Context(), tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	ref, _ := o.Result["object_ref"].(string)
	if o.Status != "succeeded" || ref == "" {
		writeErr(w, r, domain.EValidation("export not ready", nil))
		return
	}
	data, err := s.Snapshots.GetBytes(r.Context(), ref)
	if err != nil {
		s.notFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "application/gzip")
	w.Header().Set("Content-Disposition", "attachment; filename=\"cases-"+id.String()+".csv.gz\"")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(data)
}

// handleBulkByFilter resolves a filter to ids (≤5,000) via the real OpenSearch
// index, then runs the operation asynchronously (CASE-FR-030). Returns 202.
func (s *Server) handleBulkByFilter(w http.ResponseWriter, r *http.Request, op domain.Op, req bulkReq) {
	c := ClaimsFrom(r.Context())
	tenant, _ := c.Tenant()
	ws, _ := workspaceFromClaims(r)
	params := paramsFromFilterMap(req.Filter, c.EffectiveUser())
	ids, err := s.Search.CollectIDs(r.Context(), tenant, params, 5000)
	if err != nil {
		writeErr(w, r, domain.ESearchUnavailable())
		return
	}
	apply, err := s.bulkItemFunc(context.Background(), op, req)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	release, ok := s.acquireBulkSlot(r.Context(), op.Tenant)
	if !ok {
		writeErr(w, r, &domain.Error{Code: domain.CodeRateLimited, HTTP: http.StatusTooManyRequests, Message: "too many concurrent bulk operations (max 5 per tenant)"})
		return
	}
	opID := domain.NewID()
	rec := &store.Operation{ID: opID, TenantID: op.Tenant, WorkspaceID: ws, Kind: "bulk_" + req.Operation, Status: "running", Total: len(ids), CreatedBy: op.Actor.ID}
	if err := s.Store.CreateOperation(r.Context(), rec); err != nil {
		release()
		writeErr(w, r, err)
		return
	}
	go func() {
		defer release()
		ctx := context.Background()
		var succeeded, failed int
		for _, raw := range ids {
			id, perr := uuid.Parse(raw)
			if perr != nil {
				failed++
				continue
			}
			if err := apply(id); err != nil {
				failed++
				continue
			}
			succeeded++
		}
		_ = s.Store.UpdateOperation(ctx, op.Tenant, opID, "succeeded", succeeded, failed, map[string]any{"succeeded": succeeded, "failed": failed})
		// Emit case.bulk.completed for realtime-hub / audit (CASE-FR §6).
		env := events.NewEnvelope(events.EvBulkCompleted, op, "", map[string]any{
			"operation_id": opID.String(), "operation": req.Operation, "succeeded": succeeded, "failed": failed,
		})
		_ = s.Store.InsertAudit(ctx, env)
	}()
	writeData(w, http.StatusAccepted, map[string]any{"operation_id": opID, "total": len(ids)})
}

// acquireBulkSlot is the per-tenant bulk concurrency gate (CASE-FR-032: max 5
// concurrent bulk ops/tenant), backed by real Redis. Nil Redis (unit tests) or a
// Redis transport error fails open — the gate protects, it must not block work.
func (s *Server) acquireBulkSlot(ctx context.Context, tenant uuid.UUID) (func(), bool) {
	if s.Redis == nil {
		return func() {}, true
	}
	key := "case:bulk:concurrency:" + tenant.String()
	n, err := s.Redis.R.Incr(ctx, key).Result()
	if err != nil {
		return func() {}, true
	}
	_ = s.Redis.R.Expire(ctx, key, 2*time.Minute).Err()
	if n > 5 {
		s.Redis.R.Decr(ctx, key)
		return nil, false
	}
	return func() { s.Redis.R.Decr(ctx, key) }, true
}

// paramsFromFilterMap builds search params from a bulk/export filter map.
func paramsFromFilterMap(f map[string]string, effectiveUser string) search.Params {
	p := search.Params{
		Q:                   f["q"],
		Statuses:            search.ExpandStatus(f["status"]),
		Severity:            f["severity"],
		DispositionCategory: f["disposition_category"],
		QueryURN:            f["query_urn"],
		Due:                 f["due"],
	}
	if a := f["assignee"]; a == "me" {
		p.AssigneeID = effectiveUser
	} else if a != "" {
		p.AssigneeID = a
	}
	return p
}

// statusesFromFilter maps a filter[status] value to concrete domain statuses
// (export selection).
func statusesFromFilter(v string) []domain.Status {
	var out []domain.Status
	for _, name := range search.ExpandStatus(v) {
		if st, ok := domain.ParseStatus(name); ok {
			out = append(out, st)
		}
	}
	return out
}

// gzipCSV renders cases to a gzipped CSV with authoritative Postgres values
// (CASE-FR-044).
func gzipCSV(cases []*domain.Case) []byte {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	cw := csv.NewWriter(gz)
	_ = cw.Write([]string{"case_number", "status", "severity", "assigned_to_id", "dataset_urn", "row_pk",
		"disposition_id", "resolution_note", "due_date", "created_at", "case_version"})
	for _, c := range cases {
		assignee := ""
		if c.AssignedToID != nil {
			assignee = c.AssignedToID.String()
		}
		disp := ""
		if c.DispositionID != nil {
			disp = c.DispositionID.String()
		}
		_ = cw.Write([]string{
			strconv.FormatInt(c.CaseNumber, 10), c.Status.String(), c.Severity, assignee, c.DatasetURN, c.RowPK,
			disp, c.ResolutionNote, c.DueDate.Format(time.RFC3339), c.CreatedAt.Format(time.RFC3339), strconv.Itoa(c.CaseVersion),
		})
	}
	cw.Flush()
	_ = gz.Close()
	return buf.Bytes()
}

func (s *Server) handleGetOperation(w http.ResponseWriter, r *http.Request) {
	c := ClaimsFrom(r.Context())
	tenant, _ := c.Tenant()
	id, err := uuid.Parse(chiURLParam(r, "id"))
	if err != nil {
		s.notFound(w, r)
		return
	}
	o, err := s.Store.GetOperation(r.Context(), tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, map[string]any{
		"id": o.ID, "kind": o.Kind, "status": o.Status, "succeeded": o.Succeeded, "failed": o.Failed,
		"total": o.Total, "result": o.Result,
	})
}
