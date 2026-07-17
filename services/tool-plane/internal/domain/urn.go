package domain

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"sort"
	"strings"
)

// ToolURN builds the tool resource URN (MASTER-FR-013):
// wr:<tenant>:tool-plane:tool/<tool_id>@<version>.
func ToolURN(tenant, toolID, version string) string {
	return "wr:" + tenant + ":tool-plane:tool/" + toolID + "@" + version
}

// URNTenant extracts the tenant segment of a wr: URN, or "" if malformed.
// URN shape: wr:<tenant>:<service>:<resource_type>/<resource_id>.
func URNTenant(urn string) string {
	parts := strings.SplitN(urn, ":", 4)
	if len(parts) < 4 || parts[0] != "wr" {
		return ""
	}
	return parts[1]
}

// IsURN reports whether s looks like a windrose URN.
func IsURN(s string) bool {
	return strings.HasPrefix(s, "wr:") && strings.Count(s, ":") >= 3
}

// ArgsDigest is SHA-256 over canonical JSON of args (BR-3, MASTER-FR-042). Raw
// arg values never appear in ai.tool_invoked.v1 — only this digest.
func ArgsDigest(args map[string]any) string {
	canon := canonicalJSON(args)
	sum := sha256.Sum256(canon)
	return hex.EncodeToString(sum[:])
}

// canonicalJSON produces deterministic JSON with sorted object keys.
func canonicalJSON(v any) []byte {
	switch t := v.(type) {
	case map[string]any:
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		var b strings.Builder
		b.WriteByte('{')
		for i, k := range keys {
			if i > 0 {
				b.WriteByte(',')
			}
			kb, _ := json.Marshal(k)
			b.Write(kb)
			b.WriteByte(':')
			b.Write(canonicalJSON(t[k]))
		}
		b.WriteByte('}')
		return []byte(b.String())
	case []any:
		var b strings.Builder
		b.WriteByte('[')
		for i, e := range t {
			if i > 0 {
				b.WriteByte(',')
			}
			b.Write(canonicalJSON(e))
		}
		b.WriteByte(']')
		return []byte(b.String())
	default:
		out, _ := json.Marshal(v)
		return out
	}
}
