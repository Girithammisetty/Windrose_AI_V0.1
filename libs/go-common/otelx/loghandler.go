package otelx

import (
	"context"
	"log/slog"

	"go.opentelemetry.io/otel/trace"
)

// traceHandler wraps an slog.Handler, adding trace_id/span_id attributes from
// the record's context's active OTel span (BRD 58 WS2). A record with no
// active span in ctx (tracing disabled, or a call site that doesn't thread
// ctx through) passes through completely unchanged -- no attrs added, so the
// existing log shape for every untouched call site is preserved exactly.
//
// Coverage note: this only correlates log lines made via the *Context slog
// methods (slog.InfoContext(ctx, ...), etc.) or through a logger whose
// Handle receives a real ctx -- Go's slog has no ambient/implicit context
// the way Python's contextvars-backed OTel API does, so a plain slog.Info(...)
// call site (the majority of existing call sites today) is NOT retrofitted by
// this wrapper alone. Broad adoption of ctx-aware logging across call sites
// is a separate, larger effort intentionally out of scope here; this wires
// the correlation mechanism correctly wherever ctx is already threaded.
type traceHandler struct {
	slog.Handler
}

// WrapLogHandler returns h wrapped with trace-id/span-id correlation. Install
// once per service in place of the bare handler, e.g.:
//
//	slog.SetDefault(slog.New(otelx.WrapLogHandler(slog.NewJSONHandler(os.Stdout, nil))))
func WrapLogHandler(h slog.Handler) slog.Handler {
	return &traceHandler{Handler: h}
}

func (t *traceHandler) Handle(ctx context.Context, r slog.Record) error {
	if sc := trace.SpanContextFromContext(ctx); sc.IsValid() {
		r.AddAttrs(
			slog.String("trace_id", sc.TraceID().String()),
			slog.String("span_id", sc.SpanID().String()),
		)
	}
	return t.Handler.Handle(ctx, r)
}

func (t *traceHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	return &traceHandler{Handler: t.Handler.WithAttrs(attrs)}
}

func (t *traceHandler) WithGroup(name string) slog.Handler {
	return &traceHandler{Handler: t.Handler.WithGroup(name)}
}
