package httpx

import (
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// TestPinnedClientDialsOnlyThePinnedIP proves the DialContext genuinely
// ignores the request's own host and connects to the pinned address -- the
// DNS-rebind TOCTOU fix (BRD 58 SEC-5). A request built against a hostname
// that resolves nowhere still succeeds when pinned to the real server's IP,
// and a request pinned to a bogus IP fails even though the request's declared
// host is the real, reachable server.
func TestPinnedClientDialsOnlyThePinnedIP(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	_, port, err := net.SplitHostPort(srv.Listener.Addr().String())
	if err != nil {
		t.Fatalf("split host port: %v", err)
	}

	t.Run("pinned to the real IP succeeds even with a bogus request host", func(t *testing.T) {
		req, _ := http.NewRequest(http.MethodGet, "http://this-hostname-does-not-resolve.invalid:"+port+"/", nil)
		client := PinnedClient([]net.IP{net.ParseIP("127.0.0.1")}, 2*time.Second)
		resp, err := client.Do(req)
		if err != nil {
			t.Fatalf("expected the pinned IP to be dialed regardless of request host, got: %v", err)
		}
		resp.Body.Close()
	})

	t.Run("pinned to a bogus IP fails even with the real, reachable request host", func(t *testing.T) {
		realHost := srv.Listener.Addr().String()
		req, _ := http.NewRequest(http.MethodGet, "http://"+realHost+"/", nil)
		// TEST-NET-1 (RFC 5737): reserved for documentation, never routable.
		client := PinnedClient([]net.IP{net.ParseIP("192.0.2.1")}, 500*time.Millisecond)
		if _, err := client.Do(req); err == nil {
			t.Fatal("expected the dial to the bogus pinned IP to fail, even though the request's own host is reachable")
		}
	})
}

func TestPinnedClientNoIPsRefusesToDial(t *testing.T) {
	req, _ := http.NewRequest(http.MethodGet, "http://example.com/", nil)
	client := PinnedClient(nil, time.Second)
	if _, err := client.Do(req); err == nil {
		t.Fatal("expected a client with no pinned IPs to refuse to dial")
	}
}
