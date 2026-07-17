package api

import (
	"net/http"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/domain"
)

type commentReq struct {
	Body string `json:"body"`
}

// commentEditWindow is how long an author may edit/delete their own comment
// (CASE-FR-024).
const commentEditWindow = 15 * time.Minute

func (s *Server) handleAddComment(w http.ResponseWriter, r *http.Request) {
	op, id, ok := s.opCase(w, r)
	if !ok {
		return
	}
	var req commentReq
	if !decodeBody(w, r, &req) {
		return
	}
	if req.Body == "" || len(req.Body) > 8192 {
		writeErr(w, r, domain.EValidation("body is required and must be ≤ 8KB", nil))
		return
	}
	c, err := s.Store.AddComment(r.Context(), op, id, req.Body)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	writeData(w, http.StatusCreated, c)
}

func (s *Server) handleEditComment(w http.ResponseWriter, r *http.Request) {
	op, ok := opFrom(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("bad claims"))
		return
	}
	cid, err := uuid.Parse(chiURLParam(r, "cid"))
	if err != nil {
		s.notFound(w, r)
		return
	}
	existing, err := s.Store.GetComment(r.Context(), op.Tenant, cid)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	if !s.canModifyComment(op, existing) {
		writeErr(w, r, domain.EPermissionDenied("only the author may edit within 15 minutes"))
		return
	}
	var req commentReq
	if !decodeBody(w, r, &req) {
		return
	}
	if req.Body == "" || len(req.Body) > 8192 {
		writeErr(w, r, domain.EValidation("body is required and must be ≤ 8KB", nil))
		return
	}
	if err := s.Store.EditComment(r.Context(), op.Tenant, cid, req.Body); err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, map[string]any{"id": cid, "body": req.Body})
}

func (s *Server) handleDeleteComment(w http.ResponseWriter, r *http.Request) {
	op, ok := opFrom(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("bad claims"))
		return
	}
	cid, err := uuid.Parse(chiURLParam(r, "cid"))
	if err != nil {
		s.notFound(w, r)
		return
	}
	existing, err := s.Store.GetComment(r.Context(), op.Tenant, cid)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	if !s.canModifyComment(op, existing) {
		writeErr(w, r, domain.EPermissionDenied("only the author may delete within 15 minutes"))
		return
	}
	if err := s.Store.DeleteComment(r.Context(), op.Tenant, cid); err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) canModifyComment(op domain.Op, c *domain.Comment) bool {
	return c.AuthorID == op.UserID && time.Since(c.CreatedAt) <= commentEditWindow
}

// handleTimeline returns the merged event+comment timeline (CASE-FR-025).
func (s *Server) handleTimeline(w http.ResponseWriter, r *http.Request) {
	tenant, id, ok := s.pathCase(w, r)
	if !ok {
		return
	}
	limit := atoiDefault(r.URL.Query().Get("limit"), 50)
	var before *time.Time
	if cur := r.URL.Query().Get("cursor"); cur != "" {
		if t, err := time.Parse(time.RFC3339Nano, cur); err == nil {
			before = &t
		}
	}
	acts, err := s.Store.ListTimeline(r.Context(), tenant, id, limit, before)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	out := make([]any, 0, len(acts))
	for _, a := range acts {
		out = append(out, a)
	}
	page := PageInfo{}
	if len(acts) == limit && len(acts) > 0 {
		page.HasMore = true
		page.NextCursor = acts[len(acts)-1].OccurredAt.Format(time.RFC3339Nano)
	}
	writeJSON(w, http.StatusOK, PageEnvelope{Data: out, Page: page})
}
