package pipeline_test

import (
	"context"
	"net/http"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"

	gcevent "github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/notification-service/internal/channels/email"
	"github.com/windrose-ai/notification-service/internal/domain"
	"github.com/windrose-ai/notification-service/internal/pipeline"
	"github.com/windrose-ai/notification-service/internal/registry"
)

// ---- fakes (unit-test doubles only; never reachable from cmd/server) --------

type fakeStore struct {
	mu        sync.Mutex
	notifs    []*domain.Notification
	delivKey  map[string]bool
	delivs    []*domain.Delivery
	digests   map[string]int
	rules     []*domain.SubscriptionRule
	webhooks  []*domain.WebhookEndpoint
	prefs     map[string]*domain.UserPreferences
	tmpl      map[string]*domain.Template
	markedDue map[string]bool
}

func newFakeStore() *fakeStore {
	return &fakeStore{delivKey: map[string]bool{}, digests: map[string]int{}, prefs: map[string]*domain.UserPreferences{}, tmpl: map[string]*domain.Template{}, markedDue: map[string]bool{}}
}

func dk(t, e uuid.UUID, r, c string) string { return t.String() + e.String() + r + c }

func (f *fakeStore) ActiveRulesForEvent(_ context.Context, _ uuid.UUID) ([]*domain.SubscriptionRule, error) {
	return f.rules, nil
}
func (f *fakeStore) GetPreferences(_ context.Context, tenant uuid.UUID, user string) (*domain.UserPreferences, error) {
	if p, ok := f.prefs[user]; ok {
		return p, nil
	}
	return &domain.UserPreferences{TenantID: tenant, UserID: user, ChannelOverride: map[string][]string{}, DigestConfig: map[string]string{}}, nil
}
func (f *fakeStore) IsSuppressed(context.Context, uuid.UUID, string) (bool, error) { return false, nil }
func (f *fakeStore) ResolveTemplate(_ context.Context, _ uuid.UUID, key, channel, _ string) (*domain.Template, error) {
	if t, ok := f.tmpl[key+channel]; ok {
		return t, nil
	}
	return nil, nil
}
func (f *fakeStore) InsertNotificationTx(_ context.Context, n *domain.Notification, d *domain.Delivery, _ map[string]any, _ []gcevent.Envelope) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	k := dk(d.TenantID, d.EventID, d.Recipient, d.Channel)
	if f.delivKey[k] {
		return false, nil
	}
	f.delivKey[k] = true
	f.notifs = append(f.notifs, n)
	f.delivs = append(f.delivs, d)
	return true, nil
}
func (f *fakeStore) InsertDelivery(_ context.Context, d *domain.Delivery, _ map[string]any) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	k := dk(d.TenantID, d.EventID, d.Recipient, d.Channel)
	if f.delivKey[k] {
		return false, nil
	}
	f.delivKey[k] = true
	f.delivs = append(f.delivs, d)
	return true, nil
}
func (f *fakeStore) UpdateDeliveryStatus(context.Context, uuid.UUID, uuid.UUID, string, string, string, int, *time.Time, []gcevent.Envelope) error {
	return nil
}
func (f *fakeStore) AppendDigest(_ context.Context, _ uuid.UUID, user, channel, class string, _ domain.DigestItem, _ time.Time) (int, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.digests[user+channel+class]++
	return f.digests[user+channel+class], nil
}
func (f *fakeStore) MarkDigestDue(_ context.Context, _ uuid.UUID, user, channel, class string, _ time.Time) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.markedDue[user+channel+class] = true
	return nil
}
func (f *fakeStore) ActiveWebhooksForEvent(context.Context, uuid.UUID, string) ([]*domain.WebhookEndpoint, error) {
	return f.webhooks, nil
}
func (f *fakeStore) CountInAppToday(context.Context, uuid.UUID, string) (int, error) { return 0, nil }
func (f *fakeStore) UpdateWebhook(context.Context, *domain.WebhookEndpoint) error    { return nil }
func (f *fakeStore) EmitAudit(context.Context, gcevent.Envelope) error               { return nil }

func (f *fakeStore) countChannel(ch string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n := 0
	for _, d := range f.delivs {
		if d.Channel == ch {
			n++
		}
	}
	return n
}

type fakeGroups struct{}

func (fakeGroups) Role(context.Context, string, string, registry.AudienceRole) ([]string, error) {
	return nil, nil
}
func (fakeGroups) Group(context.Context, string, string) ([]string, error) { return nil, nil }

type fakeDir struct{}

func (fakeDir) Lookup(_ context.Context, _ string, user string) (pipeline.UserInfo, error) {
	return pipeline.UserInfo{Email: user + "@example.test", Locale: "en", TZ: "UTC"}, nil
}

type fakeRealtime struct {
	mu    sync.Mutex
	count int
}

func (r *fakeRealtime) Push(context.Context, string, string, map[string]any) error {
	r.mu.Lock()
	r.count++
	r.mu.Unlock()
	return nil
}

type fakeLimiter struct{ allowEmail bool }

func (l fakeLimiter) AllowEmail(context.Context, string, string) (bool, error) {
	return l.allowEmail, nil
}
func (l fakeLimiter) AllowTenantEmail(context.Context, string) (bool, error) { return true, nil }
func (l fakeLimiter) AllowWebhook(context.Context, string) (bool, error)     { return true, nil }

type captureProvider struct {
	mu   sync.Mutex
	msgs []email.Message
}

func (c *captureProvider) Name() string { return "capture" }
func (c *captureProvider) Send(_ context.Context, m email.Message) (string, error) {
	c.mu.Lock()
	c.msgs = append(c.msgs, m)
	c.mu.Unlock()
	return "cap-1", nil
}
func (c *captureProvider) ParseStatusCallback(*http.Request) ([]email.StatusUpdate, error) {
	return nil, nil
}

func newPipeline(fs *fakeStore, rt *fakeRealtime, cap *captureProvider, allowEmail bool) *pipeline.Pipeline {
	return &pipeline.Pipeline{
		Store: fs, Registry: registry.Default(), Groups: fakeGroups{}, Dir: fakeDir{},
		Email: email.NewSender(cap), Realtime: rt, Limiter: fakeLimiter{allowEmail: allowEmail},
	}
}

func assignedEvent() gcevent.Envelope {
	return gcevent.Envelope{
		EventID: domain.NewID(), EventType: "case.assigned", TenantID: uuid.New(),
		ResourceURN: "wr:t-1:case:case/1",
		Payload:     map[string]any{"assignee": "u-analyst", "case_number": 42, "severity": "high"},
	}
}

// TestAC01_CaseAssignedInAppAndEmail: case.assigned → in-app persisted + realtime
// push + email captured (AC-1).
func TestAC01_CaseAssignedInAppAndEmail(t *testing.T) {
	fs, rt, cap := newFakeStore(), &fakeRealtime{}, &captureProvider{}
	pl := newPipeline(fs, rt, cap, true)
	if err := pl.Process(context.Background(), assignedEvent()); err != nil {
		t.Fatal(err)
	}
	if len(fs.notifs) != 1 {
		t.Fatalf("expected 1 in-app notification, got %d", len(fs.notifs))
	}
	if rt.count != 1 {
		t.Fatalf("expected 1 realtime push, got %d", rt.count)
	}
	if len(cap.msgs) != 1 {
		t.Fatalf("expected 1 captured email, got %d", len(cap.msgs))
	}
	if cap.msgs[0].To != "u-analyst@example.test" {
		t.Fatalf("unexpected recipient %s", cap.msgs[0].To)
	}
}

// TestAC03_ExactlyOnceDedup: redelivering the same event_id yields exactly one
// in-app notification and one email (AC-3, BR-1).
func TestAC03_ExactlyOnceDedup(t *testing.T) {
	fs, rt, cap := newFakeStore(), &fakeRealtime{}, &captureProvider{}
	pl := newPipeline(fs, rt, cap, true)
	ev := assignedEvent()
	_ = pl.Process(context.Background(), ev)
	_ = pl.Process(context.Background(), ev) // redelivery
	if len(fs.notifs) != 1 {
		t.Fatalf("dedup failed: %d notifications", len(fs.notifs))
	}
	if got := fs.countChannel(domain.ChannelEmail); got != 1 {
		t.Fatalf("dedup failed: %d email deliveries", got)
	}
}

// TestAC09_RateLimitToDigest: when the email rate limit is exhausted the send
// converts to the digest path (never dropped) (AC-9, BR-2).
func TestAC09_RateLimitToDigest(t *testing.T) {
	fs, rt, cap := newFakeStore(), &fakeRealtime{}, &captureProvider{}
	pl := newPipeline(fs, rt, cap, false) // limiter denies immediate email
	if err := pl.Process(context.Background(), assignedEvent()); err != nil {
		t.Fatal(err)
	}
	if len(cap.msgs) != 0 {
		t.Fatalf("expected no immediate email under rate limit, got %d", len(cap.msgs))
	}
	total := 0
	for _, n := range fs.digests {
		total += n
	}
	if total != 1 {
		t.Fatalf("expected 1 digest-buffered item, got %d", total)
	}
}

// TestNOTIF_FR030_EarlyFlushAt200: a >200-item burst in one window marks the
// digest buffer due immediately rather than waiting for the window (NOTIF-FR-030).
func TestNOTIF_FR030_EarlyFlushAt200(t *testing.T) {
	fs, rt, cap := newFakeStore(), &fakeRealtime{}, &captureProvider{}
	pl := newPipeline(fs, rt, cap, true)
	pl.DigestWindow = time.Hour // long window: only the 200-cap can early-flush
	tenant := uuid.New()
	user := "burst-user"
	// case.comment.added is digestible+info; opt the user into email digests.
	fs.prefs[user] = &domain.UserPreferences{
		TenantID: tenant, UserID: user,
		ChannelOverride: map[string][]string{"case.comment.added": {"email"}},
		DigestConfig:    map[string]string{"info": "1h"},
	}
	key := user + domain.ChannelEmail + "info"
	for i := 0; i < 199; i++ {
		ev := gcevent.New("case.comment.added", tenant, gcevent.Actor{Type: "user", ID: "a"}, "wr:t:case:case/x", "tr",
			map[string]any{"assignee": user, "case_number": i})
		_ = pl.Process(context.Background(), ev)
	}
	if fs.markedDue[key] {
		t.Fatal("should not be marked due before 200 items")
	}
	ev := gcevent.New("case.comment.added", tenant, gcevent.Actor{Type: "user", ID: "a"}, "wr:t:case:case/x", "tr",
		map[string]any{"assignee": user, "case_number": 200})
	_ = pl.Process(context.Background(), ev)
	if !fs.markedDue[key] {
		t.Fatalf("buffer should be marked due at 200 items (count=%d)", fs.digests[key])
	}
}

// TestUnmappedEventIgnored: an event with no mapping produces nothing.
func TestUnmappedEventIgnored(t *testing.T) {
	fs, rt, cap := newFakeStore(), &fakeRealtime{}, &captureProvider{}
	pl := newPipeline(fs, rt, cap, true)
	ev := gcevent.Envelope{EventID: domain.NewID(), EventType: "nonexistent.event", TenantID: uuid.New()}
	if err := pl.Process(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if len(fs.notifs) != 0 || len(cap.msgs) != 0 {
		t.Fatal("unmapped event should produce nothing")
	}
}
