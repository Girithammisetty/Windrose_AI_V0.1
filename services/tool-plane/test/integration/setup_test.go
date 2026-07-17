// Package integration runs the tool-plane acceptance suite against REAL
// infrastructure: a pgvector Postgres (Testcontainers), the running Redis,
// Redpanda (Kafka), OPA sidecar, and Ollama (nomic-embed-text) from
// deploy/docker-compose.dev.yml. No fakes are in any path here — the whole
// store runs under a NOSUPERUSER/NOBYPASSRLS role so Postgres RLS is genuinely
// enforced. Auto-skips with a clear reason when any dependency is unavailable.
package integration

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net"
	"net/url"
	"os"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
	tcpostgres "github.com/testcontainers/testcontainers-go/modules/postgres"

	"github.com/windrose-ai/go-common/authjwt"
	"github.com/windrose-ai/go-common/opaclient"
	"github.com/windrose-ai/go-common/redisx"
	"github.com/windrose-ai/tool-plane/internal/api"
	"github.com/windrose-ai/tool-plane/internal/authz"
	"github.com/windrose-ai/tool-plane/internal/embed"
	"github.com/windrose-ai/tool-plane/internal/enforce"
	"github.com/windrose-ai/tool-plane/internal/mcp"
	"github.com/windrose-ai/tool-plane/internal/store"
	"github.com/windrose-ai/tool-plane/policy"
)

const (
	redisAddr  = "localhost:6379"
	opaURL     = "http://localhost:8281"
	ollamaURL  = "http://localhost:11434/v1"
	kafkaBroker = "localhost:9092"
	issuer     = "https://identity.windrose.test"
	audience   = "windrose"
	grantIssuer = "windrose-agent-runtime"
)

type harness struct {
	pool      *pgxpool.Pool
	adminPool *pgxpool.Pool
	appDSN    string
	store     *store.PG
	rc        *redisx.Client
	kill      *enforce.KillRegistry
	rate      *enforce.RateLimiter
	health    *enforce.HealthStore
	opa       *authz.OPAClient
	embedder  *embed.Ollama
	pipeline  *enforce.Pipeline
	verifier  *authjwt.Verifier
	signKey   *rsa.PrivateKey
	grantKey  *rsa.PrivateKey
	registry  *api.RegistryServer
	gateway   *api.GatewayServer
}

var (
	h          *harness
	skipReason string
)

func mustHarness(t *testing.T) *harness {
	t.Helper()
	if h == nil {
		t.Skip("integration tests skipped: " + skipReason)
	}
	return h
}

func reachable(addr string) bool {
	c, err := net.DialTimeout("tcp", addr, 2*time.Second)
	if err != nil {
		return false
	}
	_ = c.Close()
	return true
}

func TestMain(m *testing.M) {
	flag.Parse()
	if testing.Short() {
		skipReason = "-short mode (unit tier)"
		os.Exit(m.Run())
	}
	// Preflight: the running dev stack must be up (real infra, no fakes).
	for _, dep := range []struct{ name, addr string }{
		{"redis", redisAddr}, {"opa", "localhost:8281"}, {"kafka", kafkaBroker}, {"ollama", "localhost:11434"},
	} {
		if !reachable(dep.addr) {
			skipReason = dep.name + " unavailable at " + dep.addr + " (start deploy/docker-compose.dev.yml + ollama)"
			os.Exit(m.Run())
		}
	}
	ctx := context.Background()

	// Real pgvector Postgres via Testcontainers (semantic discovery needs pgvector).
	pgc, err := tcpostgres.Run(ctx, "pgvector/pgvector:pg16",
		tcpostgres.WithDatabase("tool_plane"),
		tcpostgres.WithUsername("postgres"),
		tcpostgres.WithPassword("postgres"),
		tcpostgres.BasicWaitStrategies(),
	)
	if err != nil {
		skipReason = "Docker/pgvector unavailable (" + err.Error() + ")"
		os.Exit(m.Run())
	}
	defer func() { _ = pgc.Terminate(ctx) }()

	dsn, err := pgc.ConnectionString(ctx, "sslmode=disable")
	if err != nil {
		log.Fatalf("pg dsn: %v", err)
	}
	if err := store.Migrate(dsn); err != nil {
		log.Fatalf("migrations: %v", err)
	}

	// Non-superuser role so RLS is genuinely enforced (superusers bypass RLS).
	adminPool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		log.Fatalf("admin pool: %v", err)
	}
	for _, stmt := range []string{
		`CREATE ROLE tp_app WITH LOGIN PASSWORD 'tp_app' NOSUPERUSER NOBYPASSRLS`,
		`GRANT USAGE ON SCHEMA public TO tp_app`,
		`GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO tp_app`,
		`GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tp_app`,
	} {
		if _, err := adminPool.Exec(ctx, stmt); err != nil {
			log.Fatalf("app role setup (%s): %v", stmt, err)
		}
	}

	u, _ := url.Parse(dsn)
	u.User = url.UserPassword("tp_app", "tp_app")
	appDSN := u.String()
	pool, err := pgxpool.New(ctx, appDSN)
	if err != nil {
		log.Fatalf("app pool: %v", err)
	}
	defer pool.Close()

	st := store.NewPG(pool)
	rc := redisx.Wrap(redis.NewClient(&redis.Options{Addr: redisAddr}))
	kill := enforce.NewKillRegistry(rc)
	_ = kill.SyncFromStore(ctx, st)
	rate := enforce.NewRateLimiter(rc)
	opa := authz.NewOPAClient(opaURL)
	if err := opa.UploadPolicy(ctx, policy.ToolPlaneModuleID, policy.ToolPlaneRego); err != nil {
		skipReason = "OPA policy upload failed: " + err.Error()
		os.Exit(m.Run())
	}
	embedder := embed.NewOllama(ollamaURL, embed.ModelNomic)

	// Static RS256 verifier + signer for minting test agent JWTs.
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		log.Fatalf("key: %v", err)
	}
	verifier := authjwt.NewStatic(&key.PublicKey, issuer, audience)

	// A SEPARATE key stands in for agent-runtime's proposal-grant signing key;
	// tool-plane verifies grants against its public half (like JWKS in prod).
	grantKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		log.Fatalf("grant key: %v", err)
	}
	proposals := authz.NewProposalVerifierStatic(&grantKey.PublicKey, grantIssuer)
	health := enforce.NewHealthStore(rc)

	pipeline := &enforce.Pipeline{
		Catalog:    api.NewCatalogResolver(st),
		Enablement: st,
		Kill:       kill,
		OPA:        opa,
		Rate:       rate,
		Grants:     enforce.NewRedisGrantLoader(rc),
		Backend:    mcp.NewHTTPBackend(),
		Audit:      st,
		Proposals:  proposals,
		Health:     health,
	}

	// Real admin authorizer: OPA sidecar (windrose.authz_input bundle) + Redis
	// projection loader, exactly as cmd/registry wires it. The catalog key is
	// merged (additively — the shared dev Redis may belong to a live stack) so
	// `action_known` holds for tool-plane's actions, standing in for rbac's
	// deploy-time WriteCatalog after this service registers (RBC-FR-022).
	adminAuthz := authz.NewAdminOPA(opaURL, redisAddr)
	if err := seedCatalogActions(ctx, rc); err != nil {
		log.Fatalf("seed catalog actions: %v", err)
	}

	h = &harness{
		pool: pool, adminPool: adminPool, appDSN: appDSN, store: st, rc: rc, kill: kill, rate: rate, health: health,
		opa: opa, embedder: embedder, pipeline: pipeline, verifier: verifier, signKey: key, grantKey: grantKey,
		registry: &api.RegistryServer{Store: st, Embedder: embedder, Kill: kill, Health: health, Verifier: verifier, Authz: adminAuthz},
		gateway:  &api.GatewayServer{Pipeline: pipeline, Store: st, Verifier: verifier, Kill: kill},
	}
	code := m.Run()
	adminPool.Close()
	os.Exit(code)
}

// mintToken signs an RS256 agent/user JWT for the static verifier.
func (h *harness) mintToken(claims authjwt.Claims) string {
	claims.Issuer = issuer
	claims.Audience = jwt.ClaimStrings{audience}
	claims.ExpiresAt = jwt.NewNumericDate(time.Now().Add(5 * time.Minute))
	tok := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	tok.Header["kid"] = "test"
	s, err := tok.SignedString(h.signKey)
	if err != nil {
		panic(err)
	}
	return s
}

// agentToken mints an agent_obo token for (tenant, agent, obo user, scopes).
func (h *harness) agentToken(tenant, agentID, agentVer, oboSub string, scopes []string) string {
	return h.mintToken(authjwt.Claims{
		Sub: "agent:" + agentID, TenantID: tenant, Typ: authjwt.TypAgentOBO,
		AgentID: agentID, AgentVersion: agentVer, OboSub: oboSub, Scopes: scopes,
	})
}

// operatorToken mints a service/operator token.
func (h *harness) operatorToken(tenant string) string {
	return h.mintToken(authjwt.Claims{Sub: "user:ops", TenantID: tenant, Typ: authjwt.TypService, Scopes: []string{"*"}})
}

// signGrant signs an RS256 proposal-execution grant the way agent-runtime will
// (issuer=agent-runtime), binding tenant/tool/tier/args_digest with an expiry.
func (h *harness) signGrant(tenant, toolID, tier, argsDigest string, exp time.Time, overrideIssuer string) string {
	iss := grantIssuer
	if overrideIssuer != "" {
		iss = overrideIssuer
	}
	claims := authz.ProposalGrantClaims{
		ProposalID: "p-" + uuid.NewString(), TenantID: tenant, ToolID: toolID, Tier: tier, ArgsDigest: argsDigest,
		RegisteredClaims: jwt.RegisteredClaims{
			Issuer: iss, Subject: "user:mgr", ExpiresAt: jwt.NewNumericDate(exp),
		},
	}
	tok := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	tok.Header["kid"] = "grant"
	s, err := tok.SignedString(h.grantKey)
	if err != nil {
		panic(err)
	}
	return s
}

// seedCatalogActions merges tool-plane's action manifest into the global rbac
// catalog projection key (perm:catalog:actions, rbac RedisWriter.WriteCatalog
// format) so the windrose.authz_input policy's `action_known` holds. The merge
// is ADDITIVE: existing entries from a live rbac are preserved.
func seedCatalogActions(ctx context.Context, rc *redisx.Client) error {
	actions := map[string]bool{}
	for _, e := range authz.Manifest() {
		actions[e.Action] = e.WorkspaceScoped
	}
	return opaclient.SeedCatalogActions(ctx, rc, actions)
}

// seedTenantAction grants (tenant, user) a tenant-scoped action in the rbac
// permissions_flat projection (real Redis, same key scheme rbac projects), so
// the real OPA admin authorizer allows the caller like a role-granted user.
func (h *harness) seedTenantAction(ctx context.Context, tenant, user string, actions ...string) {
	key := "perm:" + tenant + ":" + user + ":actions"
	val, _ := json.Marshal(map[string]any{"actions": actions})
	if err := h.rc.Set(ctx, key, string(val), time.Hour); err != nil {
		panic(err)
	}
}

// seedGrant writes a resource grant into the rbac Redis projection so the OBO
// intersection check finds it (real Redis, same key scheme as rbac).
func (h *harness) seedGrant(ctx context.Context, tenant, user, urn string) {
	key := "perm:" + tenant + ":" + user + ":res:" + urnHash(urn)
	val, _ := json.Marshal(map[string]any{"level": "editor", "deleted": false})
	if err := h.rc.Set(ctx, key, string(val), time.Hour); err != nil {
		panic(err)
	}
}

func newTenant() uuid.UUID { return uuid.New() }

func urnHash(urn string) string { return opaclient.URNHash(urn) }

var _ = fmt.Sprintf
