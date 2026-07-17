// Package inapp is the in-app notification channel's realtime push. The
// notification row is persisted by the store; this publishes a live event to
// realtime-hub so the client badge/toast updates (NOTIF-FR-020). realtime-hub
// fans out over Redis pub/sub on channel rt:ch:<tenant>/<topic>; this speaks
// that exact wire format against real Redis (RTH-FR-041) — no mock.
package inapp

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/windrose-ai/go-common/redisx"
)

// Publisher pushes in-app notifications to realtime-hub.
type Publisher interface {
	Push(ctx context.Context, tenant, userID string, payload map[string]any) error
}

// RedisPublisher publishes on realtime-hub's Redis backplane channel.
type RedisPublisher struct {
	R *redisx.Client
}

// NewRedisPublisher builds the real publisher.
func NewRedisPublisher(r *redisx.Client) *RedisPublisher { return &RedisPublisher{R: r} }

// wireMsg mirrors realtime-hub's fanout.wireMsg pub/sub payload.
type wireMsg struct {
	Tenant string          `json:"tenant"`
	Topic  string          `json:"topic"`
	ID     string          `json:"id"`
	Data   json.RawMessage `json:"data"`
	Chat   bool            `json:"chat"`
}

// Push publishes a notification event to topic notifications:<user_id> for the
// tenant, on realtime-hub's channel rt:ch:<tenant>/<topic>.
func (p *RedisPublisher) Push(ctx context.Context, tenant, userID string, payload map[string]any) error {
	topic := "notifications:" + userID
	data, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	id, _ := payload["id"].(string)
	msg, err := json.Marshal(wireMsg{Tenant: tenant, Topic: topic, ID: id, Data: data})
	if err != nil {
		return err
	}
	channel := fmt.Sprintf("rt:ch:%s/%s", tenant, topic)
	return p.R.Publish(ctx, channel, msg)
}

// Channel returns the realtime-hub Redis channel for a user (tests subscribe here).
func Channel(tenant, userID string) string {
	return fmt.Sprintf("rt:ch:%s/notifications:%s", tenant, userID)
}
