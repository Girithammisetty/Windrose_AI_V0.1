package integration

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/audit-service/internal/domain"
)

// --- HTTP helpers --------------------------------------------------------

func (h *harness) do(t *testing.T, method, path, token string, body any) (int, []byte) {
	t.Helper()
	var rdr io.Reader
	if body != nil {
		rdr = bytes.NewReader(mustJSON(body))
	}
	req, err := http.NewRequest(method, h.http.URL+path, rdr)
	if err != nil {
		t.Fatal(err)
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, b
}

func searchCount(b []byte) int {
	var out struct {
		Data []json.RawMessage `json:"data"`
	}
	_ = json.Unmarshal(b, &out)
	return len(out.Data)
}

// TestAC01_KafkaToClickHouse: a real event on a real Kafka topic lands in the
// real ClickHouse store, queryable with a valid payload digest (AUD-FR-001..003).
func TestAC01_KafkaToClickHouse(t *testing.T) {
	h := newHarness(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go h.consumer.Run(ctx)

	tenant := uuid.New()
	e := domain.Envelope{
		EventID: uuid.New(), EventType: "dataset.created", TenantID: tenant,
		Actor: domain.Actor{Type: "user", ID: "u-77"},
		ResourceURN: "wr:" + tenant.String() + ":dataset:dataset/ds-9f2",
		OccurredAt: time.Now().UTC(), TraceID: uuid.NewString(),
		Payload: map[string]any{"name": "claims_2026"},
	}
	h.produce(t, "dataset.events.v1", e)

	deadline := time.Now().Add(40 * time.Second)
	var rec *domain.Record
	for time.Now().Before(deadline) {
		r, err := h.ch.GetEvent(context.Background(), tenant, e.EventID)
		if err == nil && r != nil {
			rec = r
			break
		}
		time.Sleep(500 * time.Millisecond)
	}
	if rec == nil {
		t.Fatal("event never landed in ClickHouse from Kafka")
	}
	if rec.PayloadDigest != domain.PayloadDigest(e.Payload) {
		t.Fatalf("payload digest mismatch: %s", rec.PayloadDigest)
	}
	if rec.ResourceService != "dataset" || rec.ChainSeq == 0 {
		t.Fatalf("envelope columns not populated: %+v", rec)
	}
}

// TestAC04_DLQEnvelopeInvalid: a malformed envelope (missing tenant_id) is
// quarantined to the per-topic DLQ with reason ENVELOPE_INVALID (AUD-FR-002/006).
func TestAC04_DLQEnvelopeInvalid(t *testing.T) {
	h := newHarness(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go h.consumer.Run(ctx)

	// Missing tenant_id → invalid envelope. Produce raw with nil tenant.
	bad := domain.Envelope{
		EventID: uuid.New(), EventType: "case.assigned", TenantID: uuid.Nil,
		Actor: domain.Actor{Type: "user", ID: "u-1"}, OccurredAt: time.Now().UTC(),
		Payload: map[string]any{"x": 1},
	}
	h.produce(t, "case.events.v1", bad)

	dlqTopic := fmt.Sprintf("case.events.v1.%s.dlq", h.group)
	if !h.waitForDLQ(t, dlqTopic, domain.ReasonEnvelopeInvalid, 40*time.Second) {
		t.Fatal("invalid envelope never reached DLQ with ENVELOPE_INVALID")
	}
}

// TestAC05_ChainTamperEvidence: a sealed/checkpointed day verifies valid; after
// mutating a stored row's payload_digest, verification reports valid=false with
// the correct first_mismatch_seq (AUD-FR-050/051).
func TestAC05_ChainTamperEvidence(t *testing.T) {
	h := newHarness(t)
	h.seedCatalog(t)
	tenant := uuid.New()
	admin := "sec-admin"
	h.seedAdmin(t, tenant.String(), admin)
	tok := h.token(admin, tenant.String(), "user", nil)

	for i := 0; i < 8; i++ {
		e := domain.Envelope{
			EventID: uuid.New(), EventType: "case.assigned", TenantID: tenant,
			Actor: domain.Actor{Type: "user", ID: fmt.Sprintf("u-%d", i)},
			ResourceURN: "wr:" + tenant.String() + ":case:case/c-" + fmt.Sprint(i),
			OccurredAt: time.Now().UTC().Add(time.Duration(i) * time.Millisecond),
			Payload: map[string]any{"assignee": fmt.Sprintf("u-%d", i)},
		}
		h.ingestDirect(t, e, "case.events.v1")
	}

	// Seal the day (WORM export) so it is verifiable against its manifest head.
	if _, err := h.exporter.ExportDay(context.Background(), tenant, h.today); err != nil {
		t.Fatalf("seal export: %v", err)
	}

	// Valid before tamper.
	status, body := h.do(t, http.MethodPost, "/api/v1/audit/verify", tok,
		map[string]string{"tenant_id": tenant.String(), "date": h.today})
	if status != 200 {
		t.Fatalf("verify status %d: %s", status, body)
	}
	var v1 struct {
		Valid         bool    `json:"valid"`
		EventsChecked uint64  `json:"events_checked"`
		FirstMismatch *uint64 `json:"first_mismatch_seq"`
	}
	_ = json.Unmarshal(body, &v1)
	if !v1.Valid || v1.EventsChecked != 8 {
		t.Fatalf("pre-tamper verify wrong: %+v (%s)", v1, body)
	}

	// Tamper: replace the row at seq 3 with a mutated payload_digest (higher
	// ingested_at so ReplacingMergeTree FINAL surfaces the tampered copy).
	rows, err := h.ch.ChainScan(context.Background(), tenant, h.today)
	if err != nil || len(rows) < 4 {
		t.Fatalf("chain scan: %v (%d rows)", err, len(rows))
	}
	tampered := rows[3]
	tampered.PayloadDigest = domain.SHA256Hex([]byte("tampered-value"))
	tampered.IngestedAt = tampered.IngestedAt.Add(2 * time.Second)
	if err := h.ch.InsertBatch(context.Background(), []domain.Record{tampered}); err != nil {
		t.Fatalf("tamper insert: %v", err)
	}

	status, body = h.do(t, http.MethodPost, "/api/v1/audit/verify", tok,
		map[string]string{"tenant_id": tenant.String(), "date": h.today})
	if status != 200 {
		t.Fatalf("verify2 status %d: %s", status, body)
	}
	var v2 struct {
		Valid         bool    `json:"valid"`
		FirstMismatch *uint64 `json:"first_mismatch_seq"`
	}
	_ = json.Unmarshal(body, &v2)
	if v2.Valid {
		t.Fatalf("tamper NOT detected: %s", body)
	}
	if v2.FirstMismatch == nil || *v2.FirstMismatch != rows[3].ChainSeq {
		t.Fatalf("wrong first_mismatch_seq: got %v want %d (%s)", v2.FirstMismatch, rows[3].ChainSeq, body)
	}
}

// TestAC06_WORMExportManifest: the daily export writes Parquet + manifest under
// MinIO Object-Lock; the manifest's per-file SHA-256 matches the object, embeds
// the day's chain head, and the day is sealed (AUD-FR-020/021, AC-6).
func TestAC06_WORMExportManifest(t *testing.T) {
	h := newHarness(t)
	tenant := uuid.New()
	for i := 0; i < 5; i++ {
		e := domain.Envelope{
			EventID: uuid.New(), EventType: "case.assigned", TenantID: tenant,
			Actor: domain.Actor{Type: "user", ID: "u-1"},
			ResourceURN: "wr:" + tenant.String() + ":case:case/c-" + fmt.Sprint(i),
			OccurredAt: time.Now().UTC(), Payload: map[string]any{"i": i},
		}
		h.ingestDirect(t, e, "case.events.v1")
	}
	man, err := h.exporter.ExportDay(context.Background(), tenant, h.today)
	if err != nil {
		t.Fatalf("export: %v", err)
	}
	if man == nil {
		t.Fatal("nothing exported")
	}
	// Fetch + parse manifest object from MinIO.
	key := strings.TrimPrefix(man.URI, "s3://"+h.worm.Bucket()+"/")
	mb, err := h.worm.Get(context.Background(), key)
	if err != nil {
		t.Fatalf("get manifest: %v", err)
	}
	if domain.SHA256Hex(mb) != man.ManifestSHA256 {
		t.Fatal("stored manifest sha256 mismatch")
	}
	var mf struct {
		Files []struct {
			Name   string `json:"name"`
			SHA256 string `json:"sha256"`
			Rows   int    `json:"rows"`
		} `json:"files"`
		ChainHead string `json:"chain_head"`
	}
	if err := json.Unmarshal(mb, &mf); err != nil {
		t.Fatalf("manifest parse: %v", err)
	}
	if len(mf.Files) != 1 || mf.Files[0].Rows != 5 {
		t.Fatalf("manifest files wrong: %+v", mf.Files)
	}
	if mf.ChainHead == "" || mf.ChainHead != man.ChainHead {
		t.Fatalf("manifest missing/mismatched chain head")
	}
	// The Parquet object's SHA-256 must match the manifest entry.
	pk := strings.TrimSuffix(key[:strings.LastIndex(key, "/")], "") + "/" + mf.Files[0].Name
	pb, err := h.worm.Get(context.Background(), pk)
	if err != nil {
		t.Fatalf("get parquet: %v", err)
	}
	if domain.SHA256Hex(pb) != mf.Files[0].SHA256 {
		t.Fatal("parquet object sha256 does not match manifest")
	}
	// Object-Lock COMPLIANCE retention must be set on the WORM object.
	mode, until, err := h.worm.Retention(context.Background(), pk)
	if err != nil {
		t.Fatalf("retention: %v", err)
	}
	if !strings.EqualFold(mode, "COMPLIANCE") || until == nil {
		t.Fatalf("WORM object not under compliance retention: mode=%s until=%v", mode, until)
	}
	// Day sealed in Postgres.
	ch, _ := h.pg.GetChainHead(context.Background(), tenant, h.today)
	if ch == nil || ch.SealedAt == nil {
		t.Fatal("day not sealed after export")
	}
}

// TestAC07_DualAttribution: agent-activity returns exactly the OBO rows for a
// user, and +autonomous rows when requested (AUD-FR-031, AC-7).
func TestAC07_DualAttribution(t *testing.T) {
	h := newHarness(t)
	h.seedCatalog(t)
	tenant := uuid.New()
	admin := "sec-admin"
	h.seedAdmin(t, tenant.String(), admin)
	tok := h.token(admin, tenant.String(), "user", nil)

	urn := "wr:" + tenant.String() + ":case:case/c-1"
	for i := 0; i < 3; i++ {
		h.ingestDirect(t, oboEvent(tenant, "u-77", "triage-copilot", "case.assigned", urn), "case.events.v1")
	}
	for i := 0; i < 2; i++ {
		h.ingestDirect(t, autoEvent(tenant, "triage-copilot", "case.scored", urn), "case.events.v1")
	}

	status, body := h.do(t, http.MethodGet,
		"/api/v1/audit/agent-activity?agent_id=triage-copilot&obo_user_id=u-77", tok, nil)
	if status != 200 {
		t.Fatalf("agent-activity status %d: %s", status, body)
	}
	if n := searchCount(body); n != 3 {
		t.Fatalf("expected 3 OBO rows, got %d: %s", n, body)
	}
	status, body = h.do(t, http.MethodGet,
		"/api/v1/audit/agent-activity?agent_id=triage-copilot&obo_user_id=u-77&include_autonomous=true", tok, nil)
	if status != 200 {
		t.Fatalf("agent-activity(auto) status %d", status)
	}
	if n := searchCount(body); n != 5 {
		t.Fatalf("expected 5 rows with autonomous, got %d: %s", n, body)
	}
}

// TestAC09_AdminAPIOPAAuthzAndCrossTenant: OPA gates the admin API (admin allow,
// non-admin deny) and cross-tenant access returns 404 under the default DSN
// (MASTER-FR-003/012, AC-9).
func TestAC09_AdminAPIOPAAuthzAndCrossTenant(t *testing.T) {
	h := newHarness(t)
	h.seedCatalog(t)
	tenantA := uuid.New()
	tenantB := uuid.New()
	adminA := "admin-a"
	h.seedAdmin(t, tenantA.String(), adminA)
	tokA := h.token(adminA, tenantA.String(), "user", nil)

	// Real OPA allows the seeded admin.
	from := time.Now().UTC().Add(-time.Hour).Format(time.RFC3339)
	to := time.Now().UTC().Add(time.Hour).Format(time.RFC3339)
	status, body := h.do(t, http.MethodGet,
		fmt.Sprintf("/api/v1/audit/search?from=%s&to=%s", from, to), tokA, nil)
	if status != 200 {
		t.Fatalf("admin search should be allowed by OPA, got %d: %s", status, body)
	}

	// Non-admin (no flags seeded) is denied by real OPA.
	tokNo := h.token("nobody", tenantA.String(), "user", nil)
	status, body = h.do(t, http.MethodGet,
		fmt.Sprintf("/api/v1/audit/search?from=%s&to=%s", from, to), tokNo, nil)
	if status != http.StatusForbidden {
		t.Fatalf("non-admin should be denied (403), got %d: %s", status, body)
	}

	// Cross-tenant: an event exists for tenant A; admin B must not see it (404).
	e := domain.Envelope{
		EventID: uuid.New(), EventType: "case.assigned", TenantID: tenantA,
		Actor: domain.Actor{Type: "user", ID: "u-1"},
		ResourceURN: "wr:" + tenantA.String() + ":case:case/c-9", OccurredAt: time.Now().UTC(),
		Payload: map[string]any{"a": 1},
	}
	h.ingestDirect(t, e, "case.events.v1")

	adminB := "admin-b"
	h.seedAdmin(t, tenantB.String(), adminB)
	tokB := h.token(adminB, tenantB.String(), "user", nil)
	status, _ = h.do(t, http.MethodGet, "/api/v1/audit/events/"+e.EventID.String(), tokB, nil)
	if status != http.StatusNotFound {
		t.Fatalf("cross-tenant event fetch must be 404, got %d", status)
	}
}

// TestAC03_PIIRejected: an event with an email in an unregistered type stores the
// digest but withholds the body and sets payload_ref (AUD-FR-070, AC-3).
func TestAC03_PIIRejected(t *testing.T) {
	h := newHarness(t)
	h.seedCatalog(t)
	tenant := uuid.New()
	admin := "sec-admin"
	h.seedAdmin(t, tenant.String(), admin)
	tok := h.token(admin, tenant.String(), "user", nil)

	e := domain.Envelope{
		EventID: uuid.New(), EventType: "mystery.leaked", TenantID: tenant,
		Actor: domain.Actor{Type: "service", ID: "svc:leaky"},
		ResourceURN: "wr:" + tenant.String() + ":leaky:thing/x", OccurredAt: time.Now().UTC(),
		Payload: map[string]any{"contact": "jane.doe@example.com"},
	}
	h.ingestDirect(t, e, "mystery.events.v1")

	status, body := h.do(t, http.MethodGet, "/api/v1/audit/events/"+e.EventID.String()+"?tenant_id="+tenant.String(), tok, nil)
	if status != 200 {
		t.Fatalf("get event %d: %s", status, body)
	}
	var out struct {
		Event struct {
			PayloadDigest string          `json:"payload_digest"`
			Payload       json.RawMessage `json:"payload"`
			BodyWithheld  bool            `json:"body_withheld"`
		} `json:"event"`
	}
	_ = json.Unmarshal(body, &out)
	if out.Event.PayloadDigest == "" {
		t.Fatal("digest must be kept")
	}
	if !out.Event.BodyWithheld || len(out.Event.Payload) != 0 {
		t.Fatalf("PII body must be withheld: %s", body)
	}
}

// TestAC09b_PostgresRLSNonOwner: the runtime pool connects as the NON-owner
// audit_rw role and Postgres RLS (FORCE) blocks cross-tenant reads of metadata
// even with no WHERE predicate (MASTER-FR-001).
func TestAC09b_PostgresRLSNonOwner(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()
	tenantA := uuid.New()
	if err := h.pg.UpsertChainHead(ctx, tenantA, h.today, "deadbeef", 1); err != nil {
		t.Fatalf("seed chain head: %v", err)
	}
	// Raw connection from the runtime (audit_rw) pool.
	conn, err := h.pg.Pool().Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Release()
	var role string
	if err := conn.QueryRow(ctx, "SELECT current_user").Scan(&role); err != nil {
		t.Fatal(err)
	}
	if role != "audit_rw" {
		t.Fatalf("runtime role must be non-owner audit_rw, got %q", role)
	}
	// Under tenant B, a predicate-free scan must see zero of A's rows (RLS).
	if _, err := conn.Exec(ctx, "SELECT set_config('app.tenant_id',$1,false)", uuid.New().String()); err != nil {
		t.Fatal(err)
	}
	var n int
	if err := conn.QueryRow(ctx, "SELECT count(*) FROM chain_heads").Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 0 {
		t.Fatalf("RLS breach: tenant B saw %d rows of tenant A metadata", n)
	}
}
