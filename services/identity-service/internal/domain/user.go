package domain

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"net/mail"
	"strings"
	"time"

	"github.com/google/uuid"
)

// UserStatus per IDN-FR-020.
type UserStatus string

const (
	UserInvited     UserStatus = "invited"
	UserActive      UserStatus = "active"
	UserDeactivated UserStatus = "deactivated"
)

// User is a tenant-scoped human identity (IDN-FR-020). RLS-protected.
type User struct {
	ID          uuid.UUID  `json:"id"`
	TenantID    uuid.UUID  `json:"tenant_id"`
	Email       string     `json:"email"`
	FullName    string     `json:"full_name"`
	Status      UserStatus `json:"status"`
	IdpSubject  *string    `json:"idp_subject,omitempty"` // Keycloak sub, linked on activation
	LastLoginAt *time.Time `json:"last_login_at,omitempty"`
	CreatedAt   time.Time  `json:"created_at"`
	UpdatedAt   time.Time  `json:"updated_at"`
	DeletedAt   *time.Time `json:"deleted_at,omitempty"`
}

func (u *User) URN() string { return URN(u.TenantID, "user", u.ID.String()) }

// ValidateEmail enforces RFC 5322 (IDN-FR-020; V1 had no validation).
func ValidateEmail(email string) (string, error) {
	email = strings.TrimSpace(email)
	addr, err := mail.ParseAddress(email)
	if err != nil || addr.Address != email || !strings.Contains(email, "@") {
		return "", EValidation("invalid email address", FieldError{Field: "email", Message: "must be a valid RFC 5322 address"})
	}
	return strings.ToLower(email), nil
}

// InvitationTTL per IDN-FR-021.
const InvitationTTL = 7 * 24 * time.Hour

// Invitation is a single-use activation token record (IDN-FR-021).
// Only the SHA-256 hash of the token is stored.
type Invitation struct {
	ID            uuid.UUID  `json:"id"`
	TenantID      uuid.UUID  `json:"tenant_id"`
	UserID        uuid.UUID  `json:"user_id"`
	TokenHash     string     `json:"-"`
	ExpiresAt     time.Time  `json:"expires_at"`
	AcceptedAt    *time.Time `json:"accepted_at,omitempty"`
	InvalidatedAt *time.Time `json:"invalidated_at,omitempty"`
	CreatedAt     time.Time  `json:"created_at"`
	UpdatedAt     time.Time  `json:"updated_at"`
}

// NewInvitationToken returns (plaintextToken, tokenHash). The plaintext is
// only ever placed in the user.invited outbox event for notification-service;
// it is never stored or returned by the API.
func NewInvitationToken() (string, string, error) {
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		return "", "", err
	}
	tok := base64.RawURLEncoding.EncodeToString(buf)
	return tok, HashInvitationToken(tok), nil
}

// HashInvitationToken hashes a plaintext invitation token for lookup.
func HashInvitationToken(tok string) string {
	sum := sha256.Sum256([]byte(tok))
	return hex.EncodeToString(sum[:])
}

// Usable reports whether the invitation can still be accepted at `now`.
// Returns a domain error explaining why not.
func (i *Invitation) Usable(now time.Time) error {
	if i.InvalidatedAt != nil {
		return ENotFound("invitation")
	}
	if i.AcceptedAt != nil {
		return EConflict("invitation already accepted")
	}
	if now.After(i.ExpiresAt) {
		return EInvitationExpired()
	}
	return nil
}
