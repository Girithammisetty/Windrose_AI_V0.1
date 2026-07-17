package fanout

import (
	"context"
	"encoding/json"
	"log/slog"
	"sync"

	"github.com/redis/go-redis/v9"
)

// channelFor is the Redis pub/sub channel for a tenant-scoped topic
// (RTH-FR-041). Live fan-out to every pod flows through here.
func channelFor(tenant, topic string) string { return "rt:ch:" + tenant + "/" + topic }

// RedisBus is the sticky-less cross-pod fan-out transport (RTH-FR-041): pods
// PUBLISH routed/published events and SUBSCRIBE to the channels they have local
// connections on. This is the only fan-out path to connections, so a Kafka
// event routed on one pod reaches a client on any other pod through real Redis.
type RedisBus struct {
	rdb    redis.UniversalClient
	log    *slog.Logger
	onMsg  func(tenant, topic string, ev Event)

	mu     sync.Mutex
	ps     *redis.PubSub
	refs   map[string]int // channel -> subscriber refcount
	ctx    context.Context
	cancel context.CancelFunc
}

// NewRedisBus builds a bus over a go-redis client. onMsg is invoked for every
// received live event (the hub delivers it to local connections).
func NewRedisBus(rdb redis.UniversalClient, log *slog.Logger, onMsg func(tenant, topic string, ev Event)) *RedisBus {
	ctx, cancel := context.WithCancel(context.Background())
	b := &RedisBus{rdb: rdb, log: log, onMsg: onMsg, refs: map[string]int{}, ctx: ctx, cancel: cancel}
	b.ps = rdb.Subscribe(ctx) // channels added lazily via Subscribe()
	go b.receive()
	return b
}

// wireMsg is the pub/sub payload: the live event plus its tenant/topic routing.
type wireMsg struct {
	Tenant string          `json:"tenant"`
	Topic  string          `json:"topic"`
	ID     string          `json:"id"`
	Data   json.RawMessage `json:"data"`
	Chat   bool            `json:"chat"`
}

// Publish sends one live event to all pods subscribed to (tenant, topic).
func (b *RedisBus) Publish(ctx context.Context, tenant, topic string, ev Event) error {
	raw, err := json.Marshal(wireMsg{Tenant: tenant, Topic: topic, ID: ev.ID, Data: ev.Data, Chat: ev.Chat})
	if err != nil {
		return err
	}
	return b.rdb.Publish(ctx, channelFor(tenant, topic), raw).Err()
}

// Subscribe ensures this pod receives live events for (tenant, topic). Refcounted
// so many local connections share one Redis subscription.
func (b *RedisBus) Subscribe(tenant, topic string) {
	ch := channelFor(tenant, topic)
	b.mu.Lock()
	defer b.mu.Unlock()
	b.refs[ch]++
	if b.refs[ch] == 1 {
		if err := b.ps.Subscribe(b.ctx, ch); err != nil {
			b.log.Error("redis subscribe failed", "channel", ch, "err", err)
		}
	}
}

// Unsubscribe drops this pod's interest in (tenant, topic) when the last local
// connection leaves.
func (b *RedisBus) Unsubscribe(tenant, topic string) {
	ch := channelFor(tenant, topic)
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.refs[ch] == 0 {
		return
	}
	b.refs[ch]--
	if b.refs[ch] == 0 {
		delete(b.refs, ch)
		if err := b.ps.Unsubscribe(b.ctx, ch); err != nil {
			b.log.Error("redis unsubscribe failed", "channel", ch, "err", err)
		}
	}
}

func (b *RedisBus) receive() {
	ch := b.ps.Channel()
	for {
		select {
		case <-b.ctx.Done():
			return
		case m, ok := <-ch:
			if !ok {
				return
			}
			var w wireMsg
			if err := json.Unmarshal([]byte(m.Payload), &w); err != nil {
				b.log.Error("bad pub/sub payload", "err", err)
				continue
			}
			b.onMsg(w.Tenant, w.Topic, Event{ID: w.ID, Topic: w.Topic, Data: w.Data, Chat: w.Chat})
		}
	}
}

// Close stops the receive loop and closes the subscription.
func (b *RedisBus) Close() error {
	b.cancel()
	return b.ps.Close()
}
