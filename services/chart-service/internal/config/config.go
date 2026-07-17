// Package config centralizes chart-service's environment configuration and the
// adapter wiring. Crucially, every adapter is REAL BY DEFAULT — there is no
// env flag that swaps in a stub. The only in-memory doubles live in *_test.go
// and are never reachable from BuildCore. TestBootDefaultAdapters proves this
// by introspecting the adapter types produced under the default environment.
package config

import (
	"os"
	"strings"

	"github.com/windrose-ai/chart-service/internal/authz"
	"github.com/windrose-ai/chart-service/internal/cache"
	"github.com/windrose-ai/chart-service/internal/resolve"
	"github.com/windrose-ai/go-common/authjwt"
	"github.com/windrose-ai/go-common/kafka"
	"github.com/windrose-ai/go-common/redisx"
)

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// Config holds all runtime settings (real-infra endpoints).
type Config struct {
	ListenAddr         string
	MigrateDatabaseURL string // owner DSN (runs migrations, creates chart_app)
	DatabaseURL        string // runtime DSN — NON-owner chart_app role
	RedisAddr          string
	OPAURL             string
	JWKSURL            string
	JWTIssuer          string
	JWTAudience        string
	KafkaBrokers       string
	SchemaRegistryURL  string
	SemanticURL        string
	QueryURL           string
	ExperimentURL      string
	DatasetURL         string
	DefaultModel       string
	PublicURL          string
	ExportRoot         string
	ExportSecret       string
	PNGRenderer        string
	RBACURL            string
	SigningKeyPEM      string
	SigningKID         string
}

// Load reads configuration from the environment with real-by-default values.
// The runtime DSN ships as the NON-owner chart_app role (RLS is authoritative);
// migrations run under the owner DSN.
func Load() Config {
	return Config{
		ListenAddr:         env("LISTEN_ADDR", ":8087"),
		MigrateDatabaseURL: env("MIGRATE_DATABASE_URL", "postgres://windrose:windrose_dev@localhost:5432/chart?sslmode=disable"),
		DatabaseURL:        env("DATABASE_URL", "postgres://chart_app:chart_app@localhost:5432/chart?sslmode=disable"),
		RedisAddr:          env("REDIS_ADDR", "localhost:6379"),
		OPAURL:             env("OPA_URL", "http://localhost:8281"),
		JWKSURL:            env("JWKS_URL", "http://identity-service/api/v1/.well-known/jwks.json"),
		JWTIssuer:          os.Getenv("JWT_ISSUER"),
		JWTAudience:        os.Getenv("JWT_AUDIENCE"),
		KafkaBrokers:       env("KAFKA_BROKERS", "localhost:9092"),
		SchemaRegistryURL:  os.Getenv("SCHEMA_REGISTRY_URL"),
		SemanticURL:        env("SEMANTIC_SERVICE_URL", "http://localhost:8086"),
		QueryURL:           env("QUERY_SERVICE_URL", "http://localhost:8085"),
		ExperimentURL:      os.Getenv("EXPERIMENT_SERVICE_URL"),
		DatasetURL:         os.Getenv("DATASET_SERVICE_URL"),
		DefaultModel:       os.Getenv("SEMANTIC_DEFAULT_MODEL"),
		PublicURL:          env("PUBLIC_URL", "http://localhost:8087"),
		ExportRoot:         env("EXPORT_ROOT", "/var/lib/chart-service/exports"),
		ExportSecret:       os.Getenv("EXPORT_SIGNING_SECRET"),
		PNGRenderer:        os.Getenv("PNG_RENDERER_URL"),
		RBACURL:            env("RBAC_URL", "http://localhost:8081"),
		SigningKeyPEM:      os.Getenv("PLATFORM_SIGNING_KEY_PEM"),
		SigningKID:         os.Getenv("PLATFORM_SIGNING_KID"),
	}
}

// Core holds the constructed real adapters that need no DB pool. Constructors
// do not dial, so this is safe to build (and introspect) without live infra.
type Core struct {
	Authz    *authz.OPA
	Cache    *cache.Redis
	Resolver *resolve.Resolver
	Verifier *authjwt.Verifier
	Producer *kafka.Producer
	Redis    *redisx.Client
}

// BuildCore constructs the real adapters from cfg. No env branch selects a fake.
func BuildCore(cfg Config) *Core {
	rc := redisx.NewFromEnv(cfg.RedisAddr, os.Getenv)
	sem := resolve.NewHTTPSemantic(cfg.SemanticURL)
	qry := resolve.NewHTTPQuery(cfg.QueryURL)
	var arts resolve.ArtifactFetcher
	if cfg.ExperimentURL != "" || cfg.DatasetURL != "" {
		arts = resolve.NewHTTPArtifacts(cfg.ExperimentURL, cfg.DatasetURL)
	}
	var producer *kafka.Producer
	if strings.ToLower(cfg.KafkaBrokers) != "false" {
		producer = kafka.NewProducer(kafka.Config{
			Brokers: strings.Split(cfg.KafkaBrokers, ","),
			SASL:    kafka.SASLFromEnv(os.Getenv), TLS: kafka.TLSFromEnv(os.Getenv),
		})
	}
	return &Core{
		Authz:    authz.NewOPA(cfg.OPAURL, cfg.RedisAddr),
		Cache:    cache.NewRedis(rc),
		Resolver: &resolve.Resolver{Semantic: sem, Query: qry, Artifacts: arts, DefaultModel: cfg.DefaultModel},
		Verifier: authjwt.NewJWKS(cfg.JWKSURL, cfg.JWTIssuer, cfg.JWTAudience),
		Producer: producer,
		Redis:    rc,
	}
}

// AdapterReport names the concrete adapter types wired under a config — the
// boot-introspection surface (proves real-by-default, no stubs).
type AdapterReport struct {
	Authz    string
	Cache    string
	Semantic string
	Query    string
	Verifier string
	Producer string
}

// Describe reports the concrete adapter types BuildCore produced.
func (c *Core) Describe() AdapterReport {
	rep := AdapterReport{
		Authz:    typeName(c.Authz),
		Cache:    typeName(c.Cache),
		Verifier: verifierMode(c.Verifier),
	}
	if r := c.Resolver; r != nil {
		rep.Semantic = typeName(r.Semantic)
		rep.Query = typeName(r.Query)
	}
	rep.Producer = typeName(c.Producer)
	return rep
}

func typeName(v any) string {
	if v == nil {
		return "<nil>"
	}
	return strings.TrimPrefix(sprintfType(v), "*")
}
