package domain

// Level->verb mapping, fixed platform-wide (RBC-FR-030):
//
//	viewer: read, list, export
//	editor: viewer + update, execute, share (share limited to viewer level,
//	        enforced at grant-creation time)
//	owner:  editor + delete, admin (share at any level)
var levelVerbs = map[GrantLevel]map[string]bool{
	LevelViewer: {
		VerbRead: true, VerbList: true, VerbExport: true,
	},
	LevelEditor: {
		VerbRead: true, VerbList: true, VerbExport: true,
		VerbUpdate: true, VerbExecute: true, VerbShare: true,
	},
	LevelOwner: {
		VerbRead: true, VerbList: true, VerbExport: true,
		VerbUpdate: true, VerbExecute: true, VerbShare: true,
		VerbDelete: true, VerbAdmin: true,
	},
}

// VerbsForLevel returns the set of verbs a grant level allows.
func VerbsForLevel(l GrantLevel) map[string]bool {
	out := make(map[string]bool, len(levelVerbs[l]))
	for v := range levelVerbs[l] {
		out[v] = true
	}
	return out
}

// LevelAllowsVerb reports whether a grant at level l permits the given verb.
func LevelAllowsVerb(l GrantLevel, verb string) bool {
	return levelVerbs[l][verb]
}

// levelRank orders the lattice viewer < editor < owner.
func levelRank(l GrantLevel) int {
	switch l {
	case LevelViewer:
		return 1
	case LevelEditor:
		return 2
	case LevelOwner:
		return 3
	}
	return 0
}

// MaxLevel returns the higher of two levels.
func MaxLevel(a, b GrantLevel) GrantLevel {
	if levelRank(a) >= levelRank(b) {
		return a
	}
	return b
}

// LevelAtLeast reports whether a >= b in the level lattice.
func LevelAtLeast(a, b GrantLevel) bool {
	return levelRank(a) >= levelRank(b)
}

// ArchivedReadVerbs are the only verbs permitted against archived workspaces
// (RBC-FR-004 / AC-14): reads by previously-assigned users still allow.
var ArchivedReadVerbs = map[string]bool{
	VerbRead: true, VerbList: true, VerbExport: true,
}
