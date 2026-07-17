package api

import (
	"context"
	"encoding/base64"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/audit-service/internal/chain"
	"github.com/windrose-ai/audit-service/internal/domain"
	"github.com/windrose-ai/audit-service/internal/meta"
	"github.com/windrose-ai/audit-service/internal/pgstore"
)

// resolveTenant returns the tenant to query. It is the caller's own tenant from
// the verified JWT (MASTER-FR-001/002); a platform operator may target another
// tenant only with typ=user + the audit.breakglass scope (BR-4).
func (s *Server) resolveTenant(r *http.Request) (uuid.UUID, bool, *domain.Error) {
	claims := ClaimsFrom(r.Context())
	own, err := claims.Tenant()
	if err != nil {
		return uuid.Nil, false, domain.EUnauthenticated("bad tenant claim")
	}
	q := r.URL.Query().Get("tenant_id")
	if q == "" || q == claims.TenantID {
		return own, false, nil
	}
	if claims.Typ == "user" && claims.HasScope("audit.breakglass") {
		t, err := uuid.Parse(q)
		if err != nil {
			return uuid.Nil, false, domain.EValidation("invalid tenant_id", nil)
		}
		return t, true, nil
	}
	// Non-breakglass cross-tenant request: ignore the param, use own tenant.
	return own, false, nil
}

func (s *Server) presignTTL() time.Duration {
	if s.PresignTTL > 0 {
		return s.PresignTTL
	}
	return time.Hour
}

// --- search --------------------------------------------------------------

type eventDTO struct {
	EventID     string          `json:"event_id"`
	EventType   string          `json:"event_type"`
	TenantID    string          `json:"tenant_id"`
	Actor       actorDTO        `json:"actor"`
	ViaAgent    *viaAgentDTO    `json:"via_agent"`
	ResourceURN string          `json:"resource_urn"`
	Action      string          `json:"action"`
	OccurredAt  string          `json:"occurred_at"`
	IngestedAt  string          `json:"ingested_at"`
	TraceID     string          `json:"trace_id"`
	PayloadDig  string          `json:"payload_digest"`
	Payload     json.RawMessage `json:"payload,omitempty"`
	BodyWithheld bool           `json:"body_withheld"`
	ChainSeq    uint64          `json:"chain_seq"`
	ChainHash   string          `json:"chain_hash"`
}

type actorDTO struct {
	Type string `json:"type"`
	ID   string `json:"id"`
}
type viaAgentDTO struct {
	AgentID string `json:"agent_id"`
	Version string `json:"version"`
}

func toDTO(r domain.Record) eventDTO {
	d := eventDTO{
		EventID: r.EventID.String(), EventType: r.EventType, TenantID: r.TenantID.String(),
		Actor:       actorDTO{Type: r.ActorType, ID: r.ActorID},
		ResourceURN: r.ResourceURN, Action: r.Action,
		OccurredAt: r.OccurredAt.UTC().Format(time.RFC3339Nano),
		IngestedAt: r.IngestedAt.UTC().Format(time.RFC3339Nano),
		TraceID:    r.TraceID, PayloadDig: r.PayloadDigest,
		BodyWithheld: r.PayloadJSON == "", ChainSeq: r.ChainSeq, ChainHash: r.ChainHash,
	}
	if r.ViaAgentID != "" {
		d.ViaAgent = &viaAgentDTO{AgentID: r.ViaAgentID, Version: r.ViaAgentVersion}
	}
	if r.PayloadJSON != "" {
		d.Payload = json.RawMessage(r.PayloadJSON)
	}
	return d
}

func (s *Server) parseSearch(r *http.Request, tenant uuid.UUID) (domain.SearchFilter, *domain.Error) {
	q := r.URL.Query()
	var f domain.SearchFilter
	f.TenantID = tenant
	fromS, toS := q.Get("from"), q.Get("to")
	if fromS == "" || toS == "" {
		return f, domain.EValidation("from and to are required (max 92 days)", nil)
	}
	from, err1 := time.Parse(time.RFC3339, fromS)
	to, err2 := time.Parse(time.RFC3339, toS)
	if err1 != nil || err2 != nil {
		return f, domain.EValidation("from/to must be RFC3339", nil)
	}
	if to.Before(from) {
		return f, domain.EValidation("to must be >= from", nil)
	}
	if to.Sub(from) > time.Duration(domain.MaxSearchRangeDays)*24*time.Hour {
		return f, domain.EValidation(fmt.Sprintf("time range exceeds %d days", domain.MaxSearchRangeDays), nil)
	}
	f.From, f.To = from, to
	f.ActorID = q.Get("actor_id")
	f.ActorType = q.Get("actor_type")
	f.ViaAgentID = q.Get("via_agent_id")
	f.Action = q.Get("action")
	f.EventType = q.Get("event_type")
	f.TraceID = q.Get("trace_id")
	if urn := q.Get("resource_urn"); urn != "" {
		if q.Get("resource_match") == "prefix" {
			f.ResourcePrefix = urn
		} else {
			f.ResourceURN = urn
		}
	}
	f.IncludeAuto = q.Get("include_autonomous") == "true"
	f.Limit = 50
	if l := q.Get("limit"); l != "" {
		if n, err := strconv.Atoi(l); err == nil && n > 0 {
			if n > 200 {
				n = 200
			}
			f.Limit = n
		}
	}
	if cur := q.Get("cursor"); cur != "" {
		occ, id, ok := decodeCursor(cur)
		if !ok {
			return f, domain.EValidation("invalid cursor", nil)
		}
		f.AfterOccurred = &occ
		f.AfterEventID = &id
	}
	return f, nil
}

func (s *Server) handleSearch(w http.ResponseWriter, r *http.Request) {
	tenant, breakglass, derr := s.resolveTenant(r)
	if derr != nil {
		writeErr(w, r, derr)
		return
	}
	f, derr := s.parseSearch(r, tenant)
	if derr != nil {
		writeErr(w, r, derr)
		return
	}
	// Auditors are audited (AUD-FR-032, AC-10).
	s.auditSearch(r, tenant, breakglass)

	recs, err := s.CH.Search(r.Context(), f)
	if err != nil {
		writeErr(w, r, domain.EInternal("search failed"))
		return
	}
	hasMore := len(recs) > f.Limit
	if hasMore {
		recs = recs[:f.Limit]
	}
	data := make([]eventDTO, 0, len(recs))
	for _, rec := range recs {
		data = append(data, toDTO(rec))
	}
	page := map[string]any{"has_more": hasMore, "next_cursor": nil}
	if hasMore {
		last := recs[len(recs)-1]
		c := encodeCursor(last.OccurredAt, last.EventID)
		page["next_cursor"] = c
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": data, "page": page})
}

// handleExport streams a filtered CSV / NDJSON export (AUD-FR-032), gated by the
// distinct audit.event.export action. Every export is itself audited.
func (s *Server) handleExport(w http.ResponseWriter, r *http.Request) {
	tenant, breakglass, derr := s.resolveTenant(r)
	if derr != nil {
		writeErr(w, r, derr)
		return
	}
	f, derr := s.parseSearch(r, tenant)
	if derr != nil {
		writeErr(w, r, derr)
		return
	}
	s.auditSearch(r, tenant, breakglass)
	accept := r.Header.Get("Accept")
	if !strings.Contains(accept, "x-ndjson") {
		accept = "text/csv"
	}
	s.streamExport(w, r, f, accept)
}

func (s *Server) handleAgentActivity(w http.ResponseWriter, r *http.Request) {
	tenant, breakglass, derr := s.resolveTenant(r)
	if derr != nil {
		writeErr(w, r, derr)
		return
	}
	q := r.URL.Query()
	agentID := q.Get("agent_id")
	oboUser := q.Get("obo_user_id")
	if agentID == "" {
		writeErr(w, r, domain.EValidation("agent_id is required", nil))
		return
	}
	// Default a wide-but-bounded window when not given.
	now := time.Now().UTC()
	f := domain.SearchFilter{
		TenantID: tenant, ViaAgentID: agentID, ActorID: oboUser,
		From: now.AddDate(0, 0, -domain.MaxSearchRangeDays), To: now,
		IncludeAuto: q.Get("include_autonomous") == "true", Limit: 200,
	}
	if fromS := q.Get("from"); fromS != "" {
		if t, err := time.Parse(time.RFC3339, fromS); err == nil {
			f.From = t
		}
	}
	if toS := q.Get("to"); toS != "" {
		if t, err := time.Parse(time.RFC3339, toS); err == nil {
			f.To = t
		}
	}
	if oboUser == "" {
		// Without an OBO user, agent-activity means "everything this agent did":
		// its OBO rows (any user) plus, optionally, autonomous rows.
		f.ViaAgentID = agentID
	}
	s.auditSearch(r, tenant, breakglass)
	recs, err := s.CH.Search(r.Context(), f)
	if err != nil {
		writeErr(w, r, domain.EInternal("search failed"))
		return
	}
	if len(recs) > f.Limit {
		recs = recs[:f.Limit]
	}
	data := make([]eventDTO, 0, len(recs))
	for _, rec := range recs {
		data = append(data, toDTO(rec))
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": data, "page": map[string]any{"has_more": false, "next_cursor": nil}})
}

func (s *Server) handleGetEvent(w http.ResponseWriter, r *http.Request) {
	tenant, _, derr := s.resolveTenant(r)
	if derr != nil {
		writeErr(w, r, derr)
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "event_id"))
	if err != nil {
		writeErr(w, r, domain.ENotFound())
		return
	}
	rec, err := s.CH.GetEvent(r.Context(), tenant, id)
	if err != nil {
		writeErr(w, r, domain.EInternal("lookup failed"))
		return
	}
	if rec == nil {
		// Cross-tenant and nonexistent are indistinguishable (MASTER-FR-003):
		// 404 + a cross-tenant-denied meta event.
		s.Meta.Searched(r.Context(), tenant, ClaimsFrom(r.Context()).Sub, meta.FilterDigest("event:"+id.String()), false)
		writeErr(w, r, domain.ENotFound())
		return
	}
	// Chain position + verification status for the single record.
	ch, _ := s.PG.GetChainHead(r.Context(), tenant, rec.ChainDate)
	verified := ch != nil && ch.SealedAt != nil
	writeJSON(w, http.StatusOK, map[string]any{
		"event": toDTO(*rec), "chain_date": rec.ChainDate, "chain_seq": rec.ChainSeq,
		"sealed": verified,
	})
}

// --- verify --------------------------------------------------------------

func (s *Server) handleVerify(w http.ResponseWriter, r *http.Request) {
	var body struct {
		TenantID string `json:"tenant_id"`
		Date     string `json:"date"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeErr(w, r, domain.EValidation("invalid body", nil))
		return
	}
	claims := ClaimsFrom(r.Context())
	tenant, _ := claims.Tenant()
	if body.TenantID != "" && body.TenantID != claims.TenantID {
		if claims.Typ == "user" && claims.HasScope("audit.breakglass") {
			if t, err := uuid.Parse(body.TenantID); err == nil {
				tenant = t
			}
		}
	}
	if _, err := time.Parse("2006-01-02", body.Date); err != nil {
		writeErr(w, r, domain.EValidation("date must be YYYY-MM-DD", nil))
		return
	}
	ch, err := s.PG.GetChainHead(r.Context(), tenant, body.Date)
	if err != nil {
		writeErr(w, r, domain.EInternal("checkpoint lookup failed"))
		return
	}
	if ch == nil {
		writeErr(w, r, domain.ENotFound())
		return
	}
	// A day is verifiable against its sealed manifest only once sealed. An
	// unsealed (open) or currently-supplementing day → CONFLICT (BR-9, §5).
	if ch.SealedAt == nil {
		writeErr(w, r, domain.EConflict("day not sealed yet; verify after the WORM export seals it"))
		return
	}
	rows, err := s.CH.ChainScan(r.Context(), tenant, body.Date)
	if err != nil {
		writeErr(w, r, domain.EInternal("chain scan failed"))
		return
	}
	res := chain.Verify(rows, tenant, body.Date, ch.HeadHash)
	res.Sealed = true
	// Any invalid verification is a P1 integrity event — including a
	// manifest-head-only mismatch where no per-row mismatch was found
	// (FirstMismatch nil → reported as seq 0).
	if !res.Valid && s.Meta != nil {
		var seq uint64
		if res.FirstMismatch != nil {
			seq = *res.FirstMismatch
		}
		s.Meta.IntegrityViolation(r.Context(), tenant, body.Date, seq)
	}
	writeJSON(w, http.StatusOK, res)
}

// --- exports -------------------------------------------------------------

func (s *Server) handleListExports(w http.ResponseWriter, r *http.Request) {
	tenant, _, derr := s.resolveTenant(r)
	if derr != nil {
		writeErr(w, r, derr)
		return
	}
	date := r.URL.Query().Get("date")
	mans, err := s.PG.ListSealedManifests(r.Context(), tenant, date)
	if err != nil {
		writeErr(w, r, domain.EInternal("list failed"))
		return
	}
	data := make([]map[string]any, 0, len(mans))
	for _, m := range mans {
		item := map[string]any{
			"date": m.ChainDate, "revision": m.Revision, "uri": m.URI,
			"manifest_sha256": m.ManifestSHA256, "chain_head": m.ChainHead,
			"row_count": m.RowCount, "sealed_at": m.SealedAt,
		}
		if url, err := s.signManifestURL(r, m); err == nil {
			item["download_url"] = url
		}
		data = append(data, item)
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": data, "page": map[string]any{"has_more": false, "next_cursor": nil}})
}

func (s *Server) signManifestURL(r *http.Request, m pgstore.Manifest) (string, error) {
	// s3://bucket/key → key
	key := strings.TrimPrefix(m.URI, "s3://"+s.WORM.Bucket()+"/")
	return s.WORM.PresignGet(r.Context(), key, s.presignTTL())
}

// --- compliance ----------------------------------------------------------

func (s *Server) handleSOC2Pack(w http.ResponseWriter, r *http.Request) {
	s.handlePack(w, r, "soc2", func(ctx context.Context, tenant uuid.UUID, from, to time.Time, _ string) (string, string, error) {
		return s.Compliance.SOC2Pack(ctx, tenant, from, to)
	})
}

func (s *Server) handleAIDecisionLog(w http.ResponseWriter, r *http.Request) {
	s.handlePack(w, r, "ai_decision_log", func(ctx context.Context, tenant uuid.UUID, from, to time.Time, ag string) (string, string, error) {
		return s.Compliance.AIDecisionLog(ctx, tenant, from, to, ag)
	})
}

type packFn func(ctx context.Context, tenant uuid.UUID, from, to time.Time, agentID string) (string, string, error)

// detach returns a background context (with the trace id preserved) so an async
// pack build survives the request lifetime.
func detach(r *http.Request) (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), 30*time.Minute)
}

func (s *Server) handlePack(w http.ResponseWriter, r *http.Request, kind string, fn packFn) {
	var body struct {
		From    string `json:"from"`
		To      string `json:"to"`
		AgentID string `json:"agent_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeErr(w, r, domain.EValidation("invalid body", nil))
		return
	}
	from, err1 := time.Parse(time.RFC3339, body.From)
	to, err2 := time.Parse(time.RFC3339, body.To)
	if err1 != nil || err2 != nil || to.Before(from) {
		writeErr(w, r, domain.EValidation("from/to must be RFC3339 and to>=from", nil))
		return
	}
	claims := ClaimsFrom(r.Context())
	tenant, _ := claims.Tenant()
	jobID := uuid.New()
	digest := meta.FilterDigest(kind + "|" + body.From + "|" + body.To + "|" + body.AgentID)
	if err := s.PG.CreateJob(r.Context(), pgstore.Job{ID: jobID, TenantID: tenant, Kind: kind, ParamsDigest: digest}); err != nil {
		writeErr(w, r, domain.EInternal("job create failed"))
		return
	}
	// Async build (AUD-FR-060/061): 202 + operation id; poll GET /operations/:id.
	go func() {
		ctx, cancel := detach(r)
		defer cancel()
		url, _, err := fn(ctx, tenant, from, to, body.AgentID)
		if err != nil {
			_ = s.PG.FinishJob(ctx, tenant, jobID, "failed", "", err.Error())
			return
		}
		_ = s.PG.FinishJob(ctx, tenant, jobID, "succeeded", url, "")
	}()
	writeJSON(w, http.StatusAccepted, map[string]any{"operation_id": jobID.String(), "status": "running"})
}

func (s *Server) handleGetOperation(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	tenant, _ := claims.Tenant()
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErr(w, r, domain.ENotFound())
		return
	}
	job, err := s.PG.GetJob(r.Context(), tenant, id)
	if err != nil {
		writeErr(w, r, domain.EInternal("lookup failed"))
		return
	}
	if job == nil {
		writeErr(w, r, domain.ENotFound())
		return
	}
	out := map[string]any{"operation_id": job.ID.String(), "status": job.Status}
	if job.ResultURI != "" {
		out["result_url"] = job.ResultURI
	}
	if job.Error != "" {
		out["error"] = job.Error
	}
	writeJSON(w, http.StatusOK, out)
}

// --- dlq redrive ---------------------------------------------------------

func (s *Server) handleRedrive(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Topic string `json:"topic"`
		Max   int    `json:"max"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Topic == "" {
		writeErr(w, r, domain.EValidation("topic is required", nil))
		return
	}
	if body.Max <= 0 || body.Max > 10000 {
		body.Max = 1000
	}
	claims := ClaimsFrom(r.Context())
	tenant, _ := claims.Tenant()
	dlqTopic := body.Topic
	if !strings.HasSuffix(dlqTopic, ".dlq") {
		dlqTopic = fmt.Sprintf("%s.%s.dlq", body.Topic, s.IngestGroup)
	}
	n, err := s.Redriver.Redrive(r.Context(), dlqTopic, body.Max)
	if err != nil {
		writeErr(w, r, domain.EInternal("redrive failed: "+err.Error()))
		return
	}
	_ = s.PG.RecordRedrive(r.Context(), tenant, dlqTopic, n, claims.Sub, "manual redrive")
	if s.Meta != nil {
		s.Meta.DLQRedriven(r.Context(), tenant, claims.Sub, dlqTopic, n)
	}
	writeJSON(w, http.StatusOK, map[string]any{"redriven": n, "topic": dlqTopic})
}

// --- helpers -------------------------------------------------------------

func (s *Server) auditSearch(r *http.Request, tenant uuid.UUID, breakglass bool) {
	if s.Meta == nil {
		return
	}
	claims := ClaimsFrom(r.Context())
	s.Meta.Searched(r.Context(), tenant, claims.Sub, meta.FilterDigest(r.URL.RawQuery), breakglass)
}

func (s *Server) streamExport(w http.ResponseWriter, r *http.Request, f domain.SearchFilter, accept string) {
	const cap = 100000
	f.Limit = 200
	ndjson := strings.Contains(accept, "x-ndjson")
	if ndjson {
		w.Header().Set("Content-Type", "application/x-ndjson")
	} else {
		w.Header().Set("Content-Type", "text/csv")
	}
	w.WriteHeader(http.StatusOK)
	var cw *csv.Writer
	if !ndjson {
		cw = csv.NewWriter(w)
		_ = cw.Write([]string{"occurred_at", "event_type", "actor_type", "actor_id", "via_agent_id", "obo_user_id", "resource_urn", "action", "payload_digest", "body_withheld", "chain_seq"})
	}
	written := 0
	for written < cap {
		recs, err := s.CH.Search(r.Context(), f)
		if err != nil {
			return
		}
		more := len(recs) > f.Limit
		if more {
			recs = recs[:f.Limit]
		}
		for _, rec := range recs {
			if ndjson {
				b, _ := json.Marshal(toDTO(rec))
				_, _ = w.Write(append(b, '\n'))
			} else {
				_ = cw.Write([]string{
					rec.OccurredAt.UTC().Format(time.RFC3339Nano), rec.EventType, rec.ActorType, rec.ActorID,
					rec.ViaAgentID, rec.OboUserID, rec.ResourceURN, rec.Action, rec.PayloadDigest,
					strconv.FormatBool(rec.PayloadJSON == ""), strconv.FormatUint(rec.ChainSeq, 10),
				})
			}
			written++
		}
		if !more || len(recs) == 0 {
			break
		}
		last := recs[len(recs)-1]
		occ, id := last.OccurredAt, last.EventID
		f.AfterOccurred = &occ
		f.AfterEventID = &id
	}
	if cw != nil {
		cw.Flush()
	}
}

func encodeCursor(occ time.Time, id uuid.UUID) string {
	return base64.RawURLEncoding.EncodeToString([]byte(occ.UTC().Format(time.RFC3339Nano) + "|" + id.String()))
}

func decodeCursor(c string) (time.Time, uuid.UUID, bool) {
	raw, err := base64.RawURLEncoding.DecodeString(c)
	if err != nil {
		return time.Time{}, uuid.Nil, false
	}
	parts := strings.SplitN(string(raw), "|", 2)
	if len(parts) != 2 {
		return time.Time{}, uuid.Nil, false
	}
	occ, err := time.Parse(time.RFC3339Nano, parts[0])
	if err != nil {
		return time.Time{}, uuid.Nil, false
	}
	id, err := uuid.Parse(parts[1])
	if err != nil {
		return time.Time{}, uuid.Nil, false
	}
	return occ, id, true
}
