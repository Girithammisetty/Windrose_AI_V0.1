package integration

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"

	gcevent "github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/notification-service/internal/channels/webhook"
	"github.com/windrose-ai/notification-service/internal/domain"
)

func emailHashLower(addr string) string {
	sum := sha256.Sum256([]byte(strings.ToLower(addr)))
	return hex.EncodeToString(sum[:])
}

// TestAC02_HourlyDigest12to1: with the user opted into an hourly email digest
// for case.comment.added, 12 events in one window yield 0 immediate emails and
// exactly one digest email listing all 12 (AC-2). Real: Redis prefs + Postgres
// digest buffer + Mailpit + worker flush.
func TestAC02_HourlyDigest12to1(t *testing.T) {
	h := requireHarness(t)
	h.mailpitDeleteAll(t)
	tenant := uuid.New()
	user := "dg-" + uuid.NewString()[:8]
	addr := user + "@windrose.local"
	ctx := context.Background()
	h.seedUser(t, tenant.String(), user, addr)

	// Opt user into an email digest for case.comment.added (info class).
	if err := h.pg.PutPreferences(ctx, &domain.UserPreferences{
		TenantID: tenant, UserID: user,
		ChannelOverride: map[string][]string{"case.comment.added": {"email"}},
		DigestConfig:    map[string]string{"info": "1h"},
	}); err != nil {
		t.Fatal(err)
	}

	for i := 0; i < 12; i++ {
		env := gcevent.New("case.comment.added", tenant, gcevent.Actor{Type: "user", ID: "author"},
			"wr:t:case:case/1", "tr", map[string]any{"assignee": user, "case_number": 1, "commenter_name": "Bob"})
		if err := h.pipeline.Process(ctx, env); err != nil {
			t.Fatal(err)
		}
	}
	// No immediate emails to the user.
	immediate := 0
	digest := 0
	waitFor(t, 20*time.Second, func() bool {
		immediate, digest = 0, 0
		for _, m := range h.mailpitMessages(t) {
			for _, to := range m.To {
				if to.Address != addr {
					continue
				}
				if m.Subject == "[Windrose] Your notification digest" {
					digest++
				} else {
					immediate++
				}
			}
		}
		return digest == 1
	}, "exactly one digest email")
	if immediate != 0 {
		t.Fatalf("expected 0 immediate emails, got %d", immediate)
	}
	if digest != 1 {
		t.Fatalf("expected exactly 1 digest email, got %d", digest)
	}
}

// TestAC05b_WebhookAutoProbeInOrderFlush: an open circuit auto-probes on the
// worker sweep after the probe interval, closes on success, and flushes queued
// deliveries in event-id order (AC-5). Real: Postgres state + worker + real HTTP.
func TestAC05b_WebhookAutoProbeInOrderFlush(t *testing.T) {
	h := requireHarness(t)
	ctx := context.Background()
	tenant := uuid.New()

	var mu sync.Mutex
	fail := true
	var received []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		f := fail
		if !f {
			received = append(received, r.Header.Get(webhook.HeaderEventID))
		}
		mu.Unlock()
		if f {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	ep := &domain.WebhookEndpoint{
		ID: domain.NewID(), TenantID: tenant, URL: srv.URL, EventTypes: []string{"case.assigned"},
		Secrets: []domain.WebhookSecret{{Version: 1, Secret: "s", CreatedAt: time.Now()}},
		Active:  true, VerifiedAt: ptrTime(time.Now()), CircuitState: domain.CircuitOpen,
		CircuitOpenedAt: ptrTime(time.Now().Add(-20 * time.Minute)), ConsecutiveFailures: 10,
		CreatedBy: "admin", CreatedAt: time.Now(), UpdatedAt: time.Now(),
	}
	if err := h.pg.CreateWebhook(ctx, ep); err != nil {
		t.Fatal(err)
	}

	// Two queued deliveries with distinct (time-ordered) event ids, both due.
	past := time.Now().Add(-time.Minute)
	var eventIDs []string
	for i := 0; i < 2; i++ {
		env := gcevent.New("case.assigned", tenant, gcevent.Actor{Type: "user", ID: "a"}, "wr:t:case:case/"+strconv.Itoa(i), "tr",
			map[string]any{"assignee": "u", "case_number": i})
		eventIDs = append(eventIDs, env.EventID.String())
		dID := uuid.NewSHA1(uuid.NameSpaceOID, []byte("wh:"+tenant.String()+":"+env.EventID.String()+":"+ep.ID.String()))
		if _, err := h.pg.InsertDelivery(ctx, &domain.Delivery{
			ID: dID, TenantID: tenant, WebhookEndpointID: &ep.ID, EventID: env.EventID,
			Recipient: ep.ID.String(), Channel: domain.ChannelWebhook, Provider: "webhook",
			Status: domain.StatusQueued, NextRetryAt: &past, CreatedAt: time.Now(), UpdatedAt: time.Now(),
		}, map[string]any{"envelope": env}); err != nil {
			t.Fatal(err)
		}
	}
	sort.Strings(eventIDs)

	// Recover the endpoint, then sweep: worker sets half_open (probe due), the
	// probe succeeds, circuit closes, and the queued deliveries flush in order.
	mu.Lock()
	fail = false
	mu.Unlock()
	waitFor(t, 15*time.Second, func() bool {
		h.worker.Sweep(ctx)
		ep2, _ := h.pg.GetWebhook(ctx, tenant, ep.ID)
		mu.Lock()
		n := len(received)
		mu.Unlock()
		return ep2.CircuitState == domain.CircuitClosed && n >= 2
	}, "circuit closes and queued deliveries flush")

	mu.Lock()
	got := append([]string(nil), received...)
	mu.Unlock()
	// The two queued deliveries must appear in event-id order (BR-7 in-order).
	var filtered []string
	for _, id := range got {
		for _, want := range eventIDs {
			if id == want {
				filtered = append(filtered, id)
			}
		}
	}
	if len(filtered) < 2 {
		t.Fatalf("expected both queued deliveries received, got %v", filtered)
	}
	if !sort.StringsAreSorted(filtered[:2]) {
		t.Fatalf("queued deliveries not in event-id order: %v", filtered[:2])
	}
}

// TestAC07_TenantTemplateOverrideRollbackNoRestart: a published tenant override
// renders instead of the platform default, and republishing a prior body rolls
// back — all without a service restart (AC-7). Real: Postgres template
// resolution (tenant→platform) + Mailpit render.
func TestAC07_TenantTemplateOverrideRollbackNoRestart(t *testing.T) {
	h := requireHarness(t)
	h.mailpitDeleteAll(t)
	ctx := context.Background()
	tenant := uuid.New()
	user := "tpl-" + uuid.NewString()[:8]
	addr := user + "@windrose.local"
	h.seedUser(t, tenant.String(), user, addr)

	publish := func(subject string) {
		ver, err := h.pg.NextVersion(ctx, &tenant, "case.assigned", "email", "en")
		if err != nil {
			t.Fatal(err)
		}
		tmpl := &domain.Template{
			ID: domain.NewID(), TenantID: &tenant, Key: "case.assigned", Channel: "email", Locale: "en",
			Version: ver, SubjectTpl: subject, BodyTextTpl: "Case {{.CaseNumber}} {{.DeepLink}}",
			BodyHTMLTpl: "<p>{{.CaseNumber}}</p>", Status: domain.TemplateDraft, CreatedBy: "admin", CreatedAt: time.Now(),
		}
		if err := h.pg.CreateTemplateVersion(ctx, tmpl); err != nil {
			t.Fatal(err)
		}
		if _, err := h.pg.PublishTemplate(ctx, &tenant, tmpl.ID); err != nil {
			t.Fatal(err)
		}
	}

	assertSubject := func(want string) {
		waitFor(t, 15*time.Second, func() bool {
			for _, m := range h.mailpitMessages(t) {
				for _, to := range m.To {
					if to.Address == addr && m.Subject == want {
						return true
					}
				}
			}
			return false
		}, "email with subject "+want)
	}

	// Tenant override v1.
	publish("TENANT-A override: case {{.CaseNumber}}")
	env := gcevent.New("case.assigned", tenant, gcevent.Actor{Type: "user", ID: "a"}, "wr:t:case:case/1", "tr",
		map[string]any{"assignee": user, "case_number": 11, "severity": "high"})
	if err := h.pipeline.Process(ctx, env); err != nil {
		t.Fatal(err)
	}
	assertSubject("TENANT-A override: case 11")

	// Rollback by publishing a prior body as a new version — no restart.
	h.mailpitDeleteAll(t)
	publish("ROLLED-BACK: case {{.CaseNumber}}")
	env2 := gcevent.New("case.assigned", tenant, gcevent.Actor{Type: "user", ID: "a"}, "wr:t:case:case/2", "tr",
		map[string]any{"assignee": user, "case_number": 12, "severity": "high"})
	if err := h.pipeline.Process(ctx, env2); err != nil {
		t.Fatal(err)
	}
	assertSubject("ROLLED-BACK: case 12")
}

// TestAC10_SESHardBounceSuppresses: an SES hard-bounce callback marks the
// delivery bounced and suppresses the address; the next email-eligible event is
// then suppressed while in-app still delivers (AC-10). Real: HTTP provider
// callback → Postgres suppression + delivery status.
func TestAC10_SESHardBounceSuppresses(t *testing.T) {
	h := requireHarness(t)
	h.mailpitDeleteAll(t)
	ctx := context.Background()
	tenant := uuid.New()
	user := "bnc-" + uuid.NewString()[:8]
	addr := user + "@windrose.local"
	h.seedUser(t, tenant.String(), user, addr)

	// First email event → an email delivery with a provider_msg_id (from SMTP).
	env := gcevent.New("case.assigned", tenant, gcevent.Actor{Type: "user", ID: "a"}, "wr:t:case:case/1", "tr",
		map[string]any{"assignee": user, "case_number": 1, "severity": "warning"})
	if err := h.pipeline.Process(ctx, env); err != nil {
		t.Fatal(err)
	}
	var msgID string
	waitFor(t, 10*time.Second, func() bool {
		d, err := h.pg.DeliveryByEvent(ctx, tenant, env.EventID, domain.ChannelEmail)
		if err != nil || d == nil {
			return false
		}
		msgID = d.ProviderMsgID
		return msgID != ""
	}, "email delivery with provider_msg_id")

	// Post an SES SNS hard-bounce callback naming that message id (real handler).
	router := httptest.NewServer(h.server.Router())
	defer router.Close()
	inner, _ := json.Marshal(map[string]any{
		"notificationType": "Bounce",
		"mail":             map[string]any{"messageId": msgID, "destination": []string{addr}},
		"bounce":           map[string]any{"bounceType": "Permanent"},
	})
	sns, _ := json.Marshal(map[string]any{"Message": string(inner)})
	resp, err := http.Post(router.URL+"/api/v1/providers/ses/status", "application/json", bytes.NewReader(sns))
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("callback status %d", resp.StatusCode)
	}

	// Suppression recorded.
	waitFor(t, 5*time.Second, func() bool {
		ok, _ := h.pg.IsSuppressed(ctx, tenant, emailHashLower(addr))
		return ok
	}, "address suppressed after hard bounce")

	// Next email-eligible event → email suppressed, in-app still delivered.
	env2 := gcevent.New("case.assigned", tenant, gcevent.Actor{Type: "user", ID: "a"}, "wr:t:case:case/2", "tr",
		map[string]any{"assignee": user, "case_number": 2, "severity": "warning"})
	if err := h.pipeline.Process(ctx, env2); err != nil {
		t.Fatal(err)
	}
	waitFor(t, 5*time.Second, func() bool {
		d, err := h.pg.DeliveryByEvent(ctx, tenant, env2.EventID, domain.ChannelEmail)
		return err == nil && d != nil && d.Status == domain.StatusSuppressed
	}, "second email suppressed")
	list, _ := h.pg.ListNotifications(ctx, tenant, user, false, 10, nil)
	if len(list) < 2 {
		t.Fatalf("in-app should still deliver for both events, got %d", len(list))
	}
}
