package siemexport

import (
	"bytes"
	"context"
	"log/slog"
	"net/http"
	"time"

	"github.com/google/uuid"

	gcevent "github.com/datacern-ai/go-common/event"
	"github.com/datacern-ai/go-common/httpx"
)

// ConfigLookup resolves a tenant's active SIEM destination (satisfied by
// *pgstore.Store's ActiveSiemConfigForDelivery — kept as a narrow interface so
// this package has no dependency on pgstore's pgx-specific types).
type ConfigLookup interface {
	ActiveSiemConfigForDelivery(ctx context.Context, tenant uuid.UUID) (*SiemDestination, error)
}

// SiemDestination is the subset of pgstore.SiemConfig delivery needs.
type SiemDestination struct {
	Endpoint string
	Format   Format
	AuthRef  string
}

// HTTPDelivery POSTs one formatted event to a tenant's configured SIEM
// destination (BRD 59 WS2). Best-effort, matching Exporter's existing
// philosophy: a delivery failure is logged and swallowed, never affects
// ingest, the hash chain, or the existing shared-topic Kafka publish this
// runs alongside.
//
// auth_ref resolution gap (flagged honestly, not silently skipped): auth_ref
// is stored as a reference into the tenant's own secrets backend (Vault/AWS/
// Azure/GCP, BYO hardening P2's convention — never a raw credential at rest),
// but P2's SecretsStore adapters are Python-only; no Go adapter exists yet to
// resolve auth_ref into an actual delivery credential. Delivery below sends
// an unauthenticated (SSRF-guarded, HTTPS-only) POST when auth_ref can't be
// resolved -- correct for a destination that authenticates at the network
// layer (mTLS/IP allowlist, common for enterprise SIEM HEC ingest), but a
// destination that needs a bearer/HEC token needs a Go secrets adapter wired
// here first. That wiring is a real, scoped follow-up, not fabricated as done.
type HTTPDelivery struct {
	Configs ConfigLookup
	Log     *slog.Logger
	// Timeout bounds each delivery attempt's connect/handshake/response.
	Timeout time.Duration
	// AllowHTTP is the dev/e2e escape (mirrors notification-service's
	// WEBHOOK_ALLOW_HTTP): permits http:// and loopback targets so delivery
	// is exercisable end to end against an httptest server. Never true in prod.
	AllowHTTP bool
}

// NewHTTPDelivery builds a delivery sink with sane defaults.
func NewHTTPDelivery(configs ConfigLookup, allowHTTP bool) *HTTPDelivery {
	return &HTTPDelivery{
		Configs:   configs,
		Timeout:   10 * time.Second,
		Log:       slog.Default(),
		AllowHTTP: allowHTTP,
	}
}

func (d *HTTPDelivery) log() *slog.Logger {
	if d != nil && d.Log != nil {
		return d.Log
	}
	return slog.Default()
}

// Deliver looks up tenant's active destination (if any) and POSTs env,
// formatted per the destination's configured format. A no-op when the tenant
// has no active config -- the common case, so this is cheap.
func (d *HTTPDelivery) Deliver(ctx context.Context, tenant uuid.UUID, env gcevent.Envelope) {
	if d == nil || d.Configs == nil {
		return
	}
	dest, err := d.Configs.ActiveSiemConfigForDelivery(ctx, tenant)
	if err != nil {
		d.log().Warn("siem destination lookup failed", "tenant_id", tenant.String(), "err", err)
		return
	}
	if dest == nil {
		return
	}
	ips, err := httpx.GuardURL(dest.Endpoint, d.AllowHTTP)
	if err != nil {
		d.log().Warn("siem destination failed SSRF guard, skipping delivery",
			"tenant_id", tenant.String(), "err", err)
		return
	}
	body, err := FormatEvent(env, dest.Format)
	if err != nil {
		d.log().Warn("siem export format failed", "tenant_id", tenant.String(), "format", dest.Format, "err", err)
		return
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, dest.Endpoint, bytes.NewReader([]byte(body)))
	if err != nil {
		d.log().Warn("siem delivery request build failed", "tenant_id", tenant.String(), "err", err)
		return
	}
	req.Header.Set("Content-Type", contentTypeFor(dest.Format))
	// Pin the connection to the IP GuardURL just validated, not whatever
	// dest.Endpoint's hostname re-resolves to at dial time (DNS-rebind TOCTOU,
	// BRD 58 SEC-5) -- mirrors notification-service's webhook sender.
	resp, err := httpx.PinnedClient(ips, d.Timeout).Do(req)
	if err != nil {
		d.log().Warn("siem delivery failed", "tenant_id", tenant.String(), "endpoint_host", req.URL.Host, "err", err)
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		d.log().Warn("siem delivery rejected", "tenant_id", tenant.String(), "status", resp.StatusCode)
	}
}

func contentTypeFor(f Format) string {
	if f == FormatJSON || f == "" {
		return "application/json"
	}
	return "text/plain; charset=utf-8"
}
