package otelx

import (
	"strings"
	"testing"

	semconv "go.opentelemetry.io/otel/semconv/v1.26.0"
)

// TestBuildResourceSetsTheGivenServiceName reproduces a real bug found via
// live verification against a real Tempo backend (BRD 58 WS2): resource.
// Merge's two sides can carry different semconv SchemaURLs (resource.
// Default()'s builtin detectors are pinned to whatever version the SDK
// dependency bundles internally, independent of the version this package
// imports) — when they differ, Merge returns ErrSchemaURLConflict, and the
// old code silently fell back to resource.Default() ALONE, discarding the
// given service name entirely. Every trace from every service reported
// service.name as "unknown_service:<binary-name>" instead of the real name
// whenever tracing was enabled — invisible until now because tracing had
// never actually been driven against a real backend before this workstream.
func TestBuildResourceSetsTheGivenServiceName(t *testing.T) {
	res := buildResource("my-real-service-name")
	name, ok := res.Set().Value(semconv.ServiceNameKey)
	if !ok {
		t.Fatal("expected a service.name attribute on the built resource")
	}
	got := name.AsString()
	if got != "my-real-service-name" {
		t.Fatalf("service.name = %q, want %q (bug: silently fell back to resource.Default())", got, "my-real-service-name")
	}
	if strings.HasPrefix(got, "unknown_service") {
		t.Fatalf("service.name %q looks like the unconfigured resource.Default() fallback", got)
	}
}

func TestBuildResourceStillCarriesDefaultAttributes(t *testing.T) {
	// The merge must ADD the service name, not replace the whole resource —
	// resource.Default()'s own attributes (telemetry.sdk.*, etc.) survive.
	res := buildResource("svc")
	if _, ok := res.Set().Value(semconv.TelemetrySDKLanguageKey); !ok {
		t.Fatal("expected resource.Default()'s telemetry.sdk.language attribute to survive the merge")
	}
}
