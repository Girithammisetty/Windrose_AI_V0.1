package httpx

import (
	"encoding/base64"
	"errors"
	"strconv"

	"github.com/google/uuid"
)

// Pagination per MASTER-FR-022: cursor-based, default limit 50, max 200.
const (
	DefaultPageLimit = 50
	MaxPageLimit     = 200
)

// ErrBadLimit / ErrBadCursor are returned by ParsePage for invalid inputs so
// the caller can map them onto its own error envelope.
var (
	ErrBadLimit  = errors.New("invalid limit: must be a positive integer")
	ErrBadCursor = errors.New("invalid cursor: malformed")
)

// PageRequest is a parsed cursor + limit.
type PageRequest struct {
	Limit int
	// AfterID: return items with id > AfterID. uuidv7 ids are time-ordered
	// (MASTER-FR-021) so id-ordering equals creation-ordering.
	AfterID *uuid.UUID
}

// PageInfo is the response page envelope (MASTER-FR-022).
type PageInfo struct {
	NextCursor *string `json:"next_cursor"`
	HasMore    bool    `json:"has_more"`
}

// ParsePage validates ?limit= and ?cursor= values.
func ParsePage(limitStr, cursor string) (PageRequest, error) {
	pr := PageRequest{Limit: DefaultPageLimit}
	if limitStr != "" {
		n, err := strconv.Atoi(limitStr)
		if err != nil || n < 1 {
			return pr, ErrBadLimit
		}
		if n > MaxPageLimit {
			n = MaxPageLimit
		}
		pr.Limit = n
	}
	if cursor != "" {
		raw, err := base64.RawURLEncoding.DecodeString(cursor)
		if err != nil {
			return pr, ErrBadCursor
		}
		id, err := uuid.Parse(string(raw))
		if err != nil {
			return pr, ErrBadCursor
		}
		pr.AfterID = &id
	}
	return pr, nil
}

// EncodeCursor builds an opaque cursor from the last item's id.
func EncodeCursor(lastID uuid.UUID) string {
	return base64.RawURLEncoding.EncodeToString([]byte(lastID.String()))
}

// BuildPage trims an over-fetched slice (fetched with limit+1) and returns the
// page info. idOf must return the item's uuid.
func BuildPage[T any](items []T, limit int, idOf func(T) uuid.UUID) ([]T, PageInfo) {
	if len(items) > limit {
		items = items[:limit]
		c := EncodeCursor(idOf(items[len(items)-1]))
		return items, PageInfo{NextCursor: &c, HasMore: true}
	}
	return items, PageInfo{HasMore: false}
}
