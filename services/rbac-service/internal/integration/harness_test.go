// Package integration holds the Docker-backed test tier (Testcontainers:
// Postgres + Redis). It auto-skips with a clear message when Docker is
// unavailable, and is excluded from `make test-unit` via -short.
package integration

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"sync/atomic"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	goredis "github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/require"
	tcpostgres "github.com/testcontainers/testcontainers-go/modules/postgres"
	tcredis "github.com/testcontainers/testcontainers-go/modules/redis"

	"github.com/windrose-ai/rbac-service/internal/api"
	"github.com/windrose-ai/rbac-service/internal/authz"
	"github.com/windrose-ai/rbac-service/internal/domain"
	"github.com/windrose-ai/rbac-service/internal/events"
	"github.com/windrose-ai/rbac-service/internal/projection"
	"github.com/windrose-ai/rbac-service/internal/store"
	"github.com/windrose-ai/rbac-service/seed"
)

type harness struct {
	store    *store.Store
	writer   *projection.RedisWriter
	reader   *projection.RedisReader
	redis    *goredis.Client
	checker  *authz.Checker
	lock     *projection.UserLock
	pub      *events.InMemoryPublisher
	httpSrv  *httptest.Server
	key      *rsa.PrivateKey
	fallback atomic.Int64
	cancel   context.CancelFunc
}

var (
	h          *harness
	skipReason string
)

func requireHarness(t *testing.T) *harness {
	t.Helper()
	if h == nil {
		t.Skip("integration tests skipped: " + skipReason)
	}
	return h
}

func TestMain(m *testing.M) {
	flag.Parse()
	if testing.Short() {
		skipReason = "-short mode (unit tier)"
		os.Exit(m.Run())
	}
	ctx := context.Background()

	pg, err := tcpostgres.Run(ctx, "postgres:16-alpine",
		tcpostgres.WithDatabase("rbac"),
		tcpostgres.WithUsername("postgres"),
		tcpostgres.WithPassword("postgres"),
		tcpostgres.BasicWaitStrategies(),
	)
	if err != nil {
		skipReason = "Docker unavailable (could not start Postgres container): " + err.Error()
		os.Exit(m.Run())
	}
	defer func() { _ = pg.Terminate(ctx) }()

	rd, err := tcredis.Run(ctx, "redis:7-alpine")
	if err != nil {
		skipReason = "Docker unavailable (could not start Redis container): " + err.Error()
		os.Exit(m.Run())
	}
	defer func() { _ = rd.Terminate(ctx) }()

	if err := setup(ctx, pg, rd); err != nil {
		log.Printf("integration harness setup failed: %v", err)
		os.Exit(1)
	}
	code := m.Run()
	h.cancel()
	h.httpSrv.Close()
	os.Exit(code)
}

func setup(ctx context.Context, pg *tcpostgres.PostgresContainer, rd *tcredis.RedisContainer) error {
	superURL, err := pg.ConnectionString(ctx, "sslmode=disable")
	if err != nil {
		return err
	}

	// Migrations run as the schema owner (bypasses RLS as owner).
	if err := store.Migrate(superURL); err != nil {
		return fmt.Errorf("migrate: %w", err)
	}

	// The app connects as a plain role subject to RLS — production shape.
	superPool, err := pgxpool.New(ctx, superURL)
	if err != nil {
		return err
	}
	_, err = superPool.Exec(ctx, `
		CREATE ROLE rbac_app LOGIN PASSWORD 'rbac_app';
		GRANT USAGE ON SCHEMA public TO rbac_app;
		GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO rbac_app;
		GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rbac_app;
	`)
	superPool.Close()
	if err != nil {
		return fmt.Errorf("create app role: %w", err)
	}

	host, err := pg.Host(ctx)
	if err != nil {
		return err
	}
	port, err := pg.MappedPort(ctx, "5432/tcp")
	if err != nil {
		return err
	}
	appURL := fmt.Sprintf("postgres://rbac_app:rbac_app@%s:%s/rbac?sslmode=disable", host, port.Port())
	appPool, err := pgxpool.New(ctx, appURL)
	if err != nil {
		return err
	}
	st := store.New(appPool)

	redisURI, err := rd.ConnectionString(ctx)
	if err != nil {
		return err
	}
	opts, err := goredis.ParseURL(redisURI)
	if err != nil {
		return err
	}
	rdb := goredis.NewClient(opts)

	// Deploy-time seeding: canonical catalog + system roles.
	if err := st.RegisterActions(ctx, domain.CanonicalCatalog()); err != nil {
		return fmt.Errorf("register actions: %w", err)
	}
	seeds, err := domain.ParseRoleSeeds(seed.RolesActionsYAML)
	if err != nil {
		return err
	}
	if err := st.EnsureSystemRoles(ctx, seeds); err != nil {
		return fmt.Errorf("system roles: %w", err)
	}

	writer := projection.NewRedisWriter(rdb, projection.DefaultTTL)
	if v, err := st.NextVersion(ctx); err == nil {
		catalog, cerr := st.CatalogMap(ctx)
		if cerr != nil {
			return cerr
		}
		if err := writer.WriteCatalog(ctx, catalog, v); err != nil {
			return err
		}
	}

	hh := &harness{
		store:  st,
		writer: writer,
		reader: projection.NewRedisReader(rdb),
		redis:  rdb,
		pub:    events.NewInMemoryPublisher(),
	}
	userLock := projection.NewUserLock(rdb, projection.DefaultLockTTL)
	hh.lock = userLock
	hh.checker = &authz.Checker{Store: st, Writer: writer, Lock: userLock, OnFallback: func() { hh.fallback.Add(1) }}

	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return err
	}
	hh.key = key
	verifier := api.NewVerifierStatic(&key.PublicKey, "windrose-test", "windrose")

	srv := &api.Server{
		Store: st, Checker: hh.checker, Writer: writer, Reader: hh.reader,
		Verifier: verifier, Redis: rdb,
	}
	hh.httpSrv = httptest.NewServer(srv.Router())

	// Background workers: projection recompute + outbox relay.
	wctx, cancel := context.WithCancel(context.Background())
	hh.cancel = cancel
	worker := projection.NewWorker("it-worker", st, writer)
	worker.Interval = 50 * time.Millisecond
	worker.Lock = userLock
	go worker.Run(wctx)
	relay := events.NewOutboxRelay(st, hh.pub)
	relay.Interval = 50 * time.Millisecond
	go relay.Run(wctx)

	h = hh
	return nil
}

// ---- token & request helpers -------------------------------------------------

type tokenSpec struct {
	Sub    string
	Tenant uuid.UUID
	Typ    string
	Scopes []string
	OboSub string
}

func (h *harness) mint(t *testing.T, spec tokenSpec) string {
	t.Helper()
	if spec.Typ == "" {
		spec.Typ = domain.TypUser
	}
	claims := jwt.MapClaims{
		"sub":       spec.Sub,
		"tenant_id": spec.Tenant.String(),
		"typ":       spec.Typ,
		"iss":       "windrose-test",
		"aud":       "windrose",
		"exp":       time.Now().Add(5 * time.Minute).Unix(),
		"iat":       time.Now().Unix(),
	}
	if len(spec.Scopes) > 0 {
		claims["scopes"] = spec.Scopes
	}
	if spec.OboSub != "" {
		claims["obo_sub"] = spec.OboSub
		claims["agent_id"] = "agent-test"
		claims["agent_version"] = "1"
	}
	tok, err := jwt.NewWithClaims(jwt.SigningMethodRS256, claims).SignedString(h.key)
	require.NoError(t, err)
	return tok
}

type resp struct {
	Status int
	Body   []byte
	Header http.Header
}

func (r resp) JSON(t *testing.T, dst any) {
	t.Helper()
	require.NoErrorf(t, json.Unmarshal(r.Body, dst), "body: %s", r.Body)
}

func (r resp) errorCode(t *testing.T) string {
	t.Helper()
	var e struct {
		Error struct {
			Code string `json:"code"`
		} `json:"error"`
	}
	r.JSON(t, &e)
	return e.Error.Code
}

func (h *harness) do(t *testing.T, method, path, token string, body any, headers ...map[string]string) resp {
	t.Helper()
	var rdr io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		require.NoError(t, err)
		rdr = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, h.httpSrv.URL+path, rdr)
	require.NoError(t, err)
	req.Header.Set("Content-Type", "application/json")
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	for _, hs := range headers {
		for k, v := range hs {
			req.Header.Set(k, v)
		}
	}
	res, err := http.DefaultClient.Do(req)
	require.NoError(t, err)
	defer res.Body.Close()
	raw, err := io.ReadAll(res.Body)
	require.NoError(t, err)
	return resp{Status: res.StatusCode, Body: raw, Header: res.Header}
}

// ---- tenant environment -------------------------------------------------------

type tenantEnv struct {
	Tenant     uuid.UUID
	AdminUser  string
	AdminTok   string
	AdminGroup uuid.UUID // system "Admin" permission group
	DefaultWs  uuid.UUID
}

// newTenant seeds a tenant, adds an admin user to the system Admin group and
// returns tokens/ids used across tests.
func (h *harness) newTenant(t *testing.T) *tenantEnv {
	t.Helper()
	ctx := context.Background()
	tenant := uuid.New()
	op := store.Op{Tenant: tenant, Actor: events.Actor{Type: "service", ID: "test-setup"}}
	require.NoError(t, h.store.SeedTenant(ctx, op))

	env := &tenantEnv{Tenant: tenant, AdminUser: "admin-" + uuid.NewString()[:8]}

	groups, err := h.store.ListGroups(ctx, tenant, domain.GroupTypePermission, true, "", 200)
	require.NoError(t, err)
	for _, g := range groups.Data {
		if g.Name == domain.RoleAdmin && g.System {
			env.AdminGroup = g.ID
		}
	}
	require.NotEqual(t, uuid.Nil, env.AdminGroup, "seeded Admin group must exist")
	_, err = h.store.AddMember(ctx, op, env.AdminGroup, env.AdminUser, nil)
	require.NoError(t, err)

	wss, err := h.store.ListWorkspaces(ctx, tenant, env.AdminUser, true, store.ArchivedWith, "", 200)
	require.NoError(t, err)
	for _, w := range wss.Data {
		if w.Name == domain.DefaultWorkspaceName {
			env.DefaultWs = w.ID
		}
	}
	require.NotEqual(t, uuid.Nil, env.DefaultWs, "seeded default workspace must exist")

	env.AdminTok = h.mint(t, tokenSpec{Sub: env.AdminUser, Tenant: tenant})
	return env
}

// waitFor polls until cond returns true or the deadline passes (the 5s
// staleness SLA plus scheduling slack).
func waitFor(t *testing.T, d time.Duration, cond func() bool, msg string) {
	t.Helper()
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatalf("condition not met within %s: %s", d, msg)
}

// decideRedis evaluates via the materialized projection (the OPA-equivalent
// path).
func (h *harness) decideRedis(t *testing.T, in authz.Input) authz.Decision {
	t.Helper()
	d, err := authz.Decide(context.Background(), in, h.reader)
	require.NoError(t, err)
	return d
}
