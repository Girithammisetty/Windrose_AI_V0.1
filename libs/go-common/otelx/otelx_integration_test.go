//go:build integration

// Integration test: initialize the real OTLP/gRPC exporter against the local
// collector (deploy: localhost:4317), emit a span, and flush on shutdown.
package otelx

import (
	"context"
	"os"
	"testing"
	"time"

	"go.opentelemetry.io/otel"
)

func endpoint() string {
	if e := os.Getenv("OTEL_ENDPOINT"); e != "" {
		return e
	}
	return "localhost:4317"
}

func TestOTLPExportReal(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	shutdown, err := Init(ctx, Config{ServiceName: "go-common-test", Endpoint: endpoint(), Insecure: true})
	if err != nil {
		t.Fatalf("init: %v", err)
	}

	tr := otel.Tracer("otelx-test")
	_, span := tr.Start(ctx, "integration-span")
	span.AddEvent("hello")
	span.End()

	// Shutdown flushes the batch to the collector; error means export failed.
	if err := shutdown(ctx); err != nil {
		t.Fatalf("shutdown/flush failed (collector at %s): %v", endpoint(), err)
	}
}
