package api

import (
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	gcevent "github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/notification-service/internal/domain"
	"github.com/windrose-ai/notification-service/internal/templates"
)

type templateBody struct {
	Key         string `json:"key"`
	Channel     string `json:"channel"`
	Locale      string `json:"locale"`
	SubjectTpl  string `json:"subject_tpl"`
	BodyHTMLTpl string `json:"body_html_tpl"`
	BodyTextTpl string `json:"body_text_tpl"`
}

func (s *Server) handleCreateTemplate(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	var body templateBody
	if !decodeBody(w, r, &body) {
		return
	}
	if body.Key == "" || body.Channel == "" {
		writeErr(w, r, domain.EValidation("key and channel are required", nil))
		return
	}
	locale := defStr(body.Locale, "en")
	// Whitelist validation (NOTIF-FR-040, AC-8): referenced vars must be in the
	// event type's schema.
	if m, ok := s.Registry.Lookup(body.Key); ok {
		offenders, err := templates.ValidateWhitelist(m.Variables, body.SubjectTpl, body.BodyHTMLTpl, body.BodyTextTpl)
		if err != nil {
			writeErr(w, r, domain.ERenderFailed("template does not parse: "+err.Error(), nil))
			return
		}
		if len(offenders) > 0 {
			writeErr(w, r, domain.ERenderFailed("template references non-whitelisted variables",
				map[string]any{"variables": offenders, "whitelist": keysOf(m.Variables)}))
			return
		}
	}
	tenant := o.Tenant
	ver, err := s.Store.NextVersion(r.Context(), &tenant, body.Key, body.Channel, locale)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	t := &domain.Template{
		ID: domain.NewID(), TenantID: &tenant, Key: body.Key, Channel: body.Channel, Locale: locale,
		Version: ver, SubjectTpl: body.SubjectTpl, BodyHTMLTpl: body.BodyHTMLTpl, BodyTextTpl: body.BodyTextTpl,
		Status: domain.TemplateDraft, CreatedBy: o.UserID, CreatedAt: time.Now().UTC(),
	}
	if err := s.Store.CreateTemplateVersion(r.Context(), t); err != nil {
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusCreated, t)
}

func (s *Server) handleListTemplates(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	key := r.URL.Query().Get("filter[key]")
	if key == "" {
		writeErr(w, r, domain.EValidation("filter[key] is required", nil))
		return
	}
	list, err := s.Store.ListTemplateVersions(r.Context(), o.Tenant, key)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if list == nil {
		list = []*domain.Template{}
	}
	writeData(w, http.StatusOK, list)
}

type publishBody struct {
	TemplateID string `json:"template_id"`
}

func (s *Server) handlePublishTemplate(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	var body publishBody
	if !decodeBody(w, r, &body) {
		return
	}
	id, err := uuid.Parse(body.TemplateID)
	if err != nil {
		writeErr(w, r, domain.EValidation("template_id must be a uuid", nil))
		return
	}
	tenant := o.Tenant
	t, err := s.Store.PublishTemplate(r.Context(), &tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, t)
}

type previewBody struct {
	Channel     string         `json:"channel"`
	Locale      string         `json:"locale"`
	SampleEvent map[string]any `json:"sample_event"`
}

func (s *Server) handlePreviewTemplate(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	key := chi.URLParam(r, "key")
	var body previewBody
	if !decodeBody(w, r, &body) {
		return
	}
	channel := defStr(body.Channel, domain.ChannelEmail)
	locale := defStr(body.Locale, "en")
	t, err := s.Store.ResolveTemplate(r.Context(), o.Tenant, key, channel, locale)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if t == nil {
		s.notFound(w, r)
		return
	}
	env := gcevent.Envelope{EventType: key, Payload: body.SampleEvent}
	data := previewData(env)
	rendered, rerr := templates.Render(t.SubjectTpl, t.BodyHTMLTpl, t.BodyTextTpl, data)
	if rerr != nil {
		writeErr(w, r, domain.ERenderFailed("render failed: "+rerr.Error(), nil))
		return
	}
	writeData(w, http.StatusOK, map[string]string{"subject": rendered.Subject, "html": rendered.HTML, "text": rendered.Text})
}

// previewData mirrors the pipeline's snake_case→PascalCase mapping so previews
// match runtime rendering.
func previewData(env gcevent.Envelope) map[string]any {
	data := map[string]any{}
	for k, v := range env.Payload {
		data[pascalCase(k)] = v
	}
	if _, ok := data["DeepLink"]; !ok {
		data["DeepLink"] = "https://app.windrose.local/preview"
	}
	return data
}

func pascalCase(s string) string {
	var b []rune
	upper := true
	for _, r := range s {
		if r == '_' || r == '-' || r == '.' {
			upper = true
			continue
		}
		if upper && r >= 'a' && r <= 'z' {
			r = r - 32
		}
		upper = false
		b = append(b, r)
	}
	return string(b)
}

func keysOf(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
