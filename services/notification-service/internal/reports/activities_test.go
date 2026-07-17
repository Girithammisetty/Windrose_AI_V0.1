package reports

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/windrose-ai/notification-service/internal/channels/email"
	"github.com/windrose-ai/notification-service/internal/domain"
)

// fakeStore is a real in-memory implementation of ReportStore (not a mock
// framework) — the same pattern email/sender_test.go uses for Provider.
type fakeStore struct {
	sub        *domain.ReportSubscription
	lastStatus string
	lastErr    string
	runs       int
}

func (f *fakeStore) GetReportSubscription(_ context.Context, tenant, id uuid.UUID) (*domain.ReportSubscription, error) {
	if f.sub == nil || f.sub.TenantID != tenant || f.sub.ID != id {
		return nil, domain.ENotFound()
	}
	return f.sub, nil
}

func (f *fakeStore) RecordReportRun(_ context.Context, _, _ uuid.UUID, status, sendErr string) error {
	f.runs++
	f.lastStatus, f.lastErr = status, sendErr
	return nil
}

type fakeEmailProvider struct {
	sent []email.Message
	fail bool
}

func (p *fakeEmailProvider) Name() string { return "fake" }
func (p *fakeEmailProvider) Send(_ context.Context, m email.Message) (string, error) {
	if p.fail {
		return "", &email.SendError{Class: email.ClassPermanent, Err: errClassify}
	}
	p.sent = append(p.sent, m)
	return "msg-1", nil
}
func (p *fakeEmailProvider) ParseStatusCallback(*http.Request) ([]email.StatusUpdate, error) {
	return nil, nil
}

var errClassify = &stubErr{"rejected"}

type stubErr struct{ s string }

func (e *stubErr) Error() string { return e.s }

// newFakeChartServer serves the exact three endpoints ChartClient calls,
// returning REAL-shaped (not fabricated in the product sense — this is test
// fixture data standing in for chart-service, same as httptest doubles used
// elsewhere for downstream HTTP deps) responses.
func newFakeChartServer(t *testing.T, dashboardName string, chartID uuid.UUID) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/dashboards/", func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/charts"):
			_ = json.NewEncoder(w).Encode(map[string]any{
				"data": []map[string]any{{"id": chartID, "name": "Open claims by region", "chart_type": "bar"}},
			})
		case strings.HasSuffix(r.URL.Path, "/data") && r.Method == http.MethodPost:
			_ = json.NewEncoder(w).Encode(map[string]any{
				"data": map[string]any{"results": []map[string]any{{
					"chart_id": chartID.String(),
					"data": map[string]any{
						"columns": []string{"region", "count"}, "rows": [][]any{{"West", 42}}, "row_count": 1,
					},
				}}},
			})
		default:
			_ = json.NewEncoder(w).Encode(map[string]any{"data": map[string]any{"id": chartID, "name": dashboardName}})
		}
	})
	return httptest.NewServer(mux)
}

func TestActivities_SendReportEmail_RealDataEndToEnd(t *testing.T) {
	chartID := uuid.New()
	srv := newFakeChartServer(t, "Claims overview", chartID)
	defer srv.Close()

	sub := &domain.ReportSubscription{
		ID: domain.NewID(), TenantID: uuid.New(), DashboardID: uuid.New(),
		Name: "Weekly", Recipients: []string{"manager@demo.windrose"}, Cadence: domain.CadenceDaily,
		SendHour: 8, Timezone: "UTC", Format: domain.ReportFormatHTML, Enabled: true, CreatedBy: "manager@demo.windrose",
	}
	store := &fakeStore{sub: sub}
	provider := &fakeEmailProvider{}
	acts := &Activities{
		Store:  store,
		Charts: NewChartClient(srv.URL),
		Tokens: &TokenMinter{}, // KeyPEM empty → MintOBO would fail; override below
		Email:  email.NewSender(provider),
	}
	// Bypass real RSA signing in the unit test (token minting itself is covered
	// by identity's own JWT libraries + this package's token.go doc); patch in
	// a minter that always succeeds so this test isolates the data+email path.
	acts.Tokens = &TokenMinter{KeyPEM: testRSAKeyPEM(t), KID: "test", Issuer: "iss", Audience: "aud"}

	err := acts.SendReportEmail(context.Background(), ReportRunInput{SubscriptionID: sub.ID, TenantID: sub.TenantID})
	if err != nil {
		t.Fatalf("SendReportEmail: %v", err)
	}
	if len(provider.sent) != 1 {
		t.Fatalf("expected 1 email sent, got %d", len(provider.sent))
	}
	msg := provider.sent[0]
	if msg.To != "manager@demo.windrose" {
		t.Fatalf("unexpected recipient: %q", msg.To)
	}
	if !strings.Contains(msg.HTML, "West") || !strings.Contains(msg.HTML, "42") {
		t.Fatalf("email body missing the real fetched data: %s", msg.HTML)
	}
	if store.lastStatus != domain.ReportStatusSent {
		t.Fatalf("expected last_status=sent, got %q (err=%q)", store.lastStatus, store.lastErr)
	}
}

func TestActivities_SendReportEmail_DisabledSubscriptionIsANoOp(t *testing.T) {
	sub := &domain.ReportSubscription{ID: domain.NewID(), TenantID: uuid.New(), Enabled: false}
	store := &fakeStore{sub: sub}
	acts := &Activities{Store: store, Charts: NewChartClient("http://unused.invalid"), Tokens: &TokenMinter{}, Email: email.NewSender()}
	if err := acts.SendReportEmail(context.Background(), ReportRunInput{SubscriptionID: sub.ID, TenantID: sub.TenantID}); err != nil {
		t.Fatalf("expected no-op success for a disabled subscription, got %v", err)
	}
	if store.runs != 0 {
		t.Fatalf("a disabled subscription should not record a run, got %d", store.runs)
	}
}
