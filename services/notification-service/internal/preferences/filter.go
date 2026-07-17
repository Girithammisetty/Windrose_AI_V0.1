// Package preferences applies per-user delivery preferences (NOTIF-FR-012):
// mutes, per-event-type channel overrides, quiet hours, and digest opt-in.
// Preferences win over rules except critical-class events, which cannot be
// muted on the in-app channel and bypass quiet hours (BR-3).
package preferences

import (
	"time"

	"github.com/windrose-ai/notification-service/internal/domain"
)

// Decision is the resolved per-channel delivery plan for one recipient.
type Decision struct {
	Channels     []string  // channels to deliver on after preference filtering
	Digest       bool      // route via digest buffer (NOTIF-FR-012 opt-in)
	DeferEmailTo time.Time // non-zero → send email at this time (quiet hours)
}

// Resolve computes the delivery decision for a user given their preferences,
// the default channels from the mapping, the event type and severity class.
func Resolve(prefs *domain.UserPreferences, defaultChannels []string, eventType, severityClass, resourceURN, eventClass string, now time.Time) Decision {
	critical := severityClass == domain.SeverityCritical

	// Channel override per event type wins over defaults.
	channels := defaultChannels
	if prefs != nil {
		if ov, ok := prefs.ChannelOverride[eventType]; ok {
			channels = ov
		}
	}

	// Mutes: drop channels for muted event types / resource URNs. Critical
	// cannot be muted on in-app (BR-3).
	if prefs != nil && isMuted(prefs.Mutes, eventType, resourceURN) {
		var kept []string
		for _, ch := range channels {
			if ch == domain.ChannelInApp && critical {
				kept = append(kept, ch) // critical in-app cannot be muted
			}
		}
		channels = kept
	}

	d := Decision{Channels: channels}

	// Digest opt-in per event class (NOTIF-FR-012). Critical never digests.
	if prefs != nil && !critical {
		if _, ok := prefs.DigestConfig[eventClass]; ok {
			d.Digest = true
		}
	}

	// Quiet hours defer email to window end (except critical, BR-3).
	if prefs != nil && prefs.QuietHours != nil && !critical {
		if end, in := quietUntil(prefs.QuietHours, now); in {
			d.DeferEmailTo = end
		}
	}
	return d
}

func isMuted(m domain.Mutes, eventType, resourceURN string) bool {
	for _, t := range m.EventTypes {
		if t == eventType {
			return true
		}
	}
	for _, u := range m.ResourceURNs {
		if u == resourceURN {
			return true
		}
	}
	return false
}

// quietUntil reports whether now falls inside the user's quiet window and, if
// so, the local time the window ends. Handles windows crossing midnight.
func quietUntil(q *domain.QuietHours, now time.Time) (time.Time, bool) {
	loc, err := time.LoadLocation(q.TZ)
	if err != nil {
		return time.Time{}, false
	}
	local := now.In(loc)
	start, err1 := parseHM(q.Start)
	end, err2 := parseHM(q.End)
	if err1 != nil || err2 != nil {
		return time.Time{}, false
	}
	mins := local.Hour()*60 + local.Minute()
	startM := start.h*60 + start.m
	endM := end.h*60 + end.m

	endToday := time.Date(local.Year(), local.Month(), local.Day(), end.h, end.m, 0, 0, loc)
	if startM <= endM {
		// same-day window (e.g. 01:00–05:00)
		if mins >= startM && mins < endM {
			return endToday, true
		}
		return time.Time{}, false
	}
	// crosses midnight (e.g. 22:00–07:00)
	if mins >= startM {
		return endToday.Add(24 * time.Hour), true
	}
	if mins < endM {
		return endToday, true
	}
	return time.Time{}, false
}

type hm struct{ h, m int }

func parseHM(s string) (hm, error) {
	t, err := time.Parse("15:04", s)
	if err != nil {
		return hm{}, err
	}
	return hm{t.Hour(), t.Minute()}, nil
}
