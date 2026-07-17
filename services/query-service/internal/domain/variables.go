package domain

import (
	"encoding/json"
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

// VariableType is the closed set of declared variable types (QRY-FR-002).
type VariableType string

const (
	VarString      VariableType = "string"
	VarInteger     VariableType = "integer"
	VarDecimal     VariableType = "decimal"
	VarBoolean     VariableType = "boolean"
	VarDate        VariableType = "date"
	VarTimestamp   VariableType = "timestamp"
	VarStringList  VariableType = "string_list"
	VarIntegerList VariableType = "integer_list"
)

var knownVarTypes = map[VariableType]bool{
	VarString: true, VarInteger: true, VarDecimal: true, VarBoolean: true,
	VarDate: true, VarTimestamp: true, VarStringList: true, VarIntegerList: true,
}

// VariableDecl declares one typed variable (QRY-FR-002). SQL references it
// only as the named placeholder :name.
type VariableDecl struct {
	Name          string            `json:"name"`
	Type          VariableType      `json:"type"`
	Required      *bool             `json:"required,omitempty"` // default true
	Default       json.RawMessage   `json:"default,omitempty"`
	AllowedValues []json.RawMessage `json:"allowed_values,omitempty"`
	Min           *float64          `json:"min,omitempty"`
	Max           *float64          `json:"max,omitempty"`
}

// IsRequired applies the default-true rule (QRY-FR-002).
func (d VariableDecl) IsRequired() bool { return d.Required == nil || *d.Required }

var varNameRe = regexp.MustCompile(`^[a-zA-Z_][a-zA-Z0-9_]*$`)

// ValidateDecls checks a declaration list at save time (QRY-FR-002).
func ValidateDecls(decls []VariableDecl) error {
	var problems []VariableProblem
	seen := map[string]bool{}
	for _, d := range decls {
		switch {
		case !varNameRe.MatchString(d.Name):
			problems = append(problems, VariableProblem{Name: d.Name, Problem: "invalid variable name"})
		case seen[d.Name]:
			problems = append(problems, VariableProblem{Name: d.Name, Problem: "duplicate declaration"})
		case !knownVarTypes[d.Type]:
			problems = append(problems, VariableProblem{Name: d.Name, Problem: "unknown type " + string(d.Type)})
		default:
			if d.Default != nil {
				if _, err := coerce(d, d.Default); err != nil {
					problems = append(problems, VariableProblem{Name: d.Name, Problem: "default: " + err.Error()})
				}
			}
			for _, av := range d.AllowedValues {
				if _, err := coerce(d, av); err != nil {
					problems = append(problems, VariableProblem{Name: d.Name, Problem: "allowed_values: " + err.Error()})
				}
			}
		}
		seen[d.Name] = true
	}
	if len(problems) > 0 {
		return EVariableInvalid(problems)
	}
	return nil
}

// BoundValue is one validated, typed value ready to be passed to an engine
// as a bound parameter — never spliced into SQL text (QRY-FR-003, BR-1).
type BoundValue struct {
	Name    string
	Type    VariableType
	IsList  bool
	Value   any   // scalar driver value
	List    []any // list element driver values
	Display any   // JSON-representable form for history (pre-redaction)
}

// BindValues validates supplied values against declarations and returns the
// typed bindings. All problems are collected so the 422 lists every issue at
// once (QRY-FR-004, AC-4) — including missing required variables AND unknown
// extras in the same response.
func BindValues(decls []VariableDecl, values map[string]json.RawMessage) (map[string]BoundValue, error) {
	declared := map[string]VariableDecl{}
	for _, d := range decls {
		declared[d.Name] = d
	}
	var problems []VariableProblem
	bound := map[string]BoundValue{}

	// Unknown variables in the payload → 422 (QRY-FR-004).
	var extras []string
	for name := range values {
		if _, ok := declared[name]; !ok {
			extras = append(extras, name)
		}
	}
	sort.Strings(extras)
	for _, name := range extras {
		problems = append(problems, VariableProblem{Name: name, Problem: "not declared"})
	}

	for _, d := range decls {
		raw, supplied := values[d.Name]
		if !supplied || string(raw) == "null" {
			if d.Default != nil {
				raw = d.Default
			} else if d.IsRequired() {
				problems = append(problems, VariableProblem{Name: d.Name, Problem: "required variable missing"})
				continue
			} else {
				continue // optional, no default: placeholder must not appear unset — checked at rewrite
			}
		}
		bv, err := coerce(d, raw)
		if err != nil {
			problems = append(problems, VariableProblem{Name: d.Name, Problem: err.Error()})
			continue
		}
		bound[d.Name] = bv
	}
	if len(problems) > 0 {
		return nil, EVariableInvalid(problems)
	}
	return bound, nil
}

// coerce applies BR-3 strict coercion: no numeric-string→number, ISO-8601
// only for date/timestamp, allowed_values and min/max enforced pre-bind.
func coerce(d VariableDecl, raw json.RawMessage) (BoundValue, error) {
	bv := BoundValue{Name: d.Name, Type: d.Type}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return bv, fmt.Errorf("invalid JSON value")
	}
	scalar := func(val any, display any) { bv.Value, bv.Display = val, display }
	switch d.Type {
	case VarString:
		s, ok := v.(string)
		if !ok {
			return bv, fmt.Errorf("expected string, got %s", jsonKind(v))
		}
		scalar(s, s)
	case VarInteger:
		n, err := asInt64(v)
		if err != nil {
			return bv, err
		}
		scalar(n, n)
	case VarDecimal:
		switch t := v.(type) {
		case json.Number:
			scalar(t.String(), t.String())
		case string:
			if !decimalRe.MatchString(t) {
				return bv, fmt.Errorf("expected decimal, got non-numeric string")
			}
			scalar(t, t)
		default:
			return bv, fmt.Errorf("expected decimal number, got %s", jsonKind(v))
		}
	case VarBoolean:
		b, ok := v.(bool)
		if !ok {
			return bv, fmt.Errorf("expected boolean, got %s", jsonKind(v))
		}
		scalar(b, b)
	case VarDate:
		s, ok := v.(string)
		if !ok {
			return bv, fmt.Errorf("expected ISO-8601 date string, got %s", jsonKind(v))
		}
		t, err := time.ParseInLocation("2006-01-02", s, time.UTC)
		if err != nil || len(s) != 10 { // "2026-6-1" must fail (BR-3)
			return bv, fmt.Errorf("expected ISO-8601 date (YYYY-MM-DD), got %q", s)
		}
		scalar(t, s)
	case VarTimestamp:
		s, ok := v.(string)
		if !ok {
			return bv, fmt.Errorf("expected ISO-8601 timestamp string, got %s", jsonKind(v))
		}
		t, err := time.Parse(time.RFC3339, s)
		if err != nil {
			return bv, fmt.Errorf("expected ISO-8601 timestamp, got %q", s)
		}
		scalar(t.UTC(), s)
	case VarStringList, VarIntegerList:
		arr, ok := v.([]any)
		if !ok {
			return bv, fmt.Errorf("expected array, got %s", jsonKind(v))
		}
		bv.IsList = true
		display := make([]any, 0, len(arr))
		for i, el := range arr {
			if d.Type == VarStringList {
				s, ok := el.(string)
				if !ok {
					return bv, fmt.Errorf("element %d: expected string, got %s", i, jsonKind(el))
				}
				bv.List = append(bv.List, s)
				display = append(display, s)
			} else {
				n, err := asInt64(el)
				if err != nil {
					return bv, fmt.Errorf("element %d: %s", i, err)
				}
				bv.List = append(bv.List, n)
				display = append(display, n)
			}
		}
		bv.Display = display
	default:
		return bv, fmt.Errorf("unknown type %q", d.Type)
	}

	if len(d.AllowedValues) > 0 && !bv.IsList {
		ok := false
		for _, av := range d.AllowedValues {
			if canonicalJSON(av) == canonicalJSON(raw) {
				ok = true
				break
			}
		}
		if !ok {
			return bv, fmt.Errorf("value not in allowed_values")
		}
	}
	if d.Min != nil || d.Max != nil {
		if n, err := numericOf(bv.Value); err == nil {
			if d.Min != nil && n < *d.Min {
				return bv, fmt.Errorf("value below min %v", *d.Min)
			}
			if d.Max != nil && n > *d.Max {
				return bv, fmt.Errorf("value above max %v", *d.Max)
			}
		}
	}
	return bv, nil
}

var decimalRe = regexp.MustCompile(`^-?[0-9]+(\.[0-9]+)?$`)

func asInt64(v any) (int64, error) {
	n, ok := v.(json.Number)
	if !ok {
		return 0, fmt.Errorf("expected integer, got %s", jsonKind(v))
	}
	i, err := strconv.ParseInt(n.String(), 10, 64)
	if err != nil {
		return 0, fmt.Errorf("expected integer, got %q", n.String())
	}
	return i, nil
}

func numericOf(v any) (float64, error) {
	switch t := v.(type) {
	case int64:
		return float64(t), nil
	case string:
		return strconv.ParseFloat(t, 64)
	}
	return 0, fmt.Errorf("not numeric")
}

func jsonKind(v any) string {
	switch v.(type) {
	case string:
		return "string"
	case json.Number:
		return "number"
	case bool:
		return "boolean"
	case []any:
		return "array"
	case map[string]any:
		return "object"
	case nil:
		return "null"
	}
	return "unknown"
}

func canonicalJSON(raw json.RawMessage) string {
	var v any
	if err := json.Unmarshal(raw, &v); err != nil {
		return string(raw)
	}
	b, err := json.Marshal(v)
	if err != nil {
		return string(raw)
	}
	return string(b)
}
