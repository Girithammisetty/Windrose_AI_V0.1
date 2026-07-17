package api

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/domain"
)

// idempotencyMiddleware implements MASTER-FR-025: POST endpoints accept an
// Idempotency-Key header; a duplicate key within 24h replays the original
// response with Idempotency-Replayed: true. A key reused with a different
// body is a 409.
func (s *Server) idempotencyMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		key := r.Header.Get("Idempotency-Key")
		if r.Method != http.MethodPost || key == "" {
			next.ServeHTTP(w, r)
			return
		}
		tenantID := uuid.Nil
		subject := ""
		if claims := ClaimsFrom(r.Context()); claims != nil {
			tenantID = claims.TenantID
			subject = claims.Subject
		}
		// F-4: namespace the idempotency key by the acting subject as well as
		// the tenant. Super-admins all share tenant_id=uuid.Nil, so without
		// this staffer B could receive staffer A's replayed response.
		key = subject + "\x00" + key
		body, err := io.ReadAll(r.Body)
		if err != nil {
			writeErr(w, r, domain.EValidation("unreadable body"))
			return
		}
		r.Body = io.NopCloser(bytes.NewReader(body))
		sum := sha256.Sum256(append([]byte(r.URL.Path+"\n"), body...))
		reqHash := hex.EncodeToString(sum[:])

		if rec, err := s.Store.GetIdempotency(r.Context(), tenantID, key); err == nil {
			if rec.RequestHash != reqHash {
				writeErr(w, r, domain.EConflict("Idempotency-Key reused with a different request"))
				return
			}
			w.Header().Set("Idempotency-Replayed", "true")
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(rec.Status)
			_, _ = w.Write(rec.Body)
			return
		}

		rw := &captureWriter{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(rw, r)
		if rw.status < 500 { // don't memoize server errors
			_ = s.Store.PutIdempotency(r.Context(), &domain.IdempotencyRecord{
				TenantID: tenantID, Key: key, RequestHash: reqHash,
				Status: rw.status, Body: rw.buf.Bytes(), CreatedAt: time.Now().UTC(),
			})
		}
	})
}

type captureWriter struct {
	http.ResponseWriter
	status int
	buf    bytes.Buffer
}

func (c *captureWriter) WriteHeader(status int) {
	c.status = status
	c.ResponseWriter.WriteHeader(status)
}

func (c *captureWriter) Write(b []byte) (int, error) {
	c.buf.Write(b)
	return c.ResponseWriter.Write(b)
}
