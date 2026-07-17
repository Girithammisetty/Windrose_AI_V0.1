// Package recon computes provider-bill vs metered variance (USG-FR-070/071).
// Bill line items arrive as CSV from an object-storage prefix; the mapping to
// meters is a static table. Pure helpers here; job wiring in cmd/server.
package recon

import (
	"encoding/csv"
	"fmt"
	"io"
	"math"
	"strconv"
	"strings"

	"github.com/windrose-ai/usage-service/internal/domain"
)

// llmMeters carry the tighter 5% variance threshold; infra meters get 10%
// (USG-FR-071).
var llmMeters = map[string]bool{
	domain.MeterLLMInputTokens:  true,
	domain.MeterLLMOutputTokens: true,
}

// MeterVariance is one reconciled meter line.
type MeterVariance struct {
	MeterKey    string  `json:"meter_key"`
	Metered     float64 `json:"metered"`
	Billed      float64 `json:"billed"`
	VariancePct float64 `json:"variance_pct"`
	Blocking    bool    `json:"blocking"`
}

// ParseBillCSV parses a provider bill export mapping line items to meters via
// the header row. Expected columns: meter_key, billed_quantity (RFC 4180).
func ParseBillCSV(r io.Reader) (map[string]float64, error) {
	cr := csv.NewReader(r)
	cr.TrimLeadingSpace = true
	rows, err := cr.ReadAll()
	if err != nil {
		return nil, err
	}
	if len(rows) < 1 {
		return map[string]float64{}, nil
	}
	header := rows[0]
	mkCol, qtyCol := -1, -1
	for i, h := range header {
		switch strings.ToLower(strings.TrimSpace(h)) {
		case "meter_key", "meter":
			mkCol = i
		case "billed_quantity", "billed", "quantity":
			qtyCol = i
		}
	}
	if mkCol < 0 || qtyCol < 0 {
		return nil, fmt.Errorf("bill csv: missing meter_key/billed_quantity columns")
	}
	out := map[string]float64{}
	for _, row := range rows[1:] {
		if len(row) <= mkCol || len(row) <= qtyCol {
			continue
		}
		q, err := strconv.ParseFloat(strings.TrimSpace(row[qtyCol]), 64)
		if err != nil {
			continue
		}
		out[strings.TrimSpace(row[mkCol])] += q
	}
	return out, nil
}

// Compute compares metered vs billed per meter and flags blocking variances
// (USG-FR-071). A meter present in either map is reported.
func Compute(metered, billed map[string]float64) ([]MeterVariance, bool) {
	seen := map[string]bool{}
	var out []MeterVariance
	anyBlocking := false
	add := func(mk string) {
		if seen[mk] {
			return
		}
		seen[mk] = true
		m, b := metered[mk], billed[mk]
		var pct float64
		switch {
		case b != 0:
			pct = (m - b) / b * 100
		case m != 0:
			pct = 100
		}
		threshold := 10.0
		if llmMeters[mk] {
			threshold = 5.0
		}
		blocking := math.Abs(pct) > threshold
		if blocking {
			anyBlocking = true
		}
		out = append(out, MeterVariance{MeterKey: mk, Metered: m, Billed: b, VariancePct: pct, Blocking: blocking})
	}
	for mk := range metered {
		add(mk)
	}
	for mk := range billed {
		add(mk)
	}
	return out, anyBlocking
}
