package preferences

import (
	"testing"
	"time"

	"github.com/windrose-ai/notification-service/internal/domain"
)

// TestAC13_QuietHoursDeferEmail proves an info email in the quiet window defers
// to the window end, while a critical event bypasses quiet hours (AC-13, BR-3).
func TestAC13_QuietHoursDeferEmail(t *testing.T) {
	prefs := &domain.UserPreferences{
		QuietHours: &domain.QuietHours{TZ: "UTC", Start: "22:00", End: "07:00"},
	}
	at23 := time.Date(2026, 7, 10, 23, 0, 0, 0, time.UTC)

	info := Resolve(prefs, []string{"email"}, "case.assigned", domain.SeverityInfo, "wr:x", domain.SeverityInfo, at23)
	if info.DeferEmailTo.IsZero() {
		t.Fatal("info email at 23:00 should be deferred")
	}
	if got := info.DeferEmailTo.Hour(); got != 7 {
		t.Fatalf("expected deferral to 07:00, got hour %d", got)
	}

	crit := Resolve(prefs, []string{"email"}, "case.sla.breached", domain.SeverityCritical, "wr:x", domain.SeverityCritical, at23)
	if !crit.DeferEmailTo.IsZero() {
		t.Fatal("critical event must bypass quiet hours")
	}
}

// TestBR3_CriticalCannotBeMutedInApp proves critical in-app survives a mute.
func TestBR3_CriticalCannotBeMutedInApp(t *testing.T) {
	prefs := &domain.UserPreferences{Mutes: domain.Mutes{EventTypes: []string{"case.sla.breached"}}}
	dec := Resolve(prefs, []string{"in_app", "email"}, "case.sla.breached", domain.SeverityCritical, "wr:x", domain.SeverityCritical, time.Now())
	hasInApp := false
	for _, c := range dec.Channels {
		if c == domain.ChannelInApp {
			hasInApp = true
		}
		if c == domain.ChannelEmail {
			t.Error("muted email channel should be dropped")
		}
	}
	if !hasInApp {
		t.Error("critical in-app cannot be muted")
	}
}

func TestChannelOverrideAndDigestOptIn(t *testing.T) {
	prefs := &domain.UserPreferences{
		ChannelOverride: map[string][]string{"case.comment.added": {"in_app"}},
		DigestConfig:    map[string]string{"info": "1h"},
	}
	dec := Resolve(prefs, []string{"in_app", "email"}, "case.comment.added", domain.SeverityInfo, "wr:x", "info", time.Now())
	if len(dec.Channels) != 1 || dec.Channels[0] != "in_app" {
		t.Fatalf("channel override not applied: %v", dec.Channels)
	}
	if !dec.Digest {
		t.Fatal("info digest opt-in should route to digest")
	}
}
