package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/windrose-ai/go-common/redisx"
)

// TestAC13_ReadyzDegradedWhenRedisDown: with Redis unreachable, /readyz reports
// degraded-replay (not a hard fail) so existing connections keep live-tailing
// from Kafka while replay is unavailable (RTH-FR / AC-13 / BR-9).
func TestAC13_ReadyzDegradedWhenRedisDown(t *testing.T) {
	// Point at a closed port so Ping fails fast.
	s := &Server{Redis: redisx.New("127.0.0.1:1")}
	srv := httptest.NewServer(s.Router())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/readyz")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("readyz status=%d want 200 (degraded, not hard-fail)", resp.StatusCode)
	}
	var body struct {
		Status string            `json:"status"`
		Checks map[string]string `json:"checks"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	if body.Status != "degraded-replay" {
		t.Fatalf("status=%q want degraded-replay", body.Status)
	}
	if body.Checks["redis"] != "down" {
		t.Fatalf("redis check=%q want down", body.Checks["redis"])
	}
}

// TestReadyzGatedOnActionRegistration: with a RegGate set, /readyz reports 503
// with a reason while registration is pending or failed, and flips to 200 once
// registration succeeds (RBC-FR-022 / M1 hardening — not silent best-effort).
func TestReadyzGatedOnActionRegistration(t *testing.T) {
	gate := NewRegGate()
	s := &Server{Redis: redisx.New("127.0.0.1:1"), RegGate: gate}
	srv := httptest.NewServer(s.Router())
	defer srv.Close()

	get := func() (int, string, map[string]string) {
		t.Helper()
		resp, err := http.Get(srv.URL + "/readyz")
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		var body struct {
			Status string            `json:"status"`
			Checks map[string]string `json:"checks"`
		}
		if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
			t.Fatal(err)
		}
		return resp.StatusCode, body.Status, body.Checks
	}

	// Pending -> 503 with a reason in the body.
	if code, status, checks := get(); code != http.StatusServiceUnavailable ||
		status != "degraded" || checks["action_registration"] == "" || checks["action_registration"] == "ok" {
		t.Fatalf("pending: code=%d status=%q checks=%v; want 503 degraded with reason", code, status, checks)
	}

	// Failed -> still 503, reason carries the error.
	gate.Fail("rbac register status 502: bad gateway")
	if code, _, checks := get(); code != http.StatusServiceUnavailable ||
		checks["action_registration"] == "" || checks["action_registration"] == "ok" {
		t.Fatalf("failed: code=%d checks=%v; want 503 with failure reason", code, checks)
	}

	// Success -> 200 again (redis still down => degraded-replay, not a hard fail).
	gate.Succeed()
	if code, status, checks := get(); code != http.StatusOK || status != "degraded-replay" ||
		checks["action_registration"] != "ok" {
		t.Fatalf("succeeded: code=%d status=%q checks=%v; want 200 degraded-replay with registration ok", code, status, checks)
	}
}
