package pipeline

import (
	"context"
	"strings"
	"unicode"

	"github.com/google/uuid"

	gcevent "github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/notification-service/internal/templates"
)

// templateData builds the whitelisted variable map for a template from an event
// payload: each snake_case payload key is exposed PascalCase (case_number →
// CaseNumber) so default templates render, plus a DeepLink derived from the
// deep_link payload field or the resource URN (BR-5 keeps it to references).
func templateData(env gcevent.Envelope) map[string]any {
	data := map[string]any{}
	for k, v := range env.Payload {
		data[pascal(k)] = v
	}
	if _, ok := data["DeepLink"]; !ok {
		if dl, ok := env.Payload["deep_link"].(string); ok && dl != "" {
			data["DeepLink"] = dl
		} else {
			data["DeepLink"] = "https://app.windrose.local/" + strings.TrimPrefix(env.ResourceURN, "wr:")
		}
	}
	return data
}

func pascal(s string) string {
	parts := strings.FieldsFunc(s, func(r rune) bool { return r == '_' || r == '-' || r == '.' })
	var b strings.Builder
	for _, p := range parts {
		if p == "" {
			continue
		}
		r := []rune(p)
		r[0] = unicode.ToUpper(r[0])
		b.WriteString(string(r))
	}
	return b.String()
}

// renderFor resolves the template for (key, channel, locale) with tenant →
// platform precedence and renders it against the event data. On render failure
// it falls back to a minimal safe body (BR-4); it never emits {{...}} syntax.
func (p *Pipeline) renderFor(ctx context.Context, tenant uuid.UUID, key, channel, locale string, data map[string]any, fallbackTitle string) (subject, text, html string) {
	t, err := p.Store.ResolveTemplate(ctx, tenant, key, channel, locale)
	if err == nil && t != nil {
		if r, rerr := templates.Render(t.SubjectTpl, t.BodyHTMLTpl, t.BodyTextTpl, data); rerr == nil {
			return r.Subject, r.Text, r.HTML
		}
	}
	dl, _ := data["DeepLink"].(string)
	return fallbackTitle, fallbackTitle + "\n" + dl, "<p>" + htmlEscape(fallbackTitle) + "</p>"
}

func htmlEscape(s string) string {
	return strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;").Replace(s)
}
