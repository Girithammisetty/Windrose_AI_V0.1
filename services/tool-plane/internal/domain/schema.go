package domain

import (
	"fmt"
	"strings"
	"time"
)

// FieldError is one per-field validation problem (VALIDATION_FAILED details).
type FieldError struct {
	Field   string `json:"field"`
	Message string `json:"message"`
}

// ValidateSchemaDoc checks that a document is a usable JSON Schema for a tool
// input/output (TPL-FR-001/AC-7). It enforces the platform's publish-time rules:
// object type, additionalProperties:false on the root object (TPL-FR-034 unknown
// fields rejected), and well-formed properties/required. It is intentionally a
// focused validator over the 2020-12 subset the platform uses.
func ValidateSchemaDoc(schema map[string]any) []FieldError {
	var errs []FieldError
	if schema == nil {
		return []FieldError{{Field: "$", Message: "schema is required"}}
	}
	typ, _ := schema["type"].(string)
	if typ != "object" {
		errs = append(errs, FieldError{Field: "type", Message: "root schema type must be \"object\""})
	}
	if ap, ok := schema["additionalProperties"]; ok {
		if b, isBool := ap.(bool); !isBool || b {
			errs = append(errs, FieldError{Field: "additionalProperties", Message: "must be false (unknown fields rejected)"})
		}
	} else {
		errs = append(errs, FieldError{Field: "additionalProperties", Message: "must be present and false"})
	}
	props, ok := schema["properties"].(map[string]any)
	if !ok {
		errs = append(errs, FieldError{Field: "properties", Message: "object schema must declare properties"})
		return errs
	}
	if req, ok := schema["required"].([]any); ok {
		for _, r := range req {
			name, _ := r.(string)
			if _, present := props[name]; !present {
				errs = append(errs, FieldError{Field: "required." + name, Message: "required field not declared in properties"})
			}
		}
	}
	for name, raw := range props {
		p, ok := raw.(map[string]any)
		if !ok {
			errs = append(errs, FieldError{Field: "properties." + name, Message: "property schema must be an object"})
			continue
		}
		if _, ok := p["type"]; !ok {
			errs = append(errs, FieldError{Field: "properties." + name, Message: "property must declare a type"})
		}
	}
	return errs
}

// ValidateArgs validates args against a tool input_schema (TPL-FR-034). It
// enforces required, additionalProperties:false, and per-field type/maxLength/
// enum/minimum/maximum/maxItems/format(date-time) — the constraint vocabulary the
// platform's declared schemas use. Returns per-field problems (empty = valid).
func ValidateArgs(schema, args map[string]any) []FieldError {
	var errs []FieldError
	props, _ := schema["properties"].(map[string]any)
	// Required.
	if req, ok := schema["required"].([]any); ok {
		for _, r := range req {
			name, _ := r.(string)
			if _, present := args[name]; !present {
				errs = append(errs, FieldError{Field: name, Message: "required"})
			}
		}
	}
	// additionalProperties:false -> reject unknown fields.
	ap, hasAP := schema["additionalProperties"].(bool)
	if hasAP && !ap {
		for name := range args {
			if _, ok := props[name]; !ok {
				errs = append(errs, FieldError{Field: name, Message: "unknown field (additionalProperties:false)"})
			}
		}
	}
	// Per-field constraints.
	for name, raw := range args {
		p, ok := props[name].(map[string]any)
		if !ok {
			continue
		}
		errs = append(errs, validateField(name, p, raw)...)
	}
	return errs
}

func validateField(name string, p map[string]any, val any) []FieldError {
	var errs []FieldError
	typ, _ := p["type"].(string)
	switch typ {
	case "string":
		s, ok := val.(string)
		if !ok {
			return []FieldError{{Field: name, Message: "must be a string"}}
		}
		if ml, ok := numeric(p["maxLength"]); ok && float64(len(s)) > ml {
			errs = append(errs, FieldError{Field: name, Message: fmt.Sprintf("exceeds maxLength %d", int(ml))})
		}
		if enum, ok := p["enum"].([]any); ok && !inEnum(s, enum) {
			errs = append(errs, FieldError{Field: name, Message: "not an allowed value"})
		}
		if f, _ := p["format"].(string); f == "date-time" {
			if _, err := time.Parse(time.RFC3339, s); err != nil {
				errs = append(errs, FieldError{Field: name, Message: "must be RFC3339 date-time"})
			}
		}
	case "integer", "number":
		n, ok := numeric(val)
		if !ok {
			return []FieldError{{Field: name, Message: "must be a number"}}
		}
		if typ == "integer" && n != float64(int64(n)) {
			errs = append(errs, FieldError{Field: name, Message: "must be an integer"})
		}
		if mn, ok := numeric(p["minimum"]); ok && n < mn {
			errs = append(errs, FieldError{Field: name, Message: fmt.Sprintf("below minimum %v", mn)})
		}
		if mx, ok := numeric(p["maximum"]); ok && n > mx {
			errs = append(errs, FieldError{Field: name, Message: fmt.Sprintf("above maximum %v", mx)})
		}
	case "array":
		arr, ok := val.([]any)
		if !ok {
			return []FieldError{{Field: name, Message: "must be an array"}}
		}
		if mi, ok := numeric(p["maxItems"]); ok && float64(len(arr)) > mi {
			errs = append(errs, FieldError{Field: name, Message: fmt.Sprintf("exceeds maxItems %d", int(mi))})
		}
	case "boolean":
		if _, ok := val.(bool); !ok {
			errs = append(errs, FieldError{Field: name, Message: "must be a boolean"})
		}
	}
	return errs
}

// URNFields returns arg field names annotated with x-windrose-urn in the schema,
// mapped to their URN template (BRD example, TPL-FR-032/BR-12). These are the
// fields whose values carry resource URNs used for OBO-grant + cross-tenant checks.
func URNFields(schema map[string]any) map[string]string {
	out := map[string]string{}
	props, _ := schema["properties"].(map[string]any)
	for name, raw := range props {
		p, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		if tmpl, ok := p["x-windrose-urn"].(string); ok {
			out[name] = tmpl
		}
	}
	return out
}

// AffectedURNs resolves the x-windrose-urn templates against args, producing the
// concrete resource URNs the call touches (used for OPA obo-grant + cross-tenant
// checks). Template form: "wr:{tenant}:case:case/{value}".
func AffectedURNs(schema, args map[string]any, tenant string) []string {
	var urns []string
	for field, tmpl := range URNFields(schema) {
		v, ok := args[field].(string)
		if !ok || v == "" {
			continue
		}
		urn := strings.ReplaceAll(tmpl, "{tenant}", tenant)
		urn = strings.ReplaceAll(urn, "{value}", v)
		// If the value is already a full URN, use it verbatim (cross-tenant probe).
		if IsURN(v) {
			urn = v
		}
		urns = append(urns, urn)
	}
	return urns
}

func numeric(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case float32:
		return float64(n), true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	}
	return 0, false
}

func inEnum(s string, enum []any) bool {
	for _, e := range enum {
		if es, ok := e.(string); ok && es == s {
			return true
		}
	}
	return false
}
