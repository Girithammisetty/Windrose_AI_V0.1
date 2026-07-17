package domain

import "testing"

func TestValidateArgs_RequiredAndUnknown(t *testing.T) {
	schema := map[string]any{
		"type": "object", "additionalProperties": false, "required": []any{"case_id"},
		"properties": map[string]any{
			"case_id": map[string]any{"type": "string", "maxLength": float64(10)},
			"note":    map[string]any{"type": "string"},
		},
	}
	// missing required
	if errs := ValidateArgs(schema, map[string]any{}); len(errs) == 0 {
		t.Fatal("expected required error")
	}
	// unknown field
	if errs := ValidateArgs(schema, map[string]any{"case_id": "c1", "x": 1}); len(errs) == 0 {
		t.Fatal("expected additionalProperties error")
	}
	// maxLength exceeded
	if errs := ValidateArgs(schema, map[string]any{"case_id": "0123456789ABC"}); len(errs) == 0 {
		t.Fatal("expected maxLength error")
	}
	// valid
	if errs := ValidateArgs(schema, map[string]any{"case_id": "c1"}); len(errs) != 0 {
		t.Fatalf("expected valid, got %+v", errs)
	}
}

func TestValidateSchemaDoc(t *testing.T) {
	good := map[string]any{"type": "object", "additionalProperties": false, "properties": map[string]any{"a": map[string]any{"type": "string"}}}
	if errs := ValidateSchemaDoc(good); len(errs) != 0 {
		t.Fatalf("expected valid schema, got %+v", errs)
	}
	bad := map[string]any{"type": "string"}
	if errs := ValidateSchemaDoc(bad); len(errs) == 0 {
		t.Fatal("expected invalid schema errors")
	}
}

func TestAffectedURNs_CrossTenant(t *testing.T) {
	schema := map[string]any{"properties": map[string]any{
		"case_id": map[string]any{"type": "string", "x-windrose-urn": "wr:{tenant}:case:case/{value}"},
	}}
	urns := AffectedURNs(schema, map[string]any{"case_id": "c1"}, "t-42")
	if len(urns) != 1 || urns[0] != "wr:t-42:case:case/c1" {
		t.Fatalf("bad urn expansion: %+v", urns)
	}
	// full URN passthrough (cross-tenant probe)
	urns = AffectedURNs(schema, map[string]any{"case_id": "wr:t-99:case:case/c1"}, "t-42")
	if URNTenant(urns[0]) != "t-99" {
		t.Fatalf("expected cross-tenant urn, got %+v", urns)
	}
}

// A role-governed URN field (x-windrose-urn-obo:false) still expands for the
// cross-tenant guard (AffectedURNs) but is excluded from the per-resource
// obo-grant intersection (AffectedOboURNs), while a default-annotated field
// stays in both.
func TestAffectedOboURNs_RoleGovernedOptOut(t *testing.T) {
	schema := map[string]any{"properties": map[string]any{
		"case_id": map[string]any{"type": "string",
			"x-windrose-urn": "wr:{tenant}:case:case/{value}"},
		"model_version_urn": map[string]any{"type": "string",
			"x-windrose-urn":     "wr:{tenant}:experiment:model_version/{value}",
			"x-windrose-urn-obo": false},
	}}
	args := map[string]any{"case_id": "c1", "model_version_urn": "mv1"}

	all := AffectedURNs(schema, args, "t-42")
	if len(all) != 2 {
		t.Fatalf("cross-tenant set should include both URNs, got %+v", all)
	}

	obo := AffectedOboURNs(schema, args, "t-42")
	if len(obo) != 1 || obo[0] != "wr:t-42:case:case/c1" {
		t.Fatalf("obo set should include only the case (assignable) URN, got %+v", obo)
	}
}

func TestArgsDigest_Deterministic(t *testing.T) {
	a := map[string]any{"b": 2, "a": 1}
	b := map[string]any{"a": 1, "b": 2}
	if ArgsDigest(a) != ArgsDigest(b) {
		t.Fatal("digest must be canonical (key-order independent)")
	}
	if ArgsDigest(a) == ArgsDigest(map[string]any{"a": 1}) {
		t.Fatal("different args must differ")
	}
}

func TestSemVer(t *testing.T) {
	if !SatisfiesCaret("^1.0.0", "1.3.0") {
		t.Fatal("1.3.0 should satisfy ^1.0.0")
	}
	if SatisfiesCaret("^1.0.0", "2.0.0") {
		t.Fatal("2.0.0 should not satisfy ^1.0.0")
	}
	a, _ := ParseSemVer("1.2.3")
	b, _ := ParseSemVer("1.3.0")
	if a.Compare(b) >= 0 {
		t.Fatal("1.2.3 < 1.3.0")
	}
}

func TestTierRank(t *testing.T) {
	if TierRank(TierRead) >= TierRank(TierAdmin) {
		t.Fatal("read must rank below admin")
	}
	if TierRank("bogus") != -1 {
		t.Fatal("unknown tier ranks -1")
	}
}
