package domain

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"
)

// URN is a parsed Windrose resource name (MASTER-FR-013):
// wr:<tenant_id>:<service>:<resource_type>/<resource_id>
type URN struct {
	TenantID     string
	Service      string
	ResourceType string
	ResourceID   string
}

func (u URN) String() string {
	return fmt.Sprintf("wr:%s:%s:%s/%s", u.TenantID, u.Service, u.ResourceType, u.ResourceID)
}

// ParseURN validates and parses a resource URN.
func ParseURN(s string) (URN, error) {
	parts := strings.SplitN(s, ":", 4)
	if len(parts) != 4 || parts[0] != "wr" {
		return URN{}, fmt.Errorf("invalid urn %q: want wr:<tenant>:<service>:<type>/<id>", s)
	}
	rp := strings.SplitN(parts[3], "/", 2)
	if len(rp) != 2 || rp[0] == "" || rp[1] == "" || parts[1] == "" || parts[2] == "" {
		return URN{}, fmt.Errorf("invalid urn %q: want wr:<tenant>:<service>:<type>/<id>", s)
	}
	return URN{TenantID: parts[1], Service: parts[2], ResourceType: rp[0], ResourceID: rp[1]}, nil
}

// URNHash is the stable hash used in projection keys
// (perm:{tenant}:{user}:res:{urn_hash}); first 32 hex chars of SHA-256.
func URNHash(urn string) string {
	h := sha256.Sum256([]byte(urn))
	return hex.EncodeToString(h[:])[:32]
}
