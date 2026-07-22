package otelx

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"testing"

	"go.opentelemetry.io/otel"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

func TestWrapLogHandlerAddsTraceAndSpanID(t *testing.T) {
	var buf bytes.Buffer
	logger := slog.New(WrapLogHandler(slog.NewJSONHandler(&buf, nil)))

	tp := sdktrace.NewTracerProvider()
	prevProv := otel.GetTracerProvider()
	otel.SetTracerProvider(tp)
	t.Cleanup(func() { otel.SetTracerProvider(prevProv) })

	ctx, span := tp.Tracer("test").Start(context.Background(), "op")
	want := trace.SpanContextFromContext(ctx)
	logger.InfoContext(ctx, "hello")
	span.End()

	var rec map[string]any
	if err := json.Unmarshal(buf.Bytes(), &rec); err != nil {
		t.Fatalf("unmarshal log line: %v (line: %s)", err, buf.String())
	}
	if rec["trace_id"] != want.TraceID().String() {
		t.Fatalf("trace_id = %v, want %s", rec["trace_id"], want.TraceID().String())
	}
	if rec["span_id"] != want.SpanID().String() {
		t.Fatalf("span_id = %v, want %s", rec["span_id"], want.SpanID().String())
	}
}

func TestWrapLogHandlerNoopWithoutActiveSpan(t *testing.T) {
	var buf bytes.Buffer
	logger := slog.New(WrapLogHandler(slog.NewJSONHandler(&buf, nil)))

	logger.InfoContext(context.Background(), "hello")

	var rec map[string]any
	if err := json.Unmarshal(buf.Bytes(), &rec); err != nil {
		t.Fatalf("unmarshal log line: %v (line: %s)", err, buf.String())
	}
	if _, ok := rec["trace_id"]; ok {
		t.Fatalf("expected no trace_id field with no active span, got record: %v", rec)
	}
	if _, ok := rec["span_id"]; ok {
		t.Fatalf("expected no span_id field with no active span, got record: %v", rec)
	}
	if rec["msg"] != "hello" {
		t.Fatalf("expected the plain log line to pass through unchanged, got: %v", rec)
	}
}

func TestWrapLogHandlerPreservesWithAttrsAndWithGroup(t *testing.T) {
	var buf bytes.Buffer
	logger := slog.New(WrapLogHandler(slog.NewJSONHandler(&buf, nil))).
		With("service", "test-svc").
		WithGroup("req")

	logger.InfoContext(context.Background(), "hello", "path", "/x")

	var rec map[string]any
	if err := json.Unmarshal(buf.Bytes(), &rec); err != nil {
		t.Fatalf("unmarshal log line: %v (line: %s)", err, buf.String())
	}
	if rec["service"] != "test-svc" {
		t.Fatalf("expected With() attrs to survive wrapping, got: %v", rec)
	}
	req, ok := rec["req"].(map[string]any)
	if !ok || req["path"] != "/x" {
		t.Fatalf("expected WithGroup() to survive wrapping, got: %v", rec)
	}
}
