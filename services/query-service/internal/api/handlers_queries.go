package api

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/query-service/internal/domain"
	"github.com/windrose-ai/query-service/internal/events"
	"github.com/windrose-ai/query-service/internal/exec"
	"github.com/windrose-ai/query-service/internal/sqlsafe"
	"github.com/windrose-ai/query-service/internal/store"
)

type savedQueryReq struct {
	Name        *string               `json:"name"`
	Description *string               `json:"description"`
	WorkspaceID *uuid.UUID            `json:"workspace_id"`
	SQLText     *string               `json:"sql_text"`
	Variables   []domain.VariableDecl `json:"variables"`
	Tags        []string              `json:"tags"`
	ModuleNames []string              `json:"module_names"`
}

// validateQueryDefinition enforces the save-time rules (QRY-FR-001/002/004):
// typed declarations only, every :placeholder declared, legacy {var}
// rejected (the scanner errors), statement must classify as read-only even
// at save time (AC-3 "run or saved").
func validateQueryDefinition(sqlText string, decls []domain.VariableDecl) error {
	if strings.TrimSpace(sqlText) == "" {
		return domain.EValidation("sql_text is required")
	}
	if err := domain.ValidateDecls(decls); err != nil {
		return err
	}
	names, err := sqlsafe.PlaceholderNames(sqlText)
	if err != nil {
		return err
	}
	declared := map[string]bool{}
	for _, d := range decls {
		declared[d.Name] = true
	}
	var problems []domain.VariableProblem
	for _, n := range names {
		if !declared[n] {
			// V1 failed at run time with "param missing" for the first var
			// only; here EVERY undeclared placeholder fails at save time.
			problems = append(problems, domain.VariableProblem{Name: n, Problem: "placeholder has no declaration"})
		}
	}
	if len(problems) > 0 {
		return domain.EVariableInvalid(problems)
	}
	// Save-time classification with inert dummy bindings (no value ever
	// enters the SQL text; dummies are nil parameters).
	dummy := map[string]domain.BoundValue{}
	for _, d := range decls {
		bv := domain.BoundValue{Name: d.Name, Type: d.Type}
		if d.Type == domain.VarStringList || d.Type == domain.VarIntegerList {
			bv.IsList = true
			bv.List = []any{nil}
		}
		dummy[d.Name] = bv
	}
	refs, err := sqlsafe.DatasetRefs(sqlText)
	if err != nil {
		return err
	}
	idents := map[string]string{}
	for i, ref := range refs {
		idents[fmt.Sprintf("%s@%d", ref.Name, ref.Version)] = fmt.Sprintf(`"_wr_save"."d%d"`, i)
	}
	rw, err := sqlsafe.Rewrite(sqlText, dummy, idents)
	if err != nil {
		return err
	}
	if _, err := sqlsafe.Classify(rw.SQL); err != nil {
		return err
	}
	return nil
}

// resolveRefs resolves save-time dataset refs (QRY-FR-001 dataset_refs
// resolved; QRY-FR-005 unresolved → 422).
func (s *Server) resolveRefs(ctx context.Context, tenant uuid.UUID, sqlText string) ([]domain.DatasetRef, error) {
	refs, err := sqlsafe.DatasetRefs(sqlText)
	if err != nil {
		return nil, err
	}
	out := make([]domain.DatasetRef, 0, len(refs))
	for _, ref := range refs {
		meta, err := s.Broker.Resolver.Resolve(ctx, tenant, ref.Name, ref.Version)
		if err != nil {
			return nil, err
		}
		out = append(out, domain.DatasetRef{Name: ref.Name, Version: ref.Version, URN: meta.URN})
	}
	return out, nil
}

func queryResource(q *domain.SavedQuery, v *domain.SavedQueryVersion) map[string]any {
	res := map[string]any{
		"id":                 q.ID,
		"workspace_id":       q.WorkspaceID,
		"name":               q.Name,
		"description":        q.Description,
		"current_version_no": q.CurrentVersionNo,
		"tags":               emptyIfNil(q.Tags),
		"module_names":       q.ModuleNames,
		"created_by":         q.CreatedBy,
		"created_at":         q.CreatedAt,
		"updated_at":         q.UpdatedAt,
	}
	if v != nil {
		res["sql_text"] = v.SQLText
		res["variables"] = v.Variables
		res["dataset_refs"] = v.DatasetRefs
		res["version_no"] = v.VersionNo
	}
	return res
}

func emptyIfNil(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}

func etagFor(q *domain.SavedQuery) string { return fmt.Sprintf(`"v%d"`, q.CurrentVersionNo) }

func (s *Server) handleCreateQuery(w http.ResponseWriter, r *http.Request) {
	op, ok := opFrom(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("invalid claims"))
		return
	}
	var req savedQueryReq
	if !decodeBody(w, r, &req) {
		return
	}
	if req.Name == nil || strings.TrimSpace(*req.Name) == "" {
		writeErr(w, r, domain.EValidation("name is required"))
		return
	}
	if len(req.ModuleNames) < 1 {
		// V1 SavedQuery rule preserved (QRY-FR-001).
		writeErr(w, r, domain.EValidation("module_names must contain at least one module"))
		return
	}
	sqlText := ""
	if req.SQLText != nil {
		sqlText = *req.SQLText
	}
	if err := validateQueryDefinition(sqlText, req.Variables); err != nil {
		writeErr(w, r, err)
		return
	}
	refs, err := s.resolveRefs(r.Context(), op.Tenant, sqlText)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	now := time.Now().UTC()
	q := &domain.SavedQuery{
		ID: domain.NewID(), TenantID: op.Tenant, Name: strings.TrimSpace(*req.Name),
		CurrentVersionNo: 1, Tags: emptyIfNil(req.Tags), ModuleNames: req.ModuleNames,
		CreatedBy: op.UserID, CreatedAt: now, UpdatedAt: now,
	}
	if req.Description != nil {
		q.Description = *req.Description
	}
	if req.WorkspaceID != nil {
		q.WorkspaceID = *req.WorkspaceID
	}
	v := &domain.SavedQueryVersion{
		ID: domain.NewID(), TenantID: op.Tenant, SavedQueryID: q.ID, VersionNo: 1,
		SQLText: sqlText, Variables: req.Variables, DatasetRefs: refs,
		CreatedBy: op.UserID, CreatedAt: now,
	}
	env := events.NewEnvelope(events.EvQuerySaved, op, events.QueryURN(op.Tenant, q.ID),
		map[string]any{"name": q.Name, "version_no": 1})
	if err := s.Store.CreateSavedQuery(r.Context(), op, q, v, []events.Envelope{env}); err != nil {
		writeErr(w, r, storeErr(err))
		return
	}
	w.Header().Set("ETag", etagFor(q))
	writeData(w, http.StatusCreated, queryResource(q, v))
}

func (s *Server) handleListQueries(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	f := store.SavedQueryFilter{Cursor: r.URL.Query().Get("cursor")}
	f.Limit, _ = strconv.Atoi(r.URL.Query().Get("limit"))
	if ws := r.URL.Query().Get("filter[workspace_id]"); ws != "" {
		id, err := uuid.Parse(ws)
		if err != nil {
			writeErr(w, r, domain.EValidation("invalid filter[workspace_id]"))
			return
		}
		f.WorkspaceID = &id
	}
	page, err := s.Store.ListSavedQueries(r.Context(), op.Tenant, f)
	if err != nil {
		writeErr(w, r, storeErr(err))
		return
	}
	writePage(w, page)
}

func (s *Server) parseID(w http.ResponseWriter, r *http.Request) (uuid.UUID, bool) {
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		s.notFound(w, r) // malformed ids are indistinguishable from absent
		return uuid.Nil, false
	}
	return id, true
}

func (s *Server) handleGetQuery(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := s.parseID(w, r)
	if !ok {
		return
	}
	q, v, err := s.Store.GetSavedQuery(r.Context(), op.Tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	w.Header().Set("ETag", etagFor(q))
	writeData(w, http.StatusOK, queryResource(q, v))
}

func (s *Server) handlePatchQuery(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := s.parseID(w, r)
	if !ok {
		return
	}
	var req savedQueryReq
	if !decodeBody(w, r, &req) {
		return
	}
	q, cur, err := s.Store.GetSavedQuery(r.Context(), op.Tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	expect := q.CurrentVersionNo
	if im := r.Header.Get("If-Match"); im != "" {
		var n int
		if _, err := fmt.Sscanf(strings.Trim(im, `"`), "v%d", &n); err != nil {
			writeErr(w, r, domain.EConflict("invalid If-Match etag"))
			return
		}
		expect = n
	}
	if req.Name != nil {
		if strings.TrimSpace(*req.Name) == "" {
			writeErr(w, r, domain.EValidation("name cannot be empty"))
			return
		}
		q.Name = strings.TrimSpace(*req.Name)
	}
	if req.Description != nil {
		q.Description = *req.Description
	}
	if req.Tags != nil {
		q.Tags = req.Tags
	}
	if req.ModuleNames != nil {
		if len(req.ModuleNames) < 1 {
			writeErr(w, r, domain.EValidation("module_names must contain at least one module"))
			return
		}
		q.ModuleNames = req.ModuleNames
	}
	sqlText := cur.SQLText
	decls := cur.Variables
	if req.SQLText != nil {
		sqlText = *req.SQLText
	}
	if req.Variables != nil {
		decls = req.Variables
	}
	if err := validateQueryDefinition(sqlText, decls); err != nil {
		writeErr(w, r, err)
		return
	}
	refs, err := s.resolveRefs(r.Context(), op.Tenant, sqlText)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	now := time.Now().UTC()
	// Every update creates an immutable version (QRY-FR-001); numbers never
	// fork (BR-11: expectVersion + advisory lock in the store).
	q.CurrentVersionNo = expect + 1
	q.UpdatedAt = now
	v := &domain.SavedQueryVersion{
		ID: domain.NewID(), TenantID: op.Tenant, SavedQueryID: q.ID, VersionNo: q.CurrentVersionNo,
		SQLText: sqlText, Variables: decls, DatasetRefs: refs, CreatedBy: op.UserID, CreatedAt: now,
	}
	env := events.NewEnvelope(events.EvQueryUpdated, op, events.QueryURN(op.Tenant, q.ID),
		map[string]any{"name": q.Name, "version_no": q.CurrentVersionNo})
	if err := s.Store.UpdateSavedQuery(r.Context(), op, q, v, expect, []events.Envelope{env}); err != nil {
		writeErr(w, r, storeErr(err))
		return
	}
	w.Header().Set("ETag", etagFor(q))
	writeData(w, http.StatusOK, queryResource(q, v))
}

func (s *Server) handleDeleteQuery(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := s.parseID(w, r)
	if !ok {
		return
	}
	env := events.NewEnvelope(events.EvQueryDeleted, op, events.QueryURN(op.Tenant, id), nil)
	if err := s.Store.SoftDeleteSavedQuery(r.Context(), op, id, []events.Envelope{env}); err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleListVersions(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := s.parseID(w, r)
	if !ok {
		return
	}
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	page, err := s.Store.ListVersions(r.Context(), op.Tenant, id, limit, r.URL.Query().Get("cursor"))
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	writePage(w, page)
}

// ---- Run a saved query (QRY-FR-043) -----------------------------------------

type runBody struct {
	Variables    map[string]json.RawMessage `json:"variables"`
	Mode         string                     `json:"mode"`
	EngineHint   string                     `json:"engine_hint"`
	Limit        int64                      `json:"limit"`
	Cache        *bool                      `json:"cache"`
	QueryVersion *int                       `json:"query_version"`
}

func (s *Server) handleRunSavedQuery(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := s.parseID(w, r)
	if !ok {
		return
	}
	var body runBody
	if !decodeBody(w, r, &body) {
		return
	}
	applyRunParams(&body, r)
	q, v, err := s.Store.GetSavedQuery(r.Context(), op.Tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	if body.QueryVersion != nil && *body.QueryVersion != v.VersionNo {
		v, err = s.Store.GetVersion(r.Context(), op.Tenant, id, *body.QueryVersion)
		if err != nil {
			s.writeLookupErr(w, r, err)
			return
		}
	}
	req := exec.RunRequest{
		PlanRequest: exec.PlanRequest{
			Op: op, SQLText: v.SQLText, Decls: v.Variables, Values: body.Variables,
			EngineHint: body.EngineHint, Limit: body.Limit, Async: body.Mode != "sync",
			SavedRefs: v.DatasetRefs,
		},
		WorkspaceID: q.WorkspaceID, SavedQueryID: &q.ID, VersionNo: &v.VersionNo,
		Mode: body.Mode, UseCache: body.Cache == nil || *body.Cache,
	}
	s.runAndRespond(w, r, req)
}

func applyRunParams(body *runBody, r *http.Request) {
	if m := r.URL.Query().Get("mode"); m != "" && body.Mode == "" {
		body.Mode = m
	}
	if qv := r.URL.Query().Get("query_version"); qv != "" && body.QueryVersion == nil {
		if n, err := strconv.Atoi(qv); err == nil {
			body.QueryVersion = &n
		}
	}
	if c := r.URL.Query().Get("cache"); c == "false" {
		f := false
		body.Cache = &f
	}
}

// runAndRespond submits to the broker and shapes the API response
// (202 async / 200 sync per QRY-FR-043).
func (s *Server) runAndRespond(w http.ResponseWriter, r *http.Request, req exec.RunRequest) {
	if req.Mode != "" && req.Mode != "sync" && req.Mode != "async" {
		writeErr(w, r, domain.EValidation("mode must be sync or async"))
		return
	}
	e, err := s.Broker.Run(r.Context(), req)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	res := executionResource(e, s.Broker)
	if req.Mode == "sync" {
		writeData(w, http.StatusOK, res)
		return
	}
	writeData(w, http.StatusAccepted, res)
}
