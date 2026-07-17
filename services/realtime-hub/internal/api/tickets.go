package api

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/go-common/httpx"
	"github.com/windrose-ai/realtime-hub/internal/store"
	"github.com/windrose-ai/realtime-hub/internal/topics"
)

var errUnauthenticated = errors.New("unauthenticated")

// TicketTTL is the single-use ticket lifetime (RTH-FR-011).
const TicketTTL = 30 * time.Second

// ticketData is the Redis-hot ticket record (§4: SETEX ticket:<id>).
type ticketData struct {
	Tenant  string    `json:"tenant"`
	Subject string    `json:"subject"`
	Typ     string    `json:"typ"`
	Scopes  []string  `json:"scopes"`
	Topics  []string  `json:"topics"`
	IPHash  string    `json:"ip_hash"`
	Exp     time.Time `json:"exp"`
}

func ticketKey(id string) string { return "rt:ticket:" + id }

// handleMintTicket mints a single-use connect ticket (RTH-FR-011): 30s TTL,
// bound to (subject, tenant, topics, IP hash). Keeps tokens out of URLs/logs.
func (s *Server) handleMintTicket(w http.ResponseWriter, r *http.Request) {
	claims := claimsFrom(r.Context())
	if claims == nil {
		writeErr(w, r, http.StatusUnauthorized, "UNAUTHENTICATED", "missing claims", 0)
		return
	}
	var body struct {
		Topics []string `json:"topics"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeErr(w, r, http.StatusBadRequest, httpx.CodeValidation, "invalid body", 0)
		return
	}
	// Validate topic grammar up front (RTH-FR-003).
	for _, raw := range body.Topics {
		if _, err := topics.Parse(raw); err != nil {
			writeErr(w, r, http.StatusUnprocessableEntity, "INVALID_TOPIC", "invalid topic: "+raw, 0)
			return
		}
	}
	id := uuid.NewString()
	ipHash := hashIP(clientIP(r))
	var exp time.Time
	if claims.ExpiresAt != nil {
		exp = claims.ExpiresAt.Time
	}
	td := ticketData{
		Tenant: claims.TenantID, Subject: claims.EffectiveUser(), Typ: claims.Typ,
		Scopes: claims.Scopes, Topics: body.Topics, IPHash: ipHash, Exp: exp,
	}
	raw, _ := json.Marshal(td)
	if err := s.Redis.Set(r.Context(), ticketKey(id), raw, TicketTTL); err != nil {
		writeErr(w, r, http.StatusInternalServerError, "INTERNAL", "ticket store failed", 0)
		return
	}
	// Best-effort durable audit copy (§4).
	if s.Store != nil {
		if tid, err := uuid.Parse(claims.TenantID); err == nil {
			_ = s.Store.InsertTicketAudit(r.Context(), store.TicketAudit{
				ID: uuid.MustParse(id), Tenant: tid, Subject: claims.EffectiveUser(),
				Topics: body.Topics, IPHash: ipHash, ExpiresAt: time.Now().Add(TicketTTL),
			})
		}
	}
	httpx.WriteJSON(w, http.StatusCreated, map[string]any{
		"data": map[string]any{"ticket": id, "expires_in": int(TicketTTL.Seconds())},
	})
}

// consumeTicket redeems a ticket exactly once (GETDEL) and enforces the IP
// binding (RTH-FR-011 / AC-12). Reuse fails (the key is gone).
func (s *Server) consumeTicket(ctx context.Context, id, ipHash string) (*connIdentity, error) {
	raw, err := s.Redis.R.GetDel(ctx, ticketKey(id)).Result()
	if err != nil || raw == "" {
		return nil, errUnauthenticated
	}
	var td ticketData
	if err := json.Unmarshal([]byte(raw), &td); err != nil {
		return nil, errUnauthenticated
	}
	if td.IPHash != "" && ipHash != "" && td.IPHash != ipHash {
		return nil, errUnauthenticated
	}
	return &connIdentity{
		Subject: td.Subject, Tenant: td.Tenant, Typ: td.Typ, Scopes: td.Scopes,
		Topics: td.Topics, Exp: td.Exp, IPHash: ipHash,
	}, nil
}

func clientIP(r *http.Request) string {
	if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
		return strings.TrimSpace(strings.Split(xff, ",")[0])
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}

func hashIP(ip string) string {
	if ip == "" {
		return ""
	}
	sum := sha256.Sum256([]byte(ip))
	return hex.EncodeToString(sum[:])[:16]
}
