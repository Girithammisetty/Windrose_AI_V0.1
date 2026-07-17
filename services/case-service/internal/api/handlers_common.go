package api

import (
	"context"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/domain"
	"github.com/windrose-ai/case-service/internal/events"
)

// chiURLParam is a thin wrapper so handler files need not import chi directly.
func chiURLParam(r *http.Request, key string) string { return chi.URLParam(r, key) }

// mkActivity builds a timeline entry from op + old/new values.
func mkActivity(op domain.Op, eventType string, oldV, newV any) domain.Activity {
	return domain.Activity{
		ID: domain.NewID(), EventType: eventType, ActorType: op.Actor.Type, ActorID: op.Actor.ID,
		ViaAgent: op.ViaAgent, OldValue: oldV, NewValue: newV, OccurredAt: time.Now().UTC(),
	}
}

// ifMatchVersion parses an If-Match header carrying the expected case_version.
func ifMatchVersion(r *http.Request) *int {
	v := strings.Trim(r.Header.Get("If-Match"), `"`)
	if v == "" {
		return nil
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return nil
	}
	return &n
}

func atoiDefault(s string, def int) int {
	if s == "" {
		return def
	}
	n, err := strconv.Atoi(s)
	if err != nil {
		return def
	}
	return n
}

func splitComma(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

// validateCustomFields rejects custom-field keys not defined in the workspace
// catalog (CASE-FR-023). An empty catalog with no provided fields is fine.
func (s *Server) validateCustomFields(ctx context.Context, tenant, ws uuid.UUID, queryURN string, fields map[string]any) error {
	if len(fields) == 0 {
		return nil
	}
	defs, err := s.Store.ListFields(ctx, tenant, ws, queryURN, nil)
	if err != nil {
		return err
	}
	known := map[string]bool{}
	for _, d := range defs {
		known[d.Name] = true
	}
	for k := range fields {
		if !known[k] {
			return domain.EValidation("unknown custom field: "+k, nil)
		}
	}
	return nil
}

var _ = events.Topic
