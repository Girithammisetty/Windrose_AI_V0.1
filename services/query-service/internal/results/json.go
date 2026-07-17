// Package results is the result plane (QRY-FR-060..063): a chunked,
// tenant-prefixed part store with a bounded write buffer, stable pagination
// cursors over sealed parts (BR-9), the uniform edge JSON type mapping
// (QRY-FR-063), CSV export with signed URLs (QRY-FR-062) and retention GC.
//
// DEVIATION (documented in README): parts are length-bounded JSONL chunks
// rather than Apache Arrow IPC files. The streaming semantics the BRD
// requires — bounded service memory, chunked persistence, stable cursors,
// results decoupled from engines (BR-14) — are preserved; swapping the part
// codec for Arrow is localized to this package.
package results

import (
	"encoding/base64"
	"fmt"
	"math"
	"strconv"
	"time"
)

// Edge warnings attached per column (QRY-FR-063).
const WarnNaNReplaced = "non_finite_float_replaced_with_null"

// MapValue converts one canonical engine value into its edge JSON
// representation (QRY-FR-063). warning is non-empty when a lossy
// substitution occurred (NaN/±Inf → null; V1 clients silently saw 0).
func MapValue(v any, logicalType string) (out any, warning string) {
	switch t := v.(type) {
	case nil:
		return nil, ""
	case bool:
		return t, ""
	case int64:
		// int64/uint64: number if |v| < 2^53, else string (lossless).
		if t > 1<<53-1 || t < -(1<<53-1) {
			return strconv.FormatInt(t, 10), ""
		}
		return t, ""
	case float64:
		if math.IsNaN(t) || math.IsInf(t, 0) {
			return nil, WarnNaNReplaced
		}
		return t, ""
	case string:
		return t, ""
	case []byte:
		return base64.StdEncoding.EncodeToString(t), ""
	case time.Time:
		if logicalType == "date" {
			return t.UTC().Format("2006-01-02"), ""
		}
		return t.UTC().Format(time.RFC3339Nano), ""
	case []any:
		outArr := make([]any, len(t))
		var w string
		for i, el := range t {
			var ew string
			outArr[i], ew = MapValue(el, "")
			if w == "" {
				w = ew
			}
		}
		return outArr, w
	case map[string]any:
		outMap := make(map[string]any, len(t))
		var w string
		for k, el := range t {
			var ew string
			outMap[k], ew = MapValue(el, "")
			if w == "" {
				w = ew
			}
		}
		return outMap, w
	default:
		return fmt.Sprint(v), ""
	}
}

// Cursor addresses a row inside sealed parts: stable until result GC
// (BR-9).
type Cursor struct {
	Part int
	Row  int
}

// Encode renders the cursor opaque (MASTER-FR-022).
func (c Cursor) Encode() string {
	return base64.RawURLEncoding.EncodeToString([]byte(fmt.Sprintf("p%d:r%d", c.Part, c.Row)))
}

// DecodeCursor parses an opaque cursor.
func DecodeCursor(s string) (Cursor, error) {
	if s == "" {
		return Cursor{}, nil
	}
	b, err := base64.RawURLEncoding.DecodeString(s)
	if err != nil {
		return Cursor{}, fmt.Errorf("invalid cursor")
	}
	var c Cursor
	if _, err := fmt.Sscanf(string(b), "p%d:r%d", &c.Part, &c.Row); err != nil {
		return Cursor{}, fmt.Errorf("invalid cursor")
	}
	if c.Part < 0 || c.Row < 0 {
		return Cursor{}, fmt.Errorf("invalid cursor")
	}
	return c, nil
}
