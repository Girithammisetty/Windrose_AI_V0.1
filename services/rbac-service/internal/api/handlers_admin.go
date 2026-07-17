package api

import (
	"log/slog"
	"net/http"
	"strconv"

	"github.com/windrose-ai/rbac-service/internal/events"
	"github.com/windrose-ai/rbac-service/internal/projection"
	"github.com/windrose-ai/rbac-service/internal/store"
)

// handleProjectionRebuild enqueues a full per-tenant rebuild (RBC-FR-043):
// every known user is marked dirty; the recompute worker drains the queue.
// Long-running semantics per MASTER-FR-027: 202 + operation id.
func (s *Server) handleProjectionRebuild(w http.ResponseWriter, r *http.Request) {
	tenant, err := parseUUIDField(r.URL.Query().Get("tenant"))
	if err != nil {
		writeError(w, r, http.StatusBadRequest, "VALIDATION_FAILED", "tenant query parameter must be a uuid", nil)
		return
	}
	count, err := s.Store.MarkTenantDirty(r.Context(), tenant, "projection.rebuild")
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	env := events.NewEnvelope(events.EvProjectionRebuilt, tenant,
		events.Actor{Type: "user", ID: ClaimsFrom(r.Context()).Sub}, "", TraceID(r.Context()),
		map[string]any{"users_enqueued": count})
	if err := s.Store.InsertAudit(r.Context(), env); err != nil {
		slog.Warn("projection.rebuilt audit emit failed", "err", err)
	}
	writeJSON(w, http.StatusAccepted, map[string]any{
		"operation_id":   env.EventID.String(),
		"users_enqueued": count,
	})
}

// handleProjectionVerify compares sampled users' projections against SQL
// ground truth, repairing drift (RBC-FR-043 weekly verification, AC-12).
func (s *Server) handleProjectionVerify(w http.ResponseWriter, r *http.Request) {
	tenant, err := parseUUIDField(r.URL.Query().Get("tenant"))
	if err != nil {
		writeError(w, r, http.StatusBadRequest, "VALIDATION_FAILED", "tenant query parameter must be a uuid", nil)
		return
	}
	sample, _ := strconv.Atoi(r.URL.Query().Get("sample"))
	users, err := s.Store.TenantUserIDs(r.Context(), tenant)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	if sample > 0 && sample < len(users) {
		users = users[:sample]
	}
	repair := r.URL.Query().Get("repair") != "false"
	res, err := projection.Verify(r.Context(), s.Store, s.Reader, s.Writer, tenant, users, repair)
	if err != nil {
		writeError(w, r, http.StatusInternalServerError, "INTERNAL", "verification failed", nil)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"tenant_id": res.TenantID, "users_checked": res.UsersChecked,
		"drift": res.Drift(), "drifted_users": res.DriftedUsers, "repaired_users": res.RepairedUsers,
	})
}

// handleSeedTenant provisions a tenant on demand (mirrors the
// tenant.provisioned consumer; used by ops and tests).
func (s *Server) handleSeedTenant(w http.ResponseWriter, r *http.Request) {
	tenant, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	claims := ClaimsFrom(r.Context())
	err := s.Store.SeedTenant(r.Context(), store.Op{
		Tenant:  tenant,
		Actor:   events.Actor{Type: "service", ID: claims.Sub},
		TraceID: TraceID(r.Context()),
	})
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"tenant_id": tenant.String(), "status": "seeded"})
}
