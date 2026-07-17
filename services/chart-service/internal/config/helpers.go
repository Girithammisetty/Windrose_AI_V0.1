package config

import (
	"fmt"

	"github.com/windrose-ai/go-common/authjwt"
)

func sprintfType(v any) string { return fmt.Sprintf("%T", v) }

// verifierMode reports the verifier type; BuildCore always uses the real JWKS
// verifier (production path).
func verifierMode(v *authjwt.Verifier) string {
	if v == nil {
		return "<nil>"
	}
	return "authjwt.Verifier(JWKS)"
}
