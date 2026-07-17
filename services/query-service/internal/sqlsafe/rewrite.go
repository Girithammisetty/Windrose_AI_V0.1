package sqlsafe

import (
	"fmt"
	"strings"

	"github.com/windrose-ai/query-service/internal/domain"
)

// Rewritten is the product of the safe-substitution rewrite (QRY-FR-003).
type Rewritten struct {
	SQL        string   // SQL with $n positional placeholders only
	Args       []any    // ordered bound values, Args[i] binds $i+1
	ParamNames []string // ParamNames[i] is the variable name behind $i+1
}

// Rewrite performs safe substitution:
//
//   - every :name placeholder becomes a positional $n reference and its
//     validated value is appended to the ordered argument list — the value
//     itself never touches the SQL text (QRY-FR-003, BR-1);
//   - the same variable used twice reuses the same $n (the V1 process_vars!
//     bug substituted only the FIRST variable; here every occurrence of
//     every variable is bound — AC-1);
//   - list variables expand to a parenthesised placeholder set
//     ($n,$n+1,…) with each element bound separately (QRY-FR-003); an empty
//     list becomes (NULL), which matches no row under IN;
//   - {{dataset(...)}} refs are replaced by engine-quoted identifiers
//     provided by the dataset resolver — never user-typed strings (BR-1).
//
// datasetIdents maps "name@version" to the resolved, engine-quoted physical
// identifier.
func Rewrite(sql string, bindings map[string]domain.BoundValue, datasetIdents map[string]string) (*Rewritten, error) {
	toks, err := Scan(sql)
	if err != nil {
		return nil, err
	}
	var (
		out        strings.Builder
		args       []any
		paramNames []string
		exprByName = map[string]string{} // variable name -> emitted placeholder expression
		problems   []domain.VariableProblem
		pos        = 0
	)
	next := func(name string, v any) string {
		args = append(args, v)
		paramNames = append(paramNames, name)
		return fmt.Sprintf("$%d", len(args))
	}
	for _, t := range toks {
		out.WriteString(sql[pos:t.Start])
		pos = t.End
		switch t.Kind {
		case tokPlaceholder:
			expr, done := exprByName[t.Name]
			if !done {
				bv, ok := bindings[t.Name]
				if !ok {
					problems = append(problems, domain.VariableProblem{Name: t.Name, Problem: "no value bound for placeholder"})
					exprByName[t.Name] = "$0" // placeholder to avoid duplicate problems
					continue
				}
				if bv.IsList {
					if len(bv.List) == 0 {
						expr = "(NULL)"
					} else {
						parts := make([]string, len(bv.List))
						for i, el := range bv.List {
							parts[i] = next(t.Name, el)
						}
						expr = "(" + strings.Join(parts, ",") + ")"
					}
				} else {
					expr = next(t.Name, bv.Value)
				}
				exprByName[t.Name] = expr
			}
			if expr == "$0" {
				continue
			}
			out.WriteString(expr)
		case tokDatasetRef:
			key := fmt.Sprintf("%s@%d", t.Name, t.Version)
			ident, ok := datasetIdents[key]
			if !ok {
				return nil, domain.EDatasetNotFound(fmt.Sprintf("dataset %q (version %d) is not resolved", t.Name, t.Version))
			}
			out.WriteString(ident)
		}
	}
	out.WriteString(sql[pos:])
	if len(problems) > 0 {
		return nil, domain.EVariableInvalid(problems)
	}
	return &Rewritten{SQL: out.String(), Args: args, ParamNames: paramNames}, nil
}

// RewriteBinds is the binds-mode rewrite (chart-service /sql/run contract):
// the SQL already carries $n positional placeholders and `binds` supplies the
// ordered values — they are passed through as prepared-statement arguments,
// never spliced into the SQL text (BR-1). Rules:
//
//   - :name placeholders may NOT be mixed with binds (one style per request);
//   - every $n must satisfy 1 ≤ n ≤ len(binds), and every bind must be
//     referenced at least once — a count mismatch is a 422;
//   - {{dataset(...)}} refs are substituted exactly like Rewrite.
func RewriteBinds(sql string, binds []any, datasetIdents map[string]string) (*Rewritten, error) {
	toks, err := ScanBinds(sql)
	if err != nil {
		return nil, err
	}
	var out strings.Builder
	pos := 0
	seen := map[int]bool{}
	for _, t := range toks {
		out.WriteString(sql[pos:t.Start])
		pos = t.End
		switch t.Kind {
		case tokPlaceholder:
			return nil, domain.EValidation("named :" + t.Name + " placeholders cannot be combined with binds; use one parameter style")
		case tokPositional:
			if t.Index > len(binds) {
				return nil, domain.EValidationDetails("bind count mismatch",
					map[string]string{"placeholder": fmt.Sprintf("$%d", t.Index), "binds": fmt.Sprint(len(binds))})
			}
			seen[t.Index] = true
			out.WriteString(sql[t.Start:t.End]) // keep $n verbatim
		case tokDatasetRef:
			key := fmt.Sprintf("%s@%d", t.Name, t.Version)
			ident, ok := datasetIdents[key]
			if !ok {
				return nil, domain.EDatasetNotFound(fmt.Sprintf("dataset %q (version %d) is not resolved", t.Name, t.Version))
			}
			out.WriteString(ident)
		}
	}
	out.WriteString(sql[pos:])
	for i := 1; i <= len(binds); i++ {
		if !seen[i] {
			return nil, domain.EValidationDetails("bind count mismatch",
				map[string]string{"unreferenced_bind": fmt.Sprintf("$%d", i), "binds": fmt.Sprint(len(binds))})
		}
	}
	// ParamNames mirror the positional slots; binds-mode history never
	// persists the raw values (conservative PII posture: redactParams only
	// emits declared-variable display values).
	names := make([]string, len(binds))
	for i := range names {
		names[i] = fmt.Sprintf("$%d", i+1)
	}
	return &Rewritten{SQL: out.String(), Args: binds, ParamNames: names}, nil
}
