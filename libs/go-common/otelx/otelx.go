// Package otelx initializes real OpenTelemetry tracing exporting OTLP/gRPC to
// the collector (deploy: localhost:4317). It wires a TracerProvider with a
// batch span processor and installs it globally + the W3C traceparent
// propagator (MASTER-FR-050). Shutdown flushes pending spans.
package otelx

import (
	"context"
	"net/http"
	"os"
	"strings"
	"time"

	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.26.0"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

// Config configures Init.
type Config struct {
	ServiceName string
	Endpoint    string // host:port, e.g. localhost:4317 (defaults to that)
	Insecure    bool   // true in dev (no TLS to the local collector)
}

// noopShutdown is returned when tracing is disabled so callers can always
// `defer shutdown(ctx)` without a nil check.
func noopShutdown(context.Context) error { return nil }

// Enabled reports whether tracing should be installed. It is on when
// DATACERN_OTEL_ENABLED is truthy or an OTEL endpoint is explicitly configured,
// so the default (dev, no collector) is a clean no-op.
func Enabled() bool {
	if v := strings.ToLower(os.Getenv("DATACERN_OTEL_ENABLED")); v == "1" || v == "true" || v == "yes" {
		return true
	}
	return os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT") != ""
}

// InitFromEnv installs the tracer provider iff Enabled(), reading the endpoint
// from OTEL_EXPORTER_OTLP_ENDPOINT (host:port; a leading scheme is stripped).
// It never errors: an unreachable collector is dialed lazily, and when disabled
// it returns a no-op shutdown. This is the one call every service main makes.
func InitFromEnv(ctx context.Context, serviceName string) func(context.Context) error {
	if !Enabled() {
		return noopShutdown
	}
	ep := os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
	ep = strings.TrimPrefix(strings.TrimPrefix(ep, "http://"), "https://")
	shutdown, err := Init(ctx, Config{ServiceName: serviceName, Endpoint: ep, Insecure: true})
	if err != nil {
		return noopShutdown
	}
	return shutdown
}

// WrapHandler wraps an HTTP handler so every request is a server span with the
// W3C context extracted from inbound headers (parenting under the caller). A
// no-op wrapper cost when tracing is disabled is negligible; callers may still
// guard on Enabled() to skip it entirely.
func WrapHandler(h http.Handler, serviceName string) http.Handler {
	return otelhttp.NewHandler(h, serviceName)
}

// Transport returns an http.RoundTripper that injects the W3C traceparent into
// outbound requests and records a client span, so downstream services parent
// under this one. Wrap your http.Client.Transport with it.
func Transport(base http.RoundTripper) http.RoundTripper {
	if base == nil {
		base = http.DefaultTransport
	}
	return otelhttp.NewTransport(base)
}

// buildResource merges the default (auto-detected) resource with the given
// service name. Schemaless (no SchemaURL) rather than a versioned
// semconv.SchemaURL for the custom attribute: resource.Default()'s builtin
// detectors are pinned to whatever semconv version the SDK dependency bundles
// internally, which can silently drift ahead of the semconv version this
// package imports. A schema URL MISMATCH makes resource.Merge return
// ErrSchemaURLConflict — a bug found live against a real Tempo backend (BRD 58
// WS2): the old code silently fell back to resource.Default() alone on that
// error, discarding serviceName entirely, so every trace from every service
// reported service.name as "unknown_service:<binary-name>" instead of the
// real name whenever tracing was enabled. A schemaless resource has an empty
// SchemaURL, which Merge always accepts against either side without
// conflict, making this resilient to future SDK semconv version bumps too.
func buildResource(serviceName string) *resource.Resource {
	res, err := resource.Merge(resource.Default(), resource.NewSchemaless(semconv.ServiceName(serviceName)))
	if err != nil {
		return resource.Default()
	}
	return res
}

// Init sets up the global tracer provider + propagators and returns a shutdown
// func. The exporter dials lazily, so Init does not fail if the collector is
// briefly unavailable at startup.
func Init(ctx context.Context, cfg Config) (func(context.Context) error, error) {
	if cfg.Endpoint == "" {
		cfg.Endpoint = "localhost:4317"
	}
	opts := []otlptracegrpc.Option{
		otlptracegrpc.WithEndpoint(cfg.Endpoint),
		otlptracegrpc.WithDialOption(grpc.WithTransportCredentials(insecure.NewCredentials())),
	}
	if cfg.Insecure {
		opts = append(opts, otlptracegrpc.WithInsecure())
	}
	exp, err := otlptracegrpc.New(ctx, opts...)
	if err != nil {
		return nil, err
	}
	res := buildResource(cfg.ServiceName)
	tp := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exp, sdktrace.WithBatchTimeout(2*time.Second)),
		sdktrace.WithResource(res),
	)
	otel.SetTracerProvider(tp)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{}, propagation.Baggage{},
	))
	return tp.Shutdown, nil
}
