package domain

import (
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
	"golang.org/x/crypto/argon2"
)

// MaxServiceAccountsPerTenant per IDN-FR-031.
const MaxServiceAccountsPerTenant = 20

// RotationOverlap: after rotation the old secret keeps working for this long
// (IDN-FR-033 create-new + deprecate-old with overlap).
const RotationOverlap = 5 * time.Minute

// ServiceAccount is a tenant-facing API key (IDN-FR-031). Tenant-scoped, RLS.
type ServiceAccount struct {
	ID                 uuid.UUID  `json:"id"`
	TenantID           uuid.UUID  `json:"tenant_id"`
	Name               string     `json:"name"`
	SecretHash         string     `json:"-"`
	OldSecretHash      *string    `json:"-"`
	OldSecretExpiresAt *time.Time `json:"old_secret_expires_at,omitempty"`
	Scopes             []string   `json:"scopes"`
	ExpiresAt          *time.Time `json:"expires_at,omitempty"`
	LastUsedAt         *time.Time `json:"last_used_at,omitempty"`
	RevokedAt          *time.Time `json:"revoked_at,omitempty"`
	CreatedAt          time.Time  `json:"created_at"`
	UpdatedAt          time.Time  `json:"updated_at"`
}

func (s *ServiceAccount) URN() string { return URN(s.TenantID, "service_account", s.ID.String()) }

// argon2id parameters (OWASP baseline).
const (
	argonTime    = 1
	argonMemory  = 64 * 1024
	argonThreads = 4
	argonKeyLen  = 32
	argonSaltLen = 16
)

// NewAPIKeySecret generates the secret half of an API key.
func NewAPIKeySecret() (string, error) {
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(buf), nil
}

// FormatAPIKey renders the shown-once key: wr_sa_<id>.<secret> (IDN-FR-031).
func FormatAPIKey(id uuid.UUID, secret string) string {
	return "wr_sa_" + id.String() + "." + secret
}

// ParseAPIKey splits a presented key into service-account id and secret.
func ParseAPIKey(key string) (uuid.UUID, string, error) {
	if !strings.HasPrefix(key, "wr_sa_") {
		return uuid.Nil, "", EUnauthenticated("malformed api key")
	}
	rest := strings.TrimPrefix(key, "wr_sa_")
	idStr, secret, ok := strings.Cut(rest, ".")
	if !ok || secret == "" {
		return uuid.Nil, "", EUnauthenticated("malformed api key")
	}
	id, err := uuid.Parse(idStr)
	if err != nil {
		return uuid.Nil, "", EUnauthenticated("malformed api key")
	}
	return id, secret, nil
}

// HashSecret hashes an API-key secret with argon2id, PHC-formatted.
func HashSecret(secret string) (string, error) {
	salt := make([]byte, argonSaltLen)
	if _, err := rand.Read(salt); err != nil {
		return "", err
	}
	key := argon2.IDKey([]byte(secret), salt, argonTime, argonMemory, argonThreads, argonKeyLen)
	return fmt.Sprintf("$argon2id$v=19$m=%d,t=%d,p=%d$%s$%s",
		argonMemory, argonTime, argonThreads,
		base64.RawStdEncoding.EncodeToString(salt),
		base64.RawStdEncoding.EncodeToString(key)), nil
}

// VerifySecret constant-time-compares a candidate secret against a PHC hash.
func VerifySecret(secret, phc string) bool {
	parts := strings.Split(phc, "$")
	if len(parts) != 6 || parts[1] != "argon2id" {
		return false
	}
	var m uint32
	var t uint32
	var p uint8
	if _, err := fmt.Sscanf(parts[3], "m=%d,t=%d,p=%d", &m, &t, &p); err != nil {
		return false
	}
	salt, err := base64.RawStdEncoding.DecodeString(parts[4])
	if err != nil {
		return false
	}
	want, err := base64.RawStdEncoding.DecodeString(parts[5])
	if err != nil {
		return false
	}
	got := argon2.IDKey([]byte(secret), salt, t, m, p, uint32(len(want)))
	return subtle.ConstantTimeCompare(got, want) == 1
}

// VerifyPresentedSecret checks the current hash and, within the rotation
// overlap window, the previous hash (IDN-FR-033).
func (s *ServiceAccount) VerifyPresentedSecret(secret string, now time.Time) bool {
	if VerifySecret(secret, s.SecretHash) {
		return true
	}
	if s.OldSecretHash != nil && s.OldSecretExpiresAt != nil && now.Before(*s.OldSecretExpiresAt) {
		return VerifySecret(secret, *s.OldSecretHash)
	}
	return false
}

// Denylist is the API-key revocation denylist checked at the edge
// (IDN-FR-033: in-memory implementation now, Redis adapter later; <=5s propagation).
type Denylist interface {
	Revoke(id string)
	IsRevoked(id string) bool
}
