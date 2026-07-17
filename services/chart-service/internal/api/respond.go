package api

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"

	"github.com/google/uuid"

	"github.com/windrose-ai/chart-service/internal/domain"
	"github.com/windrose-ai/go-common/authjwt"
	"github.com/windrose-ai/go-common/httpx"
)

type ctxKey int

const traceKey ctxKey = 0

func traceID(ctx context.Context) string {
	if v, ok := ctx.Value(traceKey).(string); ok {
		return v
	}
	return ""
}

// writeData writes a {data: v} envelope.
func writeData(w http.ResponseWriter, status int, v any) {
	httpx.WriteJSON(w, status, map[string]any{"data": v})
}

// writePage writes a paginated {data, page} envelope (MASTER-FR-022).
func writePage(w http.ResponseWriter, data any, nextCursor *string, hasMore bool) {
	httpx.WriteJSON(w, http.StatusOK, map[string]any{
		"data": data,
		"page": map[string]any{"next_cursor": nextCursor, "has_more": hasMore},
	})
}

// writeErr maps a domain.Error (or generic error) to the master envelope.
func writeErr(w http.ResponseWriter, r *http.Request, err error) {
	tid := traceID(r.Context())
	if de, ok := domain.AsError(err); ok {
		httpx.WriteError(w, de.Status, de.Code, de.Message, tid, de.Details, 0)
		return
	}
	httpx.WriteError(w, http.StatusInternalServerError, httpx.CodeInternal, "internal error", tid, nil, 0)
}

// decodeBody decodes a JSON request body, writing a 422 on failure.
func decodeBody(w http.ResponseWriter, r *http.Request, v any) bool {
	dec := json.NewDecoder(io.LimitReader(r.Body, 1<<20))
	if err := dec.Decode(v); err != nil {
		writeErr(w, r, domain.EValidation("request body is not valid JSON"))
		return false
	}
	return true
}

// claims returns the verified claims + tenant, writing a 401 when absent.
func (s *Server) claims(w http.ResponseWriter, r *http.Request) (*authjwt.Claims, uuid.UUID, bool) {
	c, ok := authjwt.FromContext(r.Context())
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing or invalid token"))
		return nil, uuid.Nil, false
	}
	tenant, err := c.Tenant()
	if err != nil {
		writeErr(w, r, domain.EUnauthenticated("invalid tenant_id claim"))
		return nil, uuid.Nil, false
	}
	return c, tenant, true
}

func authClaims(r *http.Request) (*authjwt.Claims, bool) { return authjwt.FromContext(r.Context()) }

func newTraceID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func encodeCursor(id uuid.UUID) string {
	return base64.RawURLEncoding.EncodeToString([]byte(id.String()))
}

func decodeCursor(cursor string) (*uuid.UUID, error) {
	if cursor == "" {
		return nil, nil
	}
	raw, err := base64.RawURLEncoding.DecodeString(cursor)
	if err != nil {
		return nil, domain.EValidation("invalid cursor")
	}
	id, err := uuid.Parse(string(raw))
	if err != nil {
		return nil, domain.EValidation("invalid cursor")
	}
	return &id, nil
}
