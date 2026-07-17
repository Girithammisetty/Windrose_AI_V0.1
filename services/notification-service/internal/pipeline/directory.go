package pipeline

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/windrose-ai/go-common/redisx"
)

// UserInfo is the recipient contact info resolved for delivery.
type UserInfo struct {
	Email  string
	Locale string
	TZ     string
}

// UserDirectory resolves a user's email/locale/TZ (identity-service data,
// cached, NOTIF-FR-020/§8). The runtime adapter reads the Redis projection
// identity-service populates.
//
// Error contract (so the pipeline can act honestly on a lookup outcome):
//   - a non-nil error means a TRANSIENT failure (e.g. Redis down): the caller
//     must retry/DLQ, never drop or fabricate.
//   - a nil error with a NON-EMPTY UserInfo.Email means a resolved recipient.
//   - a nil error with an EMPTY UserInfo.Email means a genuine directory miss
//     ("no email on file"): the caller skips the email channel and counts it —
//     it must NOT invent an address (a fabricated `<id>@windrose.local` silently
//     mis-delivers or bounces).
type UserDirectory interface {
	Lookup(ctx context.Context, tenant, userID string) (UserInfo, error)
}

// RedisUserDirectory reads `notif:user:<tenant>:<user_id>` → {email,locale,tz}.
type RedisUserDirectory struct {
	R *redisx.Client
}

// NewRedisUserDirectory builds the real directory.
func NewRedisUserDirectory(r *redisx.Client) *RedisUserDirectory {
	return &RedisUserDirectory{R: r}
}

// Lookup resolves contact info. It returns an error only on a transient Redis
// failure; a genuine miss (key absent or no email on file) yields a UserInfo
// with an empty Email and no error, so the pipeline skips the email channel
// rather than fabricating an address.
func (d *RedisUserDirectory) Lookup(ctx context.Context, tenant, userID string) (UserInfo, error) {
	info := UserInfo{Locale: "en", TZ: "UTC"}
	raw, ok, err := d.R.Get(ctx, fmt.Sprintf("notif:user:%s:%s", tenant, userID))
	if err != nil {
		// Transient store error: surface it so delivery is retried/DLQ'd. Do NOT
		// fall back to a derived address (that would mask an outage as a bad send).
		return UserInfo{}, fmt.Errorf("directory lookup for %s/%s: %w", tenant, userID, err)
	}
	if !ok {
		return info, nil // genuine miss: no contact record → empty Email, no error
	}
	var u UserInfo
	if json.Unmarshal([]byte(raw), &u) != nil {
		return info, nil // corrupt record treated as a miss (no email on file)
	}
	if u.Locale == "" {
		u.Locale = "en"
	}
	if u.TZ == "" {
		u.TZ = "UTC"
	}
	return u, nil // Email may be empty here → treated as "no email on file"
}
