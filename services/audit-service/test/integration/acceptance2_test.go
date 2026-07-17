package integration

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/audit-service/internal/domain"
	"github.com/windrose-ai/audit-service/internal/ingest"
	gcevent "github.com/windrose-ai/go-common/event"
)

// TestAC08_AIDecisionLogReproducible: the EU AI Act decision-log pack is built to
// real MinIO, downloadable via signed URL, and re-running with identical params
// yields a byte-identical ai_decision_log.csv (AUD-FR-061/062, AC-8).
func TestAC08_AIDecisionLogReproducible(t *testing.T) {
	h := newHarness(t)
	h.seedCatalog(t)
	tenant := uuid.New()
	admin := "sec-admin"
	h.seedAdmin(t, tenant.String(), admin)
	tok := h.token(admin, tenant.String(), "user", nil)

	urn := "wr:" + tenant.String() + ":ai:proposal/p-1"
	base := time.Now().UTC().Add(-time.Hour)
	decisions := []struct {
		etype string
		user  string
	}{
		{"proposal.proposed", "u-1"},
		{"proposal.approved", "u-1"},
		{"proposal.rejected", "u-2"},
		{"proposal.edited", "u-3"},
	}
	for i, d := range decisions {
		e := domain.Envelope{
			EventID: uuid.New(), EventType: d.etype, TenantID: tenant,
			Actor: domain.Actor{Type: "user", ID: d.user},
			ViaAgent: &domain.ViaAgent{AgentID: "triage-copilot", Version: "1.0.0"},
			ResourceURN: urn, OccurredAt: base.Add(time.Duration(i) * time.Second),
			TraceID: "trace-p1", Payload: map[string]any{"decision": d.etype},
		}
		h.ingestDirect(t, e, "ai.proposal.v1")
	}
	// One executing tool call correlated by trace_id.
	h.ingestDirect(t, domain.Envelope{
		EventID: uuid.New(), EventType: "ai.tool_invoked", TenantID: tenant,
		Actor: domain.Actor{Type: "agent", ID: "triage-copilot"},
		ViaAgent: &domain.ViaAgent{AgentID: "triage-copilot", Version: "1.0.0"},
		ResourceURN: urn, OccurredAt: base.Add(5 * time.Second), TraceID: "trace-p1",
		Payload: map[string]any{"tool": "assign"},
	}, "ai.tool_invoked.v1")

	from := base.Add(-time.Hour).Format(time.RFC3339)
	to := time.Now().UTC().Add(time.Hour).Format(time.RFC3339)

	csv1 := h.runPackAndGetCSV(t, tok, from, to)
	csv2 := h.runPackAndGetCSV(t, tok, from, to)
	if !bytes.Equal(csv1, csv2) {
		t.Fatalf("ai_decision_log.csv not reproducible:\n--- run1 ---\n%s\n--- run2 ---\n%s", csv1, csv2)
	}
	// The CSV must carry one row per decision (4) with the executing tool count.
	lines := bytes.Count(bytes.TrimSpace(csv1), []byte("\n")) + 1
	if lines != 1+4 { // header + 4 decisions
		t.Fatalf("expected header + 4 decision rows, got %d lines:\n%s", lines, csv1)
	}
	if !bytes.Contains(csv1, []byte("rejected")) || !bytes.Contains(csv1, []byte("edited")) {
		t.Fatal("decision outcomes missing from decision log")
	}
}

// runPackAndGetCSV POSTs the AI decision-log pack, polls the operation to done,
// downloads the zip from the signed URL and returns ai_decision_log.csv.
func (h *harness) runPackAndGetCSV(t *testing.T, tok, from, to string) []byte {
	t.Helper()
	status, body := h.do(t, http.MethodPost, "/api/v1/compliance/ai-decision-log", tok,
		map[string]string{"from": from, "to": to})
	if status != http.StatusAccepted {
		t.Fatalf("pack request status %d: %s", status, body)
	}
	var acc struct {
		OperationID string `json:"operation_id"`
	}
	_ = json.Unmarshal(body, &acc)
	var url string
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		st, b := h.do(t, http.MethodGet, "/api/v1/operations/"+acc.OperationID, tok, nil)
		if st != 200 {
			t.Fatalf("operation poll %d: %s", st, b)
		}
		var op struct {
			Status    string `json:"status"`
			ResultURL string `json:"result_url"`
			Error     string `json:"error"`
		}
		_ = json.Unmarshal(b, &op)
		if op.Status == "succeeded" {
			url = op.ResultURL
			break
		}
		if op.Status == "failed" {
			t.Fatalf("pack job failed: %s", op.Error)
		}
		time.Sleep(300 * time.Millisecond)
	}
	if url == "" {
		t.Fatal("pack never completed")
	}
	resp, err := http.Get(url)
	if err != nil {
		t.Fatalf("download pack: %v", err)
	}
	defer resp.Body.Close()
	zb, _ := io.ReadAll(resp.Body)
	zr, err := zip.NewReader(bytes.NewReader(zb), int64(len(zb)))
	if err != nil {
		t.Fatalf("open pack zip: %v", err)
	}
	for _, f := range zr.File {
		if f.Name == "ai_decision_log.csv" {
			rc, _ := f.Open()
			data, _ := io.ReadAll(rc)
			rc.Close()
			return data
		}
	}
	t.Fatal("ai_decision_log.csv not found in pack")
	return nil
}

// TestAC15_DLQRedrive: a quarantined message whose producer is fixed re-ingests
// via redrive, lands in the chain, and is queryable (AUD-FR-006, AC-15).
func TestAC15_DLQRedrive(t *testing.T) {
	h := newHarness(t)
	tenant := uuid.New()

	// Simulate a fixed producer: a poison message on the DLQ whose raw payload is
	// now a valid envelope. Publish it to the DLQ topic, then redrive.
	good := domain.Envelope{
		EventID: uuid.New(), EventType: "case.assigned", TenantID: tenant,
		Actor: domain.Actor{Type: "user", ID: "u-redrive"},
		ResourceURN: "wr:" + tenant.String() + ":case:case/c-r", OccurredAt: time.Now().UTC(),
		TraceID: uuid.NewString(), Payload: map[string]any{"assignee": "u-9"},
	}
	rawJSON, _ := json.Marshal(good)
	dlqTopic := fmt.Sprintf("case.events.v1.%s.dlq", h.group)
	poison := gcevent.New("audit.dlq.poison", tenant,
		gcevent.Actor{Type: "service", ID: "audit-service"}, "", "",
		map[string]any{"reason": domain.ReasonEnvelopeInvalid, "source_topic": "case.events.v1", "raw": string(rawJSON)})
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := h.producer.Publish(ctx, dlqTopic, poison); err != nil {
		t.Fatalf("seed DLQ: %v", err)
	}

	// Redrive.
	var n int
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		got, err := h.consumer.Redrive(ctx, dlqTopic, 10)
		if err != nil {
			t.Fatalf("redrive: %v", err)
		}
		if got > 0 {
			n = got
			break
		}
		time.Sleep(500 * time.Millisecond)
	}
	if n == 0 {
		t.Fatal("redrive processed nothing")
	}
	rec, err := h.ch.GetEvent(context.Background(), tenant, good.EventID)
	if err != nil || rec == nil {
		t.Fatalf("redriven event not in ClickHouse: err=%v", err)
	}
	if rec.ChainSeq == 0 {
		t.Fatal("redriven event not placed in the chain")
	}
	_ = ingest.Source{}
}
