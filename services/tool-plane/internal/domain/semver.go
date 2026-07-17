package domain

import (
	"fmt"
	"strconv"
	"strings"
)

// SemVer is a minimal semantic version (major.minor.patch) — enough for the
// registry's version ordering, deprecation, and range pinning (TPL-FR-002/BR-4).
type SemVer struct {
	Major, Minor, Patch int
}

// ParseSemVer parses "1.2.0" style versions (no pre-release/build metadata).
func ParseSemVer(s string) (SemVer, error) {
	parts := strings.Split(s, ".")
	if len(parts) != 3 {
		return SemVer{}, fmt.Errorf("invalid semver %q: want major.minor.patch", s)
	}
	var v SemVer
	var err error
	if v.Major, err = strconv.Atoi(parts[0]); err != nil {
		return SemVer{}, fmt.Errorf("invalid major in %q", s)
	}
	if v.Minor, err = strconv.Atoi(parts[1]); err != nil {
		return SemVer{}, fmt.Errorf("invalid minor in %q", s)
	}
	if v.Patch, err = strconv.Atoi(parts[2]); err != nil {
		return SemVer{}, fmt.Errorf("invalid patch in %q", s)
	}
	return v, nil
}

// Compare returns -1/0/1.
func (v SemVer) Compare(o SemVer) int {
	if v.Major != o.Major {
		return cmpInt(v.Major, o.Major)
	}
	if v.Minor != o.Minor {
		return cmpInt(v.Minor, o.Minor)
	}
	return cmpInt(v.Patch, o.Patch)
}

func (v SemVer) String() string {
	return fmt.Sprintf("%d.%d.%d", v.Major, v.Minor, v.Patch)
}

func cmpInt(a, b int) int {
	if a < b {
		return -1
	}
	if a > b {
		return 1
	}
	return 0
}

// SatisfiesCaret reports whether version v satisfies a "^X.Y.Z" range (BR-4
// toolset pinning): same major, and v >= X.Y.Z.
func SatisfiesCaret(constraint, version string) bool {
	constraint = strings.TrimPrefix(constraint, "^")
	c, err := ParseSemVer(constraint)
	if err != nil {
		return constraint == version
	}
	v, err := ParseSemVer(version)
	if err != nil {
		return false
	}
	return v.Major == c.Major && v.Compare(c) >= 0
}
