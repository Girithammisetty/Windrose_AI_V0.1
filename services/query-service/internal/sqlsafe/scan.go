// Package sqlsafe is the statement-safety layer (QRY-FR-002/003/004/005 and
// QRY-FR-020/021/022):
//
//   - a tokenizer-aware scanner that finds :name placeholders and
//     {{dataset(...)}} refs outside strings/comments, rejects the legacy
//     {var} syntax, and rejects raw positional parameters;
//   - a rewriter that turns named placeholders into positional $n bindings
//     with ordered argument values — values are NEVER concatenated into SQL
//     text (BR-1; designs out the V1 process_vars! gsub splicing);
//   - AST-based statement classification via the PostgreSQL parser
//     (pg_query_go): single SELECT only, walked recursively so CTE-wrapped
//     DML, SELECT INTO, locking clauses and multi-statement batches are all
//     rejected (replaces the bypassable V1 regex verify_statement);
//   - an identifier-level tenant namespace guard (BR-2).
package sqlsafe

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/windrose-ai/query-service/internal/domain"
)

type tokenKind int

const (
	tokPlaceholder tokenKind = iota // :name
	tokDatasetRef                   // {{dataset('name'[, version=N])}}
	tokPositional                   // $n (allowed only in binds mode)
)

// Token is one scanner finding, with byte offsets into the original SQL.
type Token struct {
	Kind    tokenKind
	Start   int
	End     int // exclusive
	Name    string
	Version int // dataset refs only; 0 = latest
	Index   int // positional params only; the n of $n
}

// Scan walks the SQL respecting string literals (” escapes), double-quoted
// identifiers, line/block comments (nested, per PG), dollar-quoted strings
// and ::type casts. It returns placeholder and dataset-ref tokens in order
// of appearance.
//
// Rejections at this layer (all 422):
//   - legacy {var} placeholders (QRY-FR-002: migration hint);
//   - raw positional parameters ($1 / ?) — only named :var is accepted, so
//     the service owns the whole positional numbering space.
func Scan(sql string) ([]Token, error) { return scan(sql, false) }

// ScanBinds scans like Scan but ACCEPTS $n positional parameters, emitting
// them as tokens: the binds-mode contract (chart-service /sql/run with
// `binds`) supplies the ordered argument values out of band, so the caller —
// not this service — owns the positional numbering space. `?` stays rejected.
func ScanBinds(sql string) ([]Token, error) { return scan(sql, true) }

func scan(sql string, allowPositional bool) ([]Token, error) {
	var toks []Token
	n := len(sql)
	i := 0
	for i < n {
		c := sql[i]
		switch {
		case c == '\'': // string literal, '' escape
			i++
			for i < n {
				if sql[i] == '\'' {
					if i+1 < n && sql[i+1] == '\'' {
						i += 2
						continue
					}
					i++
					break
				}
				i++
			}
		case c == '"': // quoted identifier, "" escape
			i++
			for i < n {
				if sql[i] == '"' {
					if i+1 < n && sql[i+1] == '"' {
						i += 2
						continue
					}
					i++
					break
				}
				i++
			}
		case c == '-' && i+1 < n && sql[i+1] == '-': // line comment
			for i < n && sql[i] != '\n' {
				i++
			}
		case c == '/' && i+1 < n && sql[i+1] == '*': // block comment (nested)
			depth := 1
			i += 2
			for i < n && depth > 0 {
				if sql[i] == '/' && i+1 < n && sql[i+1] == '*' {
					depth++
					i += 2
				} else if sql[i] == '*' && i+1 < n && sql[i+1] == '/' {
					depth--
					i += 2
				} else {
					i++
				}
			}
		case c == '$': // dollar-quoted string or positional param
			if i+1 < n && isDigit(sql[i+1]) {
				if !allowPositional {
					return nil, domain.EValidation(
						"positional parameters ($n) are not allowed; declare typed variables and reference them as :name")
				}
				j := i + 1
				for j < n && isDigit(sql[j]) {
					j++
				}
				idx, err := strconv.Atoi(sql[i+1 : j])
				if err != nil || idx <= 0 {
					return nil, domain.EValidation("invalid positional parameter " + sql[i:j])
				}
				toks = append(toks, Token{Kind: tokPositional, Start: i, End: j, Index: idx})
				i = j
				continue
			}
			j := i + 1
			for j < n && isIdentChar(sql[j]) {
				j++
			}
			if j < n && sql[j] == '$' {
				tag := sql[i : j+1] // $tag$
				end := strings.Index(sql[j+1:], tag)
				if end < 0 {
					return nil, domain.EValidation("unterminated dollar-quoted string")
				}
				i = j + 1 + end + len(tag)
			} else {
				i++
			}
		case c == '?':
			return nil, domain.EValidation(
				"positional parameters (?) are not allowed; declare typed variables and reference them as :name")
		case c == ':':
			if i+1 < n && sql[i+1] == ':' { // ::type cast
				i += 2
				continue
			}
			if i+1 < n && (isIdentStart(sql[i+1])) {
				j := i + 1
				for j < n && isIdentChar(sql[j]) {
					j++
				}
				toks = append(toks, Token{Kind: tokPlaceholder, Start: i, End: j, Name: sql[i+1 : j]})
				i = j
				continue
			}
			i++
		case c == '{':
			if i+1 < n && sql[i+1] == '{' {
				tok, next, err := parseDatasetRef(sql, i)
				if err != nil {
					return nil, err
				}
				toks = append(toks, tok)
				i = next
				continue
			}
			// legacy {var} syntax (V1) — rejected with a migration hint
			j := i + 1
			for j < n && isIdentChar(sql[j]) {
				j++
			}
			if j > i+1 && j < n && sql[j] == '}' {
				return nil, domain.EValidationDetails(
					"legacy {var} placeholder syntax is not supported",
					map[string]string{"placeholder": sql[i : j+1],
						"hint": "declare a typed variable and reference it as :" + sql[i+1:j]})
			}
			i++
		default:
			i++
		}
	}
	return toks, nil
}

// parseDatasetRef parses {{dataset('<name>'[, version=<n>])}} starting at
// the "{{" (QRY-FR-005).
func parseDatasetRef(sql string, start int) (Token, int, error) {
	bad := func(msg string) (Token, int, error) {
		return Token{}, 0, domain.EValidation("invalid dataset reference: " + msg)
	}
	i := start + 2
	skipWS := func() {
		for i < len(sql) && (sql[i] == ' ' || sql[i] == '\t' || sql[i] == '\n' || sql[i] == '\r') {
			i++
		}
	}
	skipWS()
	if !strings.HasPrefix(sql[i:], "dataset") {
		return bad("expected dataset('<name>')")
	}
	i += len("dataset")
	skipWS()
	if i >= len(sql) || sql[i] != '(' {
		return bad("expected (")
	}
	i++
	skipWS()
	if i >= len(sql) || sql[i] != '\'' {
		return bad("expected quoted dataset name")
	}
	i++
	nameStart := i
	for i < len(sql) && sql[i] != '\'' {
		i++
	}
	if i >= len(sql) {
		return bad("unterminated dataset name")
	}
	name := sql[nameStart:i]
	i++
	skipWS()
	version := 0
	if i < len(sql) && sql[i] == ',' {
		i++
		skipWS()
		if !strings.HasPrefix(sql[i:], "version") {
			return bad("expected version=<n>")
		}
		i += len("version")
		skipWS()
		if i >= len(sql) || sql[i] != '=' {
			return bad("expected version=<n>")
		}
		i++
		skipWS()
		numStart := i
		for i < len(sql) && isDigit(sql[i]) {
			i++
		}
		v, err := strconv.Atoi(sql[numStart:i])
		if err != nil || v <= 0 {
			return bad("version must be a positive integer")
		}
		version = v
		skipWS()
	}
	if i >= len(sql) || sql[i] != ')' {
		return bad("expected )")
	}
	i++
	skipWS()
	if !strings.HasPrefix(sql[i:], "}}") {
		return bad("expected }}")
	}
	i += 2
	if name == "" {
		return bad("empty dataset name")
	}
	return Token{Kind: tokDatasetRef, Start: start, End: i, Name: name, Version: version}, i, nil
}

// PlaceholderNames returns the distinct :name placeholders in order of first
// appearance (save-time declaration check, QRY-FR-004).
func PlaceholderNames(sql string) ([]string, error) {
	toks, err := Scan(sql)
	if err != nil {
		return nil, err
	}
	seen := map[string]bool{}
	var names []string
	for _, t := range toks {
		if t.Kind == tokPlaceholder && !seen[t.Name] {
			seen[t.Name] = true
			names = append(names, t.Name)
		}
	}
	return names, nil
}

// DatasetRefs returns the dataset references in order of first appearance.
func DatasetRefs(sql string) ([]domain.DatasetRef, error) { return datasetRefs(sql, false) }

// DatasetRefsBinds is DatasetRefs for binds-mode SQL ($n params allowed).
func DatasetRefsBinds(sql string) ([]domain.DatasetRef, error) { return datasetRefs(sql, true) }

func datasetRefs(sql string, allowPositional bool) ([]domain.DatasetRef, error) {
	toks, err := scan(sql, allowPositional)
	if err != nil {
		return nil, err
	}
	seen := map[string]bool{}
	var refs []domain.DatasetRef
	for _, t := range toks {
		if t.Kind != tokDatasetRef {
			continue
		}
		key := fmt.Sprintf("%s@%d", t.Name, t.Version)
		if !seen[key] {
			seen[key] = true
			refs = append(refs, domain.DatasetRef{Name: t.Name, Version: t.Version})
		}
	}
	return refs, nil
}

func isDigit(c byte) bool      { return c >= '0' && c <= '9' }
func isIdentStart(c byte) bool { return c == '_' || c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' }
func isIdentChar(c byte) bool  { return isIdentStart(c) || isDigit(c) }
