package domain

import (
	"encoding/json"
	"fmt"
	"hash/fnv"
	"sort"
	"time"
)

// RawRowCap is the deterministic-sampling cap for raw mode (BR-2 / CHART-FR-021).
const RawRowCap = 10000

// ExecColumn is a column name/type returned by query execution.
type ExecColumn struct {
	Name string `json:"name"`
	Type string `json:"type"`
}

// Shape maps executed columns/rows into the normative per-family shape
// (CHART-FR-020, §5 response-shaping table). The service never aggregates in
// memory when aggregated=true — GROUP BY is applied upstream in SQL; Shape only
// re-orders/renames columns. Raw mode applies deterministic hash-sampling.
func Shape(chart *Chart, cfg ChartConfig, execCols []ExecColumn, rows [][]any, aggregated bool) *ShapedResult {
	ct, _ := LookupType(chart.ChartType)
	res := &ShapedResult{
		ChartID:      chart.ID.String(),
		ChartType:    chart.ChartType,
		ChartVersion: chart.ChartVersion,
		Aggregated:   aggregated,
		ResolvedAt:   time.Now().UTC(),
	}

	switch ct.Family {
	case FamilyNetwork:
		res.Graph = shapeNetwork(cfg, execCols, rows)
		res.Columns = []string{}
		res.RowCount = len(rows)
		return res
	case FamilyGrid:
		res.Columns = passthroughCols(cfg.Columns, execCols)
		res.Rows = rows
		res.RowCount = len(rows)
		return res
	case FamilyMetric:
		// Artifact pass-through is populated by the resolver, not Shape.
		res.Columns = []string{}
		res.RowCount = len(rows)
		return res
	}

	// axis / y_only / heatmap are tabular.
	res.Columns = tabularColumns(ct.Family, cfg, execCols)

	if !aggregated && len(rows) > RawRowCap {
		res.Rows, res.Truncated = sampleRows(rows, chart.ChartVersion, RawRowCap)
	} else {
		res.Rows = rows
	}
	res.RowCount = len(res.Rows)
	return res
}

// tabularColumns builds the normative column-name list for tabular families.
func tabularColumns(family string, cfg ChartConfig, execCols []ExecColumn) []string {
	switch family {
	case FamilyAxis:
		cols := []string{}
		if cfg.X != nil && cfg.X.Dimension != "" {
			cols = append(cols, cfg.X.Dimension)
		} else if len(execCols) > 0 {
			cols = append(cols, execCols[0].Name)
		}
		if cfg.Dataseries != nil && cfg.Dataseries.Dimension != "" {
			cols = append(cols, cfg.Dataseries.Dimension)
		}
		for _, y := range cfg.Y {
			cols = append(cols, measureColName(y))
		}
		return cols
	case FamilyYOnly:
		if len(cfg.Y) == 1 {
			label := "label"
			if cfg.X != nil && cfg.X.Dimension != "" {
				label = cfg.X.Dimension
			}
			return []string{label, measureColName(cfg.Y[0])}
		}
		cols := []string{}
		for _, y := range cfg.Y {
			cols = append(cols, measureColName(y))
		}
		return cols
	case FamilyHeatmap:
		// x, y, value — x/y are dimensions (cfg.YDim, populated by ParseConfig's
		// heatmap-specific parse of "y"); "value" is a fixed generic label since
		// this family resolves via a saved query rather than a named measure
		// (matches FamilyNetwork's saved-query-only path — chart-service labels
		// the query's result columns generically instead of deriving names from
		// a semantic measure).
		xname, yname := "x", "y"
		if cfg.X != nil && cfg.X.Dimension != "" {
			xname = cfg.X.Dimension
		}
		if cfg.YDim != nil && cfg.YDim.Dimension != "" {
			yname = cfg.YDim.Dimension
		}
		return []string{xname, yname, "value"}
	}
	// fallback: echo exec columns.
	names := make([]string, len(execCols))
	for i, c := range execCols {
		names[i] = c.Name
	}
	return names
}

func measureColName(y MeasureRef) string {
	if y.AggFn != "" {
		return y.AggFn + "_" + y.Measure
	}
	return y.Measure
}

func passthroughCols(configCols []string, execCols []ExecColumn) []string {
	if len(configCols) > 0 {
		return configCols
	}
	names := make([]string, len(execCols))
	for i, c := range execCols {
		names[i] = c.Name
	}
	return names
}

// shapeNetwork builds the {nodes, edges} object shape from tabular rows.
// Row layout is [node, child, value?] driven by cfg.Nodes/Children/NodeValues.
func shapeNetwork(cfg ChartConfig, execCols []ExecColumn, rows [][]any) json.RawMessage {
	type node struct {
		ID    any `json:"id"`
		Value any `json:"value,omitempty"`
	}
	type edge struct {
		From  any `json:"from"`
		To    any `json:"to"`
		Value any `json:"value,omitempty"`
	}
	seen := map[string]bool{}
	var nodes []node
	var edges []edge
	addNode := func(id, val any) {
		if id == nil {
			return
		}
		k := fmt.Sprintf("%v", id)
		if seen[k] {
			return
		}
		seen[k] = true
		nodes = append(nodes, node{ID: id, Value: val})
	}
	for _, r := range rows {
		var parent, child, val any
		if len(r) > 0 {
			parent = r[0]
		}
		if len(r) > 1 {
			child = r[1]
		}
		if len(r) > 2 {
			val = r[2]
		}
		addNode(parent, val)
		addNode(child, nil)
		if parent != nil && child != nil {
			edges = append(edges, edge{From: parent, To: child, Value: val})
		}
	}
	if nodes == nil {
		nodes = []node{}
	}
	if edges == nil {
		edges = []edge{}
	}
	out, _ := json.Marshal(map[string]any{"nodes": nodes, "edges": edges})
	return out
}

// sampleRows deterministically samples rows down to cap, stable per version
// (BR-2). Selection order = ascending hash(version, rowKey); ties broken by
// original index for full determinism.
func sampleRows(rows [][]any, version, cap int) ([][]any, bool) {
	if len(rows) <= cap {
		return rows, false
	}
	type scored struct {
		idx int
		h   uint64
	}
	scoredRows := make([]scored, len(rows))
	for i, r := range rows {
		hh := fnv.New64a()
		_, _ = fmt.Fprintf(hh, "%d|", version)
		for _, cell := range r {
			_, _ = fmt.Fprintf(hh, "%v|", cell)
		}
		scoredRows[i] = scored{idx: i, h: hh.Sum64()}
	}
	sort.Slice(scoredRows, func(i, j int) bool {
		if scoredRows[i].h != scoredRows[j].h {
			return scoredRows[i].h < scoredRows[j].h
		}
		return scoredRows[i].idx < scoredRows[j].idx
	})
	pick := scoredRows[:cap]
	// restore original ordering among the picked set for a stable render.
	sort.Slice(pick, func(i, j int) bool { return pick[i].idx < pick[j].idx })
	out := make([][]any, cap)
	for i, s := range pick {
		out[i] = rows[s.idx]
	}
	return out, true
}
