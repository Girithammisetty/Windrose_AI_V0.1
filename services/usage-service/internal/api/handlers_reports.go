package api

import (
	"encoding/base64"
	"encoding/csv"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/windrose-ai/usage-service/internal/domain"
	"github.com/windrose-ai/usage-service/internal/store"
)

func parseDate(s string) (time.Time, bool) {
	if s == "" {
		return time.Time{}, false
	}
	t, err := time.Parse("2006-01-02", s)
	if err != nil {
		return time.Time{}, false
	}
	return t, true
}

// handleReportUsage serves showback rollups; CSV via Accept header
// (USG-FR-040/041). Range capped at 400 days (BR-8).
func (s *Server) handleReportUsage(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	q := r.URL.Query()

	from, okF := parseDate(q.Get("from"))
	to, okT := parseDate(q.Get("to"))
	if !okF || !okT || to.Before(from) {
		writeErrCode(w, r, http.StatusBadRequest, "VALIDATION_FAILED", "from/to must be YYYY-MM-DD with to>=from", nil)
		return
	}
	if to.Sub(from) > 400*24*time.Hour {
		writeErrCode(w, r, http.StatusBadRequest, "VALIDATION_FAILED", "range exceeds 400 days", nil)
		return
	}
	var groupBy []string
	if g := q.Get("group_by"); g != "" {
		groupBy = strings.Split(g, ",")
	}
	limit := 50
	if l, err := strconv.Atoi(q.Get("limit")); err == nil && l > 0 && l <= 200 {
		limit = l
	}
	offset := 0
	if cur := q.Get("cursor"); cur != "" {
		if b, err := base64.RawURLEncoding.DecodeString(cur); err == nil {
			if o, err := strconv.Atoi(string(b)); err == nil {
				offset = o
			}
		}
	}

	sq := store.ShowbackQuery{
		GroupBy:     groupBy,
		From:        from,
		To:          to,
		MeterKey:    q.Get("meter_key"),
		WorkspaceID: q.Get("workspace_id"),
		Limit:       limit,
		Offset:      offset,
	}
	rows, err := s.Store.QueryUsage(r.Context(), op.Tenant, sq)
	if err != nil {
		if err == domain.ErrValidation {
			writeErrCode(w, r, http.StatusBadRequest, "VALIDATION_FAILED", "invalid group_by", nil)
			return
		}
		writeErr(w, r, err)
		return
	}

	// Layer USD when a rate card resolves for this tenant at the range start.
	prices, _, _ := s.Store.ResolvePrices(r.Context(), op.Tenant, from)
	for i := range rows {
		if p, ok := prices[rows[i].MeterKey]; ok {
			usd := rows[i].Quantity * p
			rows[i].USD = &usd
		}
	}

	if strings.Contains(r.Header.Get("Accept"), "text/csv") {
		s.streamCSV(w, groupBy, rows)
		return
	}

	next := ""
	hasMore := len(rows) == limit
	if hasMore {
		next = base64.RawURLEncoding.EncodeToString([]byte(strconv.Itoa(offset + limit)))
	}
	writePage(w, rows, next, hasMore)
}

// streamCSV writes an RFC-4180 report with no buffering (USG-FR-041).
func (s *Server) streamCSV(w http.ResponseWriter, groupBy []string, rows []domain.RollupRow) {
	w.Header().Set("Content-Type", "text/csv; charset=utf-8")
	w.Header().Set("Content-Disposition", "attachment; filename=usage_report.csv")
	w.WriteHeader(http.StatusOK)
	cw := csv.NewWriter(w)
	defer cw.Flush()

	header := append([]string{}, groupBy...)
	header = append(header, "meter_key", "unit", "quantity", "usd")
	_ = cw.Write(header)
	for _, row := range rows {
		rec := make([]string, 0, len(header))
		for _, g := range groupBy {
			rec = append(rec, groupValue(row, g))
		}
		usd := ""
		if row.USD != nil {
			usd = strconv.FormatFloat(*row.USD, 'f', -1, 64)
		}
		rec = append(rec, row.MeterKey, row.Unit, strconv.FormatFloat(row.Quantity, 'f', -1, 64), usd)
		_ = cw.Write(rec)
	}
}

func groupValue(row domain.RollupRow, g string) string {
	deref := func(p *string) string {
		if p == nil {
			return ""
		}
		return *p
	}
	switch g {
	case "workspace":
		return deref(row.WorkspaceID)
	case "user":
		return deref(row.UserID)
	case "agent":
		return deref(row.AgentID)
	case "model":
		return deref(row.Model)
	case "meter":
		return row.MeterKey
	case "day":
		return deref(row.Day)
	case "month":
		return deref(row.Month)
	}
	return ""
}

// handleChargeback returns priced monthly rollups for a finalized month
// (USG-FR-043). Blocked while reconciliation variance is unresolved (AC-9).
func (s *Server) handleChargeback(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	month := r.URL.Query().Get("month")
	if _, err := time.Parse("2006-01", month); err != nil {
		writeErrCode(w, r, http.StatusBadRequest, "VALIDATION_FAILED", "month must be YYYY-MM", nil)
		return
	}
	status, err := s.Store.ReconciliationStatus(r.Context(), month)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if status == domain.ReconVariance {
		writeErrCode(w, r, http.StatusConflict, "CONFLICT", "chargeback blocked", map[string]any{"reason": "reconciliation_variance"})
		return
	}
	lines, err := s.Store.Chargeback(r.Context(), op.Tenant, month)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writePage(w, lines, "", false)
}
