package api

import (
	"net/http"
	"strconv"
	"time"

	"github.com/windrose-ai/query-service/internal/domain"
)

// handleStats is the TA/OP aggregate view (QRY-FR-081, US-10): top queries
// by scan bytes with failure counts over a window.
func (s *Server) handleStats(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	since := time.Now().AddDate(0, 0, -7)
	if v := r.URL.Query().Get("since"); v != "" {
		t, err := time.Parse(time.RFC3339, v)
		if err != nil {
			writeErr(w, r, domain.EValidation("invalid since (RFC3339 required)"))
			return
		}
		since = t
	}
	limit := 20
	if v := r.URL.Query().Get("limit"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 && n <= 200 {
			limit = n
		}
	}
	stats, err := s.Store.QueryStats(r.Context(), op.Tenant, since, limit)
	if err != nil {
		writeErr(w, r, storeErr(err))
		return
	}
	writeData(w, http.StatusOK, map[string]any{
		"since":       since.UTC().Format(time.RFC3339),
		"top_queries": stats,
	})
}

// handleGetLimits returns the tenant's effective ceilings (QRY-FR-042).
func (s *Server) handleGetLimits(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	limits, err := s.Store.GetTenantLimits(r.Context(), op.Tenant)
	if err != nil {
		writeErr(w, r, storeErr(err))
		return
	}
	writeData(w, http.StatusOK, map[string]any{
		"overrides":       limits,
		"effective_user":  domain.EffectiveCeilings(limits, domain.CallerUser, true),
		"effective_agent": domain.EffectiveCeilings(limits, domain.CallerAgent, true),
		"platform_maxima": domain.Ceilings{
			MaxScanBytes:   domain.DefaultMaxScanBytes,
			MaxRuntimeS:    domain.DefaultMaxRuntimeAsyncS,
			MaxResultBytes: domain.DefaultMaxResultBytes,
			MaxResultRows:  domain.DefaultMaxResultRows,
		},
	})
}

// handlePutLimits lets a TA lower ceilings/concurrency (QRY-FR-042/044,
// US-7). Overrides above platform maxima are rejected.
func (s *Server) handlePutLimits(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	var req domain.TenantLimits
	if !decodeBody(w, r, &req) {
		return
	}
	if err := req.Validate(); err != nil {
		writeErr(w, r, err)
		return
	}
	req.UpdatedBy = op.UserID
	if err := s.Store.PutTenantLimits(r.Context(), op, &req); err != nil {
		writeErr(w, r, storeErr(err))
		return
	}
	writeData(w, http.StatusOK, map[string]any{"overrides": req})
}
