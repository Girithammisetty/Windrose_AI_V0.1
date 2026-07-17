// Package compliance builds evidence packs (AUD-FR-060/061): the SOC 2 pack
// (access changes, permission denials, admin actions, user lifecycle, agent
// governance, integrity) and the EU AI Act agent-decision log (proposal
// lifecycle joined with executing tool calls + dual attribution). Packs are
// deterministic: identical params yield byte-identical CSVs (AUD-FR-062, AC-8).
package compliance

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/audit-service/internal/chstore"
	"github.com/windrose-ai/audit-service/internal/domain"
	"github.com/windrose-ai/audit-service/internal/worm"
)

// Builder produces packs.
type Builder struct {
	CH   *chstore.Store
	WORM *worm.Client
}

// eventCSVHeader is the stable column order for event CSVs.
var eventCSVHeader = []string{
	"occurred_at", "event_type", "actor_type", "actor_id", "via_agent_id",
	"obo_user_id", "resource_urn", "action", "payload_digest", "chain_seq",
}

func eventRow(r domain.Record) []string {
	return []string{
		r.OccurredAt.UTC().Format(time.RFC3339Nano), r.EventType, r.ActorType, r.ActorID,
		r.ViaAgentID, r.OboUserID, r.ResourceURN, r.Action, r.PayloadDigest,
		strconv.FormatUint(r.ChainSeq, 10),
	}
}

// sortRecords gives a deterministic order for reproducible CSVs (AC-8).
func sortRecords(recs []domain.Record) {
	sort.SliceStable(recs, func(i, j int) bool {
		if !recs[i].OccurredAt.Equal(recs[j].OccurredAt) {
			return recs[i].OccurredAt.Before(recs[j].OccurredAt)
		}
		return recs[i].EventID.String() < recs[j].EventID.String()
	})
}

func writeCSV(header []string, recs []domain.Record) []byte {
	var buf bytes.Buffer
	w := csv.NewWriter(&buf)
	_ = w.Write(header)
	for _, r := range recs {
		_ = w.Write(eventRow(r))
	}
	w.Flush()
	return buf.Bytes()
}

// selectByTypes fetches tenant rows in [from,to] whose event_type has any of the
// given prefixes (deterministic order).
func (b *Builder) selectByTypes(ctx context.Context, tenant uuid.UUID, from, to time.Time, prefixes ...string) ([]domain.Record, error) {
	where := "tenant_id = ? AND occurred_at >= ? AND occurred_at <= ?"
	args := []any{tenant, chTime(from), chTime(to)}
	if len(prefixes) > 0 {
		where += " AND ("
		for i, p := range prefixes {
			if i > 0 {
				where += " OR "
			}
			where += "startsWith(event_type, ?)"
			args = append(args, p)
		}
		where += ")"
	}
	recs, err := b.CH.RawSelect(ctx, where, "occurred_at ASC, event_id ASC", args...)
	if err != nil {
		return nil, err
	}
	sortRecords(recs)
	return recs, nil
}

// SOC2Pack builds the SOC 2 evidence pack (AUD-FR-060) and uploads it as a zip,
// returning a signed download URL.
func (b *Builder) SOC2Pack(ctx context.Context, tenant uuid.UUID, from, to time.Time) (string, string, error) {
	access, err := b.selectByTypes(ctx, tenant, from, to, "rbac.", "identity.role", "identity.group")
	if err != nil {
		return "", "", err
	}
	denials, err := b.selectByTypes(ctx, tenant, from, to, "security.", "permission_denied", "rbac.permission_denied")
	if err != nil {
		return "", "", err
	}
	admin, err := b.selectByTypes(ctx, tenant, from, to, "tenant.", "workspace.", "config.", "service_account.")
	if err != nil {
		return "", "", err
	}
	lifecycle, err := b.selectByTypes(ctx, tenant, from, to, "identity.user", "user.")
	if err != nil {
		return "", "", err
	}
	governance, err := b.selectByTypes(ctx, tenant, from, to, "agent.", "ai.agent_run", "rbac.agent")
	if err != nil {
		return "", "", err
	}

	files := map[string][]byte{
		"access_changes.csv":    writeCSV(eventCSVHeader, access),
		"permission_denials.csv": writeCSV(eventCSVHeader, denials),
		"admin_actions.csv":     writeCSV(eventCSVHeader, admin),
		"user_lifecycle.csv":    writeCSV(eventCSVHeader, lifecycle),
		"agent_governance.csv":  writeCSV(eventCSVHeader, governance),
	}
	integrity, _ := json.MarshalIndent(map[string]any{
		"period_from": from.UTC().Format(time.RFC3339), "period_to": to.UTC().Format(time.RFC3339),
		"note": "chain verification is available per day via POST /audit/verify",
	}, "", " ")
	files["integrity.json"] = integrity

	manifest := map[string]any{
		"kind": "soc2", "tenant_id": tenant.String(),
		"from": from.UTC().Format(time.RFC3339), "to": to.UTC().Format(time.RFC3339),
		"generated_at": time.Now().UTC().Format(time.RFC3339Nano),
		"file_sha256":  fileHashes(files),
	}
	return b.uploadPack(ctx, tenant, "soc2", from, to, files, manifest)
}

// AIDecisionLog builds the EU AI Act agent-decision log (AUD-FR-061): proposal
// lifecycle events joined (by proposal_id/trace_id) with executing tool calls,
// one row per decision, plus a per-agent summary.
func (b *Builder) AIDecisionLog(ctx context.Context, tenant uuid.UUID, from, to time.Time, agentID string) (string, string, error) {
	proposals, err := b.selectByTypes(ctx, tenant, from, to, "ai.proposal", "proposal.")
	if err != nil {
		return "", "", err
	}
	tools, err := b.selectByTypes(ctx, tenant, from, to, "ai.tool_invoked")
	if err != nil {
		return "", "", err
	}
	if agentID != "" {
		proposals = filterAgent(proposals, agentID)
		tools = filterAgent(tools, agentID)
	}
	toolByTrace := map[string][]domain.Record{}
	for _, t := range tools {
		toolByTrace[t.TraceID] = append(toolByTrace[t.TraceID], t)
	}

	var buf bytes.Buffer
	w := csv.NewWriter(&buf)
	_ = w.Write([]string{
		"decision_at", "event_type", "proposal_ref", "decision_actor_type", "decision_actor_id",
		"agent_id", "rejection_or_edit_digest", "executing_tool_calls", "trace_id",
	})
	summary := map[string]map[string]int{}
	for _, p := range proposals {
		agent := p.ViaAgentID
		if agent == "" && p.ActorType == "agent" {
			agent = p.ActorID
		}
		outcome := decisionOutcome(p.EventType)
		if summary[agent] == nil {
			summary[agent] = map[string]int{}
		}
		summary[agent][outcome]++
		_ = w.Write([]string{
			p.OccurredAt.UTC().Format(time.RFC3339Nano), p.EventType,
			domain.ParseURN(p.ResourceURN).ID, p.ActorType, p.ActorID, agent,
			p.PayloadDigest, strconv.Itoa(len(toolByTrace[p.TraceID])), p.TraceID,
		})
	}
	w.Flush()

	summaryJSON, _ := json.MarshalIndent(summary, "", " ")
	files := map[string][]byte{
		"ai_decision_log.csv":  buf.Bytes(),
		"agent_summary.json":   summaryJSON,
	}
	manifest := map[string]any{
		"kind": "ai_decision_log", "tenant_id": tenant.String(), "agent_id": agentID,
		"from": from.UTC().Format(time.RFC3339), "to": to.UTC().Format(time.RFC3339),
		"generated_at": time.Now().UTC().Format(time.RFC3339Nano),
		"file_sha256":  fileHashes(files),
	}
	return b.uploadPack(ctx, tenant, "ai-decision-log", from, to, files, manifest)
}

func (b *Builder) uploadPack(ctx context.Context, tenant uuid.UUID, kind string, from, to time.Time, files map[string][]byte, manifest map[string]any) (string, string, error) {
	manBytes, _ := json.MarshalIndent(manifest, "", " ")
	files["pack_manifest.json"] = manBytes

	// Deterministic zip: stable file order, fixed modtime.
	var names []string
	for n := range files {
		names = append(names, n)
	}
	sort.Strings(names)
	var zbuf bytes.Buffer
	zw := zip.NewWriter(&zbuf)
	for _, n := range names {
		hdr := &zip.FileHeader{Name: n, Method: zip.Deflate, Modified: time.Unix(0, 0).UTC()}
		fw, err := zw.CreateHeader(hdr)
		if err != nil {
			return "", "", err
		}
		if _, err := fw.Write(files[n]); err != nil {
			return "", "", err
		}
	}
	if err := zw.Close(); err != nil {
		return "", "", err
	}
	key := fmt.Sprintf("compliance/tenant=%s/%s/%s_%s_%s.zip", tenant, kind,
		from.UTC().Format("20060102"), to.UTC().Format("20060102"), uuid.NewString()[:8])
	if _, err := b.WORM.PutObject(ctx, key, zbuf.Bytes(), "application/zip"); err != nil {
		return "", "", err
	}
	url, err := b.WORM.PresignGet(ctx, key, 24*time.Hour)
	if err != nil {
		return "", "", err
	}
	return url, b.WORM.URI(key), nil
}

func fileHashes(files map[string][]byte) map[string]string {
	out := map[string]string{}
	for n, b := range files {
		out[n] = domain.SHA256Hex(b)
	}
	return out
}

func filterAgent(recs []domain.Record, agentID string) []domain.Record {
	var out []domain.Record
	for _, r := range recs {
		if r.ViaAgentID == agentID || (r.ActorType == "agent" && r.ActorID == agentID) {
			out = append(out, r)
		}
	}
	return out
}

func decisionOutcome(eventType string) string {
	switch {
	case contains(eventType, "approved"):
		return "approved"
	case contains(eventType, "rejected"):
		return "rejected"
	case contains(eventType, "edited"):
		return "edited"
	case contains(eventType, "expired"):
		return "expired"
	case contains(eventType, "proposed"), contains(eventType, "created"):
		return "proposed"
	default:
		return "other"
	}
}

func contains(s, sub string) bool { return bytes.Contains([]byte(s), []byte(sub)) }

// chTime formats a time as a ms-precision ClickHouse DateTime64(3) literal.
func chTime(t time.Time) string { return t.UTC().Format("2006-01-02 15:04:05.000") }
