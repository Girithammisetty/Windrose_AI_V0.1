package webhook

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"strconv"
	"time"

	gcevent "github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/notification-service/internal/domain"
)

// Retry/circuit constants (NOTIF-FR-023).
var RetrySchedule = []time.Duration{
	time.Minute, 5 * time.Minute, 30 * time.Minute,
	2 * time.Hour, 6 * time.Hour, 24 * time.Hour,
}

const (
	// CircuitOpenThreshold consecutive failures opens the circuit.
	CircuitOpenThreshold = 10
	// ProbeInterval is how often a half-open probe fires.
	ProbeInterval = 15 * time.Minute
	// DisableAfter auto-disables an endpoint that stays open this long.
	DisableAfter = 72 * time.Hour
	// RequestTimeout bounds each delivery attempt (NFR: p95 ≤ 10s).
	RequestTimeout = 10 * time.Second
)

// Sender performs real signed HTTP POST deliveries. It refuses redirects and
// pins each connection to the SSRF-validated IP (BR-6). A fresh client per send
// carries the per-request pinned dialer.
type Sender struct {
	AllowHTTP bool // dev/test only: permit http:// targets (e.g. httptest server)
}

// NewSender builds a webhook sender.
func NewSender(allowHTTP bool) *Sender {
	return &Sender{AllowHTTP: allowHTTP}
}

// Deliver signs and POSTs the envelope to the endpoint. Success = a 2xx within
// the timeout. Returns the HTTP status (0 on transport error) and an error on
// non-2xx / transport / SSRF failure. The connection is PINNED to the IP the
// SSRF guard validated, so validation is atomic with the dial — a DNS-rebinding
// attacker cannot flip the name to a private IP between check and connect (BR-6).
func (s *Sender) Deliver(ctx context.Context, e *domain.WebhookEndpoint, env gcevent.Envelope, now time.Time) (int, error) {
	ips, err := GuardURL(e.URL, s.AllowHTTP)
	if err != nil {
		return 0, fmt.Errorf("ssrf guard: %w", err)
	}
	body, err := json.Marshal(env)
	if err != nil {
		return 0, err
	}
	ts := now.Unix()
	secrets := e.ActiveSecrets(now)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, e.URL, bytes.NewReader(body))
	if err != nil {
		return 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set(HeaderSignature, SignatureHeader(secrets, ts, body))
	req.Header.Set(HeaderTimestamp, strconv.FormatInt(ts, 10))
	req.Header.Set(HeaderEventID, env.EventID.String())
	req.Header.Set(HeaderEventType, env.EventType)

	resp, err := s.pinnedClient(ips).Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return resp.StatusCode, nil
	}
	return resp.StatusCode, fmt.Errorf("non-2xx status %d", resp.StatusCode)
}

// pinnedClient returns an http.Client whose DialContext ignores the request
// host's DNS and connects ONLY to the SSRF-validated IP (preserving the target
// port). This closes the TOCTOU/DNS-rebinding gap: the guard's decision and the
// TCP connection use the same address. Redirects are refused (BR-6).
func (s *Sender) pinnedClient(ips []net.IP) *http.Client {
	pinned := ""
	if len(ips) > 0 {
		pinned = ips[0].String()
	}
	dialer := &net.Dialer{Timeout: RequestTimeout}
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
		TLSHandshakeTimeout:   RequestTimeout,
		ResponseHeaderTimeout: RequestTimeout,
	}
	return &http.Client{
		Timeout:   RequestTimeout,
		Transport: transport,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
}

// NextRetryAt returns the next retry time for a given attempt count (1-based),
// ok=false when the schedule is exhausted (→ mark failed / dead-letter).
func NextRetryAt(now time.Time, attempts int) (time.Time, bool) {
	if attempts < 1 || attempts > len(RetrySchedule) {
		return time.Time{}, false
	}
	return now.Add(RetrySchedule[attempts-1]), true
}

// VerifyChallenge performs the registration handshake: POST a challenge, expect
// the endpoint to echo it back within the timeout (NOTIF-FR-022).
func (s *Sender) VerifyChallenge(ctx context.Context, url, challenge string) error {
	ips, err := GuardURL(url, s.AllowHTTP)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader([]byte(ChallengeBody(challenge))))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set(HeaderEventType, "endpoint.verify")
	resp, err := s.pinnedClient(ips).Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("challenge status %d", resp.StatusCode)
	}
	// Accept an echoed challenge either as a raw string or JSON {"challenge":...}.
	trimmed := string(bytes.TrimSpace(raw))
	if trimmed == challenge {
		return nil
	}
	var body struct {
		Challenge string `json:"challenge"`
	}
	if json.Unmarshal(raw, &body) == nil && body.Challenge == challenge {
		return nil
	}
	return fmt.Errorf("challenge not echoed")
}
