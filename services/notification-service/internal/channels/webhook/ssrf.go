package webhook

import (
	"fmt"
	"net"
	"net/url"
)

// GuardURL enforces the SSRF policy (BR-6, AC-12): https only, and no target
// that resolves to a private, loopback, link-local or metadata IP. Resolution
// happens here and again at send time (DNS may change between registration and
// delivery). Returns the resolved IPs on success.
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
	// allowHTTP is the explicit dev/e2e escape (WEBHOOK_ALLOW_HTTP): it permits
	// http:// AND local targets (e.g. httptest 127.0.0.1) so the webhook path is
	// exercisable end-to-end. In production allowHTTP is false and the full SSRF
	// policy applies (BR-6, AC-12).
	if !allowHTTP {
		for _, ip := range ips {
			if isForbiddenIP(ip) {
				return nil, fmt.Errorf("target resolves to a forbidden address: %s", ip)
			}
		}
	}
	return ips, nil
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
