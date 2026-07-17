// Package templates renders versioned message templates with a whitelisted
// variable schema per event type (NOTIF-FR-040, BR-5). Publishing validates
// that a template references only whitelisted variables (AC-8); rendering uses
// Go html/template (HTML body) and text/template (subject + plaintext) with a
// small function set. A render failure never leaks {{...}} syntax to a
// recipient — callers fall back to the previous published version (BR-4).
package templates

import (
	"bytes"
	"fmt"
	"html/template"
	"sort"
	"strings"
	texttemplate "text/template"
	"text/template/parse"
	"time"
)

// FuncMap is the whitelisted template function set.
var FuncMap = map[string]any{
	"date":     func(t time.Time) string { return t.Format("2006-01-02") },
	"datetime": func(t time.Time) string { return t.Format("2006-01-02 15:04 MST") },
	"upper":    strings.ToUpper,
	"lower":    strings.ToLower,
}

// Rendered is the output of a successful render.
type Rendered struct {
	Subject string
	HTML    string
	Text    string
}

// Render executes subject/html/text templates against data. Any parse or exec
// error is returned so the caller can fall back (BR-4).
func Render(subjectTpl, htmlTpl, textTpl string, data map[string]any) (Rendered, error) {
	subject, err := execText("subject", subjectTpl, data)
	if err != nil {
		return Rendered{}, fmt.Errorf("subject: %w", err)
	}
	text, err := execText("text", textTpl, data)
	if err != nil {
		return Rendered{}, fmt.Errorf("text: %w", err)
	}
	htmlOut, err := execHTML("html", htmlTpl, data)
	if err != nil {
		return Rendered{}, fmt.Errorf("html: %w", err)
	}
	return Rendered{Subject: subject, HTML: htmlOut, Text: text}, nil
}

func execText(name, tpl string, data map[string]any) (string, error) {
	if tpl == "" {
		return "", nil
	}
	t, err := texttemplate.New(name).Option("missingkey=error").Funcs(FuncMap).Parse(tpl)
	if err != nil {
		return "", err
	}
	var buf bytes.Buffer
	if err := t.Execute(&buf, data); err != nil {
		return "", err
	}
	return buf.String(), nil
}

func execHTML(name, tpl string, data map[string]any) (string, error) {
	if tpl == "" {
		return "", nil
	}
	t, err := template.New(name).Option("missingkey=error").Funcs(FuncMap).Parse(tpl)
	if err != nil {
		return "", err
	}
	var buf bytes.Buffer
	if err := t.Execute(&buf, data); err != nil {
		return "", err
	}
	return buf.String(), nil
}

// ValidateWhitelist parses every template body and returns the variable names
// referenced outside the whitelist (NOTIF-FR-040, BR-5). Empty result = valid.
func ValidateWhitelist(whitelist map[string]string, bodies ...string) ([]string, error) {
	allowed := map[string]bool{}
	for k := range whitelist {
		allowed[k] = true
	}
	seen := map[string]bool{}
	var offenders []string
	for _, body := range bodies {
		if body == "" {
			continue
		}
		t, err := texttemplate.New("v").Funcs(FuncMap).Parse(body)
		if err != nil {
			return nil, err
		}
		for _, f := range referencedFields(t.Root) {
			if !allowed[f] && !seen[f] {
				seen[f] = true
				offenders = append(offenders, f)
			}
		}
	}
	sort.Strings(offenders)
	return offenders, nil
}

// referencedFields walks a template parse tree and collects top-level field
// names referenced as {{.Field}} / {{.Field | fn}} / in pipelines.
func referencedFields(node parse.Node) []string {
	var out []string
	var walk func(n parse.Node)
	walk = func(n parse.Node) {
		if n == nil {
			return
		}
		switch t := n.(type) {
		case *parse.ListNode:
			if t == nil {
				return
			}
			for _, c := range t.Nodes {
				walk(c)
			}
		case *parse.ActionNode:
			walk(t.Pipe)
		case *parse.PipeNode:
			if t == nil {
				return
			}
			for _, cmd := range t.Cmds {
				for _, arg := range cmd.Args {
					walk(arg)
				}
			}
		case *parse.FieldNode:
			if len(t.Ident) > 0 {
				out = append(out, t.Ident[0])
			}
		case *parse.IfNode:
			walk(t.Pipe)
			walk(t.List)
			walk(t.ElseList)
		case *parse.RangeNode:
			walk(t.Pipe)
			walk(t.List)
			walk(t.ElseList)
		case *parse.WithNode:
			walk(t.Pipe)
			walk(t.List)
			walk(t.ElseList)
		case *parse.TemplateNode:
			walk(t.Pipe)
		}
	}
	walk(node)
	return out
}
