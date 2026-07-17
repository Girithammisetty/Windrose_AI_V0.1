// Package seed embeds the reviewed default role -> action matrix
// (RBC-FR-024, seed/roles_actions.yaml).
package seed

import _ "embed"

//go:embed roles_actions.yaml
var RolesActionsYAML []byte
