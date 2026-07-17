package metricsx

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestMiddlewareRecordsAndHandlerRenders(t *testing.T) {
	reg := New("test-service")
	h := reg.Middleware(func(r *http.Request) string { return r.URL.Path })(
		http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusTeapot)
			_, _ = w.Write([]byte("hi"))
		}),
	)
	// drive one request through the middleware
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/widgets", nil))
	if rec.Code != http.StatusTeapot {
		t.Fatalf("status passthrough broke: %d", rec.Code)
	}

	// scrape /metrics
	mrec := httptest.NewRecorder()
	reg.Handler().ServeHTTP(mrec, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	body, _ := io.ReadAll(mrec.Result().Body)
	text := string(body)
	for _, want := range []string{
		`http_requests_total{`, `service="test-service"`, `route="/widgets"`,
		`status="418"`, `http_request_duration_seconds_bucket`,
	} {
		if !strings.Contains(text, want) {
			t.Fatalf("metrics output missing %q\n---\n%s", want, text)
		}
	}
}
