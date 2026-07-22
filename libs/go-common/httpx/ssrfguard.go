package httpx

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"time"
)

// GuardURL enforces an SSRF policy for any outbound webhook-style delivery a
// service makes to a customer-supplied destination URL: https only, and no
// target that resolves to a private, loopback, link-local, multicast or cloud
// metadata IP. Resolution happens here (and should happen again at send time,
// since DNS may change between registration and delivery — a DNS-rebind
// TOCTOU risk, see BRD 58 SEC-5). Returns the resolved IPs on success.
//
// Originally notification-service's webhook SSRF guard (BR-6, AC-12);
// extracted here (BRD 59 WS2) so audit-service's per-tenant SIEM export
// delivery enforces the identical policy rather than a second, potentially
// divergent implementation of security-sensitive code.
func GuardURL(raw string, allowHTTP bool) ([]net.IP, error) {
	u, err := url.Parse(raw)
	if err != nil {
		return nil, fmt.Errorf("invalid url: %w", err)
	}
	if u.Scheme != "https" && !(allowHTTP && u.Scheme == "http") {
		return nil, fmt.Errorf("scheme must be https")
	}
	host := u.Hostname()
	if host == "" {
		return nil, fmt.Errorf("missing host")
	}
	ips, err := net.LookupIP(host)
	if err != nil {
		return nil, fmt.Errorf("dns resolve failed: %w", err)
	}
	// allowHTTP is the explicit dev/e2e escape: it permits http:// AND local
	// targets (e.g. httptest 127.0.0.1) so the delivery path is exercisable
	// end to end. In production allowHTTP is false and the full policy applies.
	if !allowHTTP {
		for _, ip := range ips {
			if isForbiddenIP(ip) {
				return nil, fmt.Errorf("target resolves to a forbidden address: %s", ip)
			}
		}
	}
	return ips, nil
}

// PinnedClient returns an http.Client whose DialContext ignores whatever
// hostname the outgoing request carries and connects ONLY to the first IP in
// ips — the address GuardURL already validated. This closes the DNS-rebind
// TOCTOU gap: without it, GuardURL's decision and the eventual TCP connect
// are two separate DNS resolutions, and an attacker who controls the target
// hostname's DNS (or exploits a short TTL) can pass the guard against a public
// IP and have the real dial land on a private/internal address instead.
// Every GuardURL caller that goes on to make the actual HTTP request MUST
// build its client through this helper, not a plain &http.Client{} — ported
// from notification-service's original webhook sender (BR-6, AC-12), the one
// caller that already did this correctly, so the fix lives in one place
// instead of being re-invented per caller (BRD 58 SEC-5).
func PinnedClient(ips []net.IP, timeout time.Duration) *http.Client {
	pinned := ""
	if len(ips) > 0 {
		pinned = ips[0].String()
	}
	dialer := &net.Dialer{Timeout: timeout}
	transport := &http.Transport{
		Proxy: http.ProxyFromEnvironment,
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			_, port, err := net.SplitHostPort(addr)
			if err != nil {
				return nil, err
			}
			if pinned == "" {
				return nil, fmt.Errorf("no validated address to dial")
			}
			return dialer.DialContext(ctx, network, net.JoinHostPort(pinned, port))
		},
		TLSHandshakeTimeout:   timeout,
		ResponseHeaderTimeout: timeout,
	}
	return &http.Client{
		Timeout:   timeout,
		Transport: transport,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
}

// isForbiddenIP blocks loopback, private, link-local and cloud metadata ranges.
func isForbiddenIP(ip net.IP) bool {
	if ip.IsLoopback() || ip.IsUnspecified() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() || ip.IsMulticast() {
		return true
	}
	if ip4 := ip.To4(); ip4 != nil {
		// RFC1918 + 169.254/16 (link-local, incl. 169.254.169.254 metadata).
		switch {
		case ip4[0] == 10:
			return true
		case ip4[0] == 172 && ip4[1] >= 16 && ip4[1] <= 31:
			return true
		case ip4[0] == 192 && ip4[1] == 168:
			return true
		case ip4[0] == 169 && ip4[1] == 254:
			return true
		case ip4[0] == 127:
			return true
		}
		return false
	}
	// IPv6 unique-local fc00::/7.
	if len(ip) == net.IPv6len && (ip[0]&0xfe) == 0xfc {
		return true
	}
	return false
}
