package fanout

import (
	"context"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
)

// Replay is the per-(tenant, topic) resume buffer backed by real Redis Streams
// (RTH-FR-031): last 1,000 events / 10 minutes. Reconnect with Last-Event-ID
// replays everything after that id in order; an aged-out id yields a reset.
type Replay struct {
	rdb    redis.UniversalClient
	maxLen int64
	window time.Duration
}

// NewReplay builds a replay buffer.
func NewReplay(rdb redis.UniversalClient) *Replay {
	return &Replay{rdb: rdb, maxLen: 1000, window: 10 * time.Minute}
}

func replayKey(tenant, topic string) string { return "rt:rp:" + tenant + "/" + topic }
func seenKey(eventID string) string          { return "rt:seen:" + eventID }

// Append writes an event to the ring buffer with XADD MAXLEN dedup by event_id
// (RTH-FR-042 / AC-16). It returns fresh=false when the event_id was already
// buffered (producer retry via a second source), so the caller can also skip
// the live publish and deliver exactly once.
func (r *Replay) Append(ctx context.Context, tenant, topic string, ev Event) (fresh bool, err error) {
	if ev.ID == "" {
		return true, nil // control/ephemeral: never buffered
	}
	// Dedup gate: first writer of this event_id wins.
	ok, err := r.rdb.SetNX(ctx, seenKey(ev.ID), 1, r.window).Result()
	if err != nil {
		return false, err
	}
	if !ok {
		return false, nil
	}
	key := replayKey(tenant, topic)
	if err := r.rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: key,
		MaxLen: r.maxLen,
		Approx: true,
		Values: map[string]any{"event_id": ev.ID, "data": string(ev.Data), "chat": boolStr(ev.Chat)},
	}).Err(); err != nil {
		return false, err
	}
	// Bound age to the window (RTH-FR-031).
	_ = r.rdb.Expire(ctx, key, r.window).Err()
	return true, nil
}

// replayFreshSubscribe reports whether a topic scheme should deliver its full
// current ring-buffer content to a genuinely FRESH subscriber (no
// Last-Event-ID at all), rather than the default "live-tail only" behavior.
// chat/agent_run topics are short-lived, few-event, per-session/per-run
// streams with no REST side channel to "catch up" on missed history — the
// token/run_completed/done sequence IS the only delivery path (ART-FR-070/071,
// docstring: "a client that subscribes a beat after the run started — the
// copilot always does — still receives it"). Long-lived state topics
// (run-status/notifications/proposal) intentionally keep live-tail-only
// semantics on fresh subscribe: their current state is fetched via REST, and
// replaying potentially thousands of accumulated historical events on every
// page load would be wrong, not just unnecessary.
func replayFreshSubscribe(topic string) bool {
	return strings.HasPrefix(topic, "chat:") || strings.HasPrefix(topic, "agent_run:")
}

// Resume returns the events after lastEventID in order and whether a reset is
// required (the id aged out of the window, RTH-FR-031 / AC-4). When
// lastEventID is empty, the default is no replay (fresh subscribe, live-tail
// only) — EXCEPT for chat/agent_run topics (see replayFreshSubscribe), which
// treat an empty lastEventID as "replay everything currently retained", since
// a fresh subscribe is the copilot's normal, expected first connection, not a
// resume-after-drop.
func (r *Replay) Resume(ctx context.Context, tenant, topic, lastEventID string) (evs []Event, reset bool, err error) {
	freshWantsReplay := lastEventID == "" && replayFreshSubscribe(topic)
	if lastEventID == "" && !freshWantsReplay {
		return nil, false, nil
	}
	entries, err := r.rdb.XRange(ctx, replayKey(tenant, topic), "-", "+").Result()
	if err != nil {
		return nil, false, err
	}
	if len(entries) == 0 {
		if freshWantsReplay {
			// Nothing published yet for this brand-new run — not an error, not
			// a gap, just "no history to catch up on."
			return nil, false, nil
		}
		// Nothing retained: cannot prove the gap is recoverable → reset.
		return nil, true, nil
	}
	if freshWantsReplay {
		for _, e := range entries {
			id, _ := e.Values["event_id"].(string)
			data, _ := e.Values["data"].(string)
			chat, _ := e.Values["chat"].(string)
			evs = append(evs, Event{ID: id, Topic: topic, Data: []byte(data), Chat: chat == "1"})
		}
		return evs, false, nil
	}
	oldest, _ := entries[0].Values["event_id"].(string)
	found := false
	for _, e := range entries {
		id, _ := e.Values["event_id"].(string)
		if id == lastEventID {
			found = true
			continue
		}
		// uuidv7 ids are lexicographically time-ordered, so string compare
		// selects the strictly-later events (RTH-FR-004/032).
		if id > lastEventID {
			data, _ := e.Values["data"].(string)
			chat, _ := e.Values["chat"].(string)
			evs = append(evs, Event{ID: id, Topic: topic, Data: []byte(data), Chat: chat == "1"})
		}
	}
	// Aged out: the client's position predates the oldest retained event and is
	// not itself retained → unrecoverable gap → reset (client REST-refreshes).
	if !found && lastEventID < oldest {
		return nil, true, nil
	}
	return evs, false, nil
}

func boolStr(b bool) string {
	if b {
		return "1"
	}
	return "0"
}
