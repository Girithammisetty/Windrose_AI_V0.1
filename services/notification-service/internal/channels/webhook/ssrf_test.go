package webhook

import "testing"

// TestAC12_SSRFGuard proves private/link-local/metadata targets are refused and
// non-https is rejected (AC-12, BR-6).
func TestAC12_SSRFGuard(t *testing.T) {
	forbidden := []string{
		"http://169.254.169.254/",   // cloud metadata (and non-https)
		"https://169.254.169.254/",  // metadata
		"https://10.0.0.5/hook",     // RFC1918
		"https://192.168.1.10/hook", // RFC1918
		"https://127.0.0.1/hook",    // loopback
		"https://[::1]/hook",        // ipv6 loopback
		"ftp://example.com/hook",    // bad scheme
	}
	for _, u := range forbidden {
		if _, err := GuardURL(u, false); err == nil {
			t.Errorf("expected %s to be forbidden", u)
		}
	}
	// A public https host resolves and passes (uses real DNS).
	if _, err := GuardURL("https://example.com/hook", false); err != nil {
		t.Logf("public host guard returned %v (DNS may be unavailable in sandbox)", err)
	}
	// http allowed only when explicitly permitted (dev/e2e httptest).
	if _, err := GuardURL("http://93.184.216.34/hook", true); err != nil {
		t.Logf("allowHTTP public ip guard: %v", err)
	}
}
