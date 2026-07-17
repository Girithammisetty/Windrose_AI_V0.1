package sqlsafe

import (
	"fmt"
	"strings"

	pgquery "github.com/pganalyze/pg_query_go/v6"
	"google.golang.org/protobuf/reflect/protoreflect"

	"github.com/windrose-ai/query-service/internal/domain"
)

// TableRef is one physical table reference found in the AST.
type TableRef struct {
	Catalog string
	Schema  string
	Name    string
}

func (t TableRef) String() string {
	parts := []string{}
	if t.Catalog != "" {
		parts = append(parts, t.Catalog)
	}
	if t.Schema != "" {
		parts = append(parts, t.Schema)
	}
	parts = append(parts, t.Name)
	return strings.Join(parts, ".")
}

// Classification is the result of AST-based statement analysis (QRY-FR-020).
type Classification struct {
	Fingerprint   string
	Tables        []TableRef
	CTENames      map[string]bool
	HasOuterLimit bool
	// ParamColumns maps a positional parameter number ($n) to the column it
	// is compared against in simple `col <op> $n` expressions — used for
	// PII redaction of history parameters (BR-12, AC-14).
	ParamColumns map[int]string
}

// allowedStmts is the AST allow-list: every other *Stmt node anywhere in the
// tree (including inside CTEs) is rejected. Allow-listing — rather than
// deny-listing — means new/unknown statement kinds fail closed.
var allowedStmts = map[string]bool{
	"RawStmt":    true, // statement wrapper
	"SelectStmt": true,
}

// friendly names for common rejections (error messages only).
var stmtFriendly = map[string]string{
	"InsertStmt": "INSERT", "UpdateStmt": "UPDATE", "DeleteStmt": "DELETE",
	"MergeStmt": "MERGE", "CreateStmt": "CREATE", "DropStmt": "DROP",
	"AlterTableStmt": "ALTER", "GrantStmt": "GRANT", "CallStmt": "CALL",
	"VariableSetStmt": "SET", "ExplainStmt": "EXPLAIN", "TruncateStmt": "TRUNCATE",
	"CopyStmt": "COPY", "CreateTableAsStmt": "CREATE TABLE AS / SELECT INTO",
	"TransactionStmt": "transaction control",
}

// Classify parses SQL (after placeholder rewrite, so only $n parameters
// remain) into a full AST and enforces the read-only statement policy
// (QRY-FR-020):
//
//   - exactly one statement (multi-statement batches → 403);
//   - the statement must be a SELECT (CTEs and set operations included);
//   - the ENTIRE tree is walked, so a DELETE hidden in a CTE, SELECT ...
//     INTO, FOR UPDATE locking, EXPLAIN [ANALYZE], SET, CALL, COPY and any
//     DDL/DML anywhere are rejected — comments and case obfuscation are
//     irrelevant because classification happens on the AST, not on text.
func Classify(sql string) (*Classification, error) {
	res, err := pgquery.Parse(sql)
	if err != nil {
		return nil, domain.EValidation("SQL parse error: " + firstLine(err.Error()))
	}
	if len(res.Stmts) == 0 {
		return nil, domain.EValidation("empty statement")
	}
	if len(res.Stmts) > 1 {
		return nil, domain.EStatementNotAllowed(
			fmt.Sprintf("multi-statement batches are not allowed (%d statements found)", len(res.Stmts)))
	}
	top := res.Stmts[0].Stmt
	sel := top.GetSelectStmt()
	if sel == nil {
		name := nodeTypeName(top)
		if f, ok := stmtFriendly[name]; ok {
			name = f
		}
		return nil, domain.EStatementNotAllowed("only SELECT statements are allowed; got " + name)
	}

	cls := &Classification{
		CTENames:      map[string]bool{},
		ParamColumns:  map[int]string{},
		HasOuterLimit: sel.GetLimitCount() != nil,
	}
	var walkErr error
	walk(res.ProtoReflect(), func(m protoreflect.Message) bool {
		if walkErr != nil {
			return false
		}
		name := string(m.Descriptor().Name())
		if strings.HasSuffix(name, "Stmt") && !allowedStmts[name] {
			friendly := name
			if f, ok := stmtFriendly[name]; ok {
				friendly = f
			}
			walkErr = domain.EStatementNotAllowed("statement kind not allowed: " + friendly)
			return false
		}
		switch name {
		case "IntoClause": // SELECT ... INTO creates a table
			walkErr = domain.EStatementNotAllowed("SELECT INTO is not allowed")
			return false
		case "LockingClause": // FOR UPDATE / FOR SHARE take locks
			walkErr = domain.EStatementNotAllowed("locking clauses (FOR UPDATE/SHARE) are not allowed")
			return false
		}
		switch v := m.Interface().(type) {
		case *pgquery.RangeVar:
			cls.Tables = append(cls.Tables, TableRef{
				Catalog: strings.ToLower(v.GetCatalogname()),
				Schema:  strings.ToLower(v.GetSchemaname()),
				Name:    strings.ToLower(v.GetRelname()),
			})
		case *pgquery.CommonTableExpr:
			cls.CTENames[strings.ToLower(v.GetCtename())] = true
		case *pgquery.A_Expr:
			if col, param, ok := columnParamPair(v); ok {
				cls.ParamColumns[param] = col
			}
		}
		return true
	})
	if walkErr != nil {
		return nil, walkErr
	}

	fp, err := pgquery.Fingerprint(sql)
	if err == nil {
		cls.Fingerprint = fp
	}
	return cls, nil
}

// WrapWithLimit encloses a statement in a server-constructed LIMIT wrapper
// (QRY-FR-022 agent LIMIT injection). Only server-owned constants enter the
// SQL text here — never user values.
func WrapWithLimit(sql string, limit int64) string {
	return fmt.Sprintf("SELECT * FROM (%s) AS _wr_agent_guard LIMIT %d",
		strings.TrimRight(strings.TrimSpace(sql), "; \n\t"), limit)
}

// nodeTypeName returns the concrete node kind held by a Node oneof.
func nodeTypeName(n *pgquery.Node) string {
	m := n.ProtoReflect()
	od := m.Descriptor().Oneofs().Get(0)
	fd := m.WhichOneof(od)
	if fd == nil {
		return "unknown"
	}
	if fd.Kind() == protoreflect.MessageKind {
		return string(fd.Message().Name())
	}
	return string(fd.Name())
}

// walk visits every protobuf message in the tree depth-first. visit returns
// false to stop early.
func walk(m protoreflect.Message, visit func(protoreflect.Message) bool) bool {
	if !visit(m) {
		return false
	}
	cont := true
	m.Range(func(fd protoreflect.FieldDescriptor, v protoreflect.Value) bool {
		switch {
		case fd.IsList() && fd.Kind() == protoreflect.MessageKind:
			l := v.List()
			for i := 0; i < l.Len(); i++ {
				if !walk(l.Get(i).Message(), visit) {
					cont = false
					return false
				}
			}
		case fd.IsMap():
			// pg_query's tree has no message-valued maps.
		case fd.Kind() == protoreflect.MessageKind:
			if !walk(v.Message(), visit) {
				cont = false
				return false
			}
		}
		return true
	})
	return cont
}

// columnParamPair matches `col <op> $n` / `$n <op> col` expressions.
func columnParamPair(e *pgquery.A_Expr) (column string, param int, ok bool) {
	col := columnName(e.GetLexpr())
	pr := e.GetRexpr().GetParamRef()
	if col == "" || pr == nil {
		col = columnName(e.GetRexpr())
		pr = e.GetLexpr().GetParamRef()
	}
	if col == "" || pr == nil {
		return "", 0, false
	}
	return col, int(pr.GetNumber()), true
}

func columnName(n *pgquery.Node) string {
	cr := n.GetColumnRef()
	if cr == nil {
		return ""
	}
	fields := cr.GetFields()
	if len(fields) == 0 {
		return ""
	}
	last := fields[len(fields)-1].GetString_()
	if last == nil {
		return ""
	}
	return strings.ToLower(last.GetSval())
}

func firstLine(s string) string {
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		return s[:i]
	}
	return s
}
