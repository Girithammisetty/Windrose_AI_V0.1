package api

import (
	"net/http"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/domain"
)

// ---- Dispositions (CASE-FR-020) ---------------------------------------------

type dispositionReq struct {
	Code         string `json:"code"`
	Label        string `json:"label"`
	Category     string `json:"category"`
	RequiresNote bool   `json:"requires_note"`
	Active       *bool  `json:"active"`
}

func (s *Server) handleCreateDisposition(w http.ResponseWriter, r *http.Request) {
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
	var req dispositionReq
	if !decodeBody(w, r, &req) {
		return
	}
	if req.Code == "" || req.Label == "" || !validCategory(req.Category) {
		writeErr(w, r, domain.EValidation("code, label and a valid category are required", nil))
		return
	}
	active := true
	if req.Active != nil {
		active = *req.Active
	}
	d := &domain.Disposition{ID: domain.NewID(), TenantID: op.Tenant, WorkspaceID: ws, Code: req.Code, Label: req.Label,
		Category: req.Category, RequiresNote: req.RequiresNote, Active: active}
	if err := s.Store.CreateDisposition(r.Context(), d); err != nil {
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusCreated, d)
}

func (s *Server) handleUpdateDisposition(w http.ResponseWriter, r *http.Request) {
	c := ClaimsFrom(r.Context())
	tenant, _ := c.Tenant()
	id, err := uuid.Parse(chiURLParam(r, "id"))
	if err != nil {
		s.notFound(w, r)
		return
	}
	d, err := s.Store.GetDisposition(r.Context(), tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	var req dispositionReq
	if !decodeBody(w, r, &req) {
		return
	}
	if req.Label != "" {
		d.Label = req.Label
	}
	if validCategory(req.Category) {
		d.Category = req.Category
	}
	d.RequiresNote = req.RequiresNote
	if req.Active != nil {
		d.Active = *req.Active
	}
	if err := s.Store.UpdateDisposition(r.Context(), d); err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, d)
}

func (s *Server) handleListDispositions(w http.ResponseWriter, r *http.Request) {
	c := ClaimsFrom(r.Context())
	tenant, _ := c.Tenant()
	ws, ok := workspaceFromClaims(r)
	if !ok {
		writeErr(w, r, domain.EValidation("workspace_id claim required", nil))
		return
	}
	ds, err := s.Store.ListDispositions(r.Context(), tenant, ws)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	out := make([]any, 0, len(ds))
	for _, d := range ds {
		out = append(out, d)
	}
	writeJSON(w, http.StatusOK, PageEnvelope{Data: out, Page: PageInfo{}})
}

func validCategory(c string) bool {
	switch c {
	case domain.CatTruePositive, domain.CatFalsePositive, domain.CatBenign, domain.CatInconclusive, domain.CatOther:
		return true
	}
	return false
}

// ---- Custom fields (CASE-FR-022) --------------------------------------------

type fieldReq struct {
	QueryURN  string         `json:"query_urn"`
	Name      string         `json:"name"`
	DataType  string         `json:"data_type"`
	Purpose   string         `json:"purpose"` // create | update | both
	FieldMeta map[string]any `json:"field_meta"`
}

func (s *Server) handleCreateField(w http.ResponseWriter, r *http.Request) {
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
	var req fieldReq
	if !decodeBody(w, r, &req) {
		return
	}
	if req.Name == "" || !validDataType(req.DataType) {
		writeErr(w, r, domain.EValidation("name and a valid data_type are required", nil))
		return
	}
	f := &domain.CaseField{ID: domain.NewID(), TenantID: op.Tenant, WorkspaceID: ws, QueryURN: req.QueryURN,
		Name: req.Name, DataType: req.DataType, Purpose: parsePurpose(req.Purpose), FieldMeta: nonNilMap(req.FieldMeta)}
	if err := s.Store.CreateField(r.Context(), f); err != nil {
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusCreated, f)
}

func (s *Server) handleListFields(w http.ResponseWriter, r *http.Request) {
	c := ClaimsFrom(r.Context())
	tenant, _ := c.Tenant()
	ws, ok := workspaceFromClaims(r)
	if !ok {
		writeErr(w, r, domain.EValidation("workspace_id claim required", nil))
		return
	}
	fs, err := s.Store.ListFields(r.Context(), tenant, ws, r.URL.Query().Get("query_urn"), nil)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	out := make([]any, 0, len(fs))
	for _, f := range fs {
		out = append(out, f)
	}
	writeJSON(w, http.StatusOK, PageEnvelope{Data: out, Page: PageInfo{}})
}

func (s *Server) handleUpdateField(w http.ResponseWriter, r *http.Request) {
	c := ClaimsFrom(r.Context())
	tenant, _ := c.Tenant()
	id, err := uuid.Parse(chiURLParam(r, "id"))
	if err != nil {
		s.notFound(w, r)
		return
	}
	f, err := s.Store.GetField(r.Context(), tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	var req fieldReq
	if !decodeBody(w, r, &req) {
		return
	}
	// The field key (name), data_type and query_urn scope are immutable; only
	// purpose + field_meta (display label/options/required config) are editable.
	if req.Name != "" && req.Name != f.Name {
		writeErr(w, r, domain.EValidation("name is immutable", nil))
		return
	}
	if req.DataType != "" && req.DataType != f.DataType {
		writeErr(w, r, domain.EValidation("data_type is immutable", nil))
		return
	}
	if req.QueryURN != "" && req.QueryURN != f.QueryURN {
		writeErr(w, r, domain.EValidation("query_urn is immutable", nil))
		return
	}
	if req.Purpose != "" {
		f.Purpose = parsePurpose(req.Purpose)
	}
	if req.FieldMeta != nil {
		f.FieldMeta = nonNilMap(req.FieldMeta)
	}
	if err := s.Store.UpdateField(r.Context(), f); err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, f)
}

func (s *Server) handleDeleteField(w http.ResponseWriter, r *http.Request) {
	c := ClaimsFrom(r.Context())
	tenant, _ := c.Tenant()
	id, err := uuid.Parse(chiURLParam(r, "id"))
	if err != nil {
		s.notFound(w, r)
		return
	}
	orphan := r.URL.Query().Get("orphan") == "true"
	if err := s.Store.DeleteField(r.Context(), tenant, id, orphan); err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// handleForm returns default + custom fields for the create/update form
// (CASE-FR-022, AC-12).
func (s *Server) handleForm(w http.ResponseWriter, r *http.Request) {
	c := ClaimsFrom(r.Context())
	tenant, _ := c.Tenant()
	ws, ok := workspaceFromClaims(r)
	if !ok {
		writeErr(w, r, domain.EValidation("workspace_id claim required", nil))
		return
	}
	mode := r.URL.Query().Get("mode")
	if mode == "" {
		mode = "create"
	}
	queryURN := r.URL.Query().Get("query_urn")
	var purposes []int16
	if mode == "create" {
		purposes = []int16{domain.PurposeCreate}
	} else {
		purposes = []int16{domain.PurposeUpdate}
	}
	custom, err := s.Store.ListFields(r.Context(), tenant, ws, queryURN, purposes)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	defaults := defaultFormFields(mode)
	customOut := make([]any, 0, len(custom))
	for _, f := range custom {
		customOut = append(customOut, map[string]any{
			"name": f.Name, "data_type": f.DataType, "field_meta": f.FieldMeta, "custom": true, "query_scoped": f.QueryURN != "",
		})
	}
	writeData(w, http.StatusOK, map[string]any{"mode": mode, "defaults": defaults, "custom_fields": customOut})
}

func defaultFormFields(mode string) []map[string]any {
	base := []map[string]any{
		{"name": "assignee", "data_type": "string", "required": true},
		{"name": "due_date", "data_type": "date", "required": true},
		{"name": "description", "data_type": "text", "required": false},
		{"name": "severity", "data_type": "enum", "required": false},
	}
	if mode == "update" {
		base = append(base,
			map[string]any{"name": "case_number", "data_type": "integer", "readonly": true},
			map[string]any{"name": "status", "data_type": "string", "readonly": true},
			map[string]any{"name": "disposition_id", "data_type": "string", "required": false},
			map[string]any{"name": "resolution_note", "data_type": "text", "required": false},
		)
	}
	return base
}

func validDataType(t string) bool {
	switch t {
	case "string", "text", "integer", "float", "boolean", "date", "enum":
		return true
	}
	return false
}

func parsePurpose(p string) int16 {
	switch p {
	case "create":
		return domain.PurposeCreate
	case "update":
		return domain.PurposeUpdate
	default:
		return domain.PurposeBoth
	}
}

// ---- SLA policy (CASE-FR-012) -----------------------------------------------

type slaPolicyReq struct {
	WarnBeforeSeconds int    `json:"warn_before_seconds"`
	OnBreach          string `json:"on_breach"`
	EscalateTo        string `json:"escalate_to"`
	MaxReassignCount  int    `json:"max_reassign_count"`
}

func (s *Server) handlePutSLAPolicy(w http.ResponseWriter, r *http.Request) {
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
	var req slaPolicyReq
	if !decodeBody(w, r, &req) {
		return
	}
	p := domain.DefaultSLAPolicy(op.Tenant, ws)
	if req.WarnBeforeSeconds > 0 {
		p.WarnBefore = time.Duration(req.WarnBeforeSeconds) * time.Second
	}
	if req.OnBreach != "" {
		p.OnBreach = req.OnBreach
	}
	if req.MaxReassignCount > 0 {
		p.MaxReassignCount = req.MaxReassignCount
	}
	if req.EscalateTo != "" {
		if u, err := uuid.Parse(req.EscalateTo); err == nil {
			p.EscalateTo = &u
		}
	}
	if err := s.Store.PutSLAPolicy(r.Context(), p); err != nil {
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, map[string]any{"workspace_id": ws, "warn_before_seconds": int(p.WarnBefore.Seconds()), "on_breach": p.OnBreach, "max_reassign_count": p.MaxReassignCount})
}

// handleReindex rebuilds a tenant's OpenSearch index and swaps the alias
// (CASE-FR-043).
func (s *Server) handleReindex(w http.ResponseWriter, r *http.Request) {
	c := ClaimsFrom(r.Context())
	tenant, _ := c.Tenant()
	n, err := s.Projector.Reindex(r.Context(), tenant)
	if err != nil {
		writeErr(w, r, domain.EInternal("reindex failed: "+err.Error()))
		return
	}
	writeData(w, http.StatusOK, map[string]any{"reindexed": n})
}
