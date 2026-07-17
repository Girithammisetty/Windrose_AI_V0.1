package store

import "strconv"

// Pagination bounds (MASTER-FR-022).
const (
	defaultLimit = 50
	maxLimit     = 200
)

func clampLimit(l int) int {
	if l <= 0 {
		return defaultLimit
	}
	if l > maxLimit {
		return maxLimit
	}
	return l
}

func itoa(i int) string { return strconv.Itoa(i) }
