package integration

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/go-common/opaclient"
	"github.com/windrose-ai/go-common/redisx"
	"github.com/windrose-ai/usage-service/internal/authz"
	"github.com/windrose-ai/usage-service/internal/domain"
	"github.com/windrose-ai/usage-service/internal/events"
)

// TestAC_RLSCrossTenantEmptyViaShippedRole proves tenant isolation is enforced
// by Postgres RLS under the SHIPPED default runtime role (usage_app, a
// non-owner NOSUPERUSER NOBYPASSRLS role) with FORCE ROW LEVEL SECURITY: rows
// written for tenant A are invisible when the session is pinned to tenant B.
func TestAC_RLSCrossTenantEmptyViaShippedRole(t *testing.T) {
	h := requireHarness(t)
	ctx := context.Background()

	// The pool connects as the non-owner role, and it is not a superuser
	// (superusers/owners with BYPASSRLS would defeat the test).
	role := h.queryStr(t, `SELECT current_user`)
	require.Equal(t, "usage_app", role, "runtime must connect as the shipped non-owner role")
	require.False(t, h.queryBool(t, `SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user`),
		"runtime role must be non-superuser and NOBYPASSRLS")

	tenantA := uuid.New()
	tenantB := uuid.New()
	rec := domain.MeterRecord{Time: time.Now().UTC(), TenantID: tenantA, MeterKey: domain.MeterAPICalls,
		Quantity: 1, EventID: uuid.New(), Cloud: "aws"}
	n, err := h.st.InsertRaw(ctx, []domain.MeterRecord{rec})
	require.NoError(t, err)
	require.Equal(t, 1, n)

	// Session pinned to tenant B sees zero of tenant A's rows.
	require.Equal(t, 0, h.queryInt(t, tenantB, `SELECT COUNT(*) FROM usage_raw WHERE meter_key=$1`, domain.MeterAPICalls))
	// Session pinned to tenant A sees its own row.
	require.Equal(t, 1, h.queryInt(t, tenantA, `SELECT COUNT(*) FROM usage_raw WHERE meter_key=$1`, domain.MeterAPICalls))
}

// TestAC10_CrossTenantBudget404 proves an id-addressed cross-tenant read returns
// 404 (never 403) and emits a real security.cross_tenant_denied event
// (MASTER-FR-003, AC-10). REAL: Postgres RLS, Kafka.
func TestAC10_CrossTenantBudget404(t *testing.T) {
	h := requireHarness(t)
	ctx := context.Background()
	tenantA := uuid.New()
	tenantB := uuid.New()

	b, err := h.st.CreateBudget(ctx, domain.Op{Tenant: tenantA, Actor: domain.Actor{Type: "user", ID: "a"}},
		domain.Budget{MeterKey: domain.MeterAPICalls, Window: domain.WindowCalendarMonth, LimitValue: 100, ActionAt100: domain.ActionAlertOnly})
	require.NoError(t, err)

	tokB := h.token(t, tenantB, "user", "u-b", nil)
	r := h.do(t, "GET", "/api/v1/budgets/"+b.ID.String(), tokB, nil, nil)
	require.Equal(t, 404, r.status)
	require.Equal(t, "NOT_FOUND", errCode(r))

	evs := h.consumeUsageEvents(t, tenantB, events.EvCrossTenantDenied, 1, 15*time.Second)
	require.GreaterOrEqual(t, len(evs), 1, "cross-tenant denial audited on real Kafka")
}

// TestAC_OPAAuthzRealSidecar proves authorization goes through the REAL OPA
// sidecar over the REAL Redis permissions_flat projection (MASTER-FR-012): a
// granted tenant-scoped action allows, an ungranted one denies.
func TestAC_OPAAuthzRealSidecar(t *testing.T) {
	h := requireHarness(t)
	ctx := context.Background()
	tenant := uuid.New().String()
	user := "u-" + uuid.NewString()[:8]

	// Seed the projection rbac would publish (RBC-FR-040 key scheme). The
	// catalog key is a shared global on the dev-stack Redis — merge, never SET.
	rc := h.redis.R
	require.NoError(t, opaclient.SeedCatalogActions(ctx, h.redis, map[string]bool{
		"usage.budget.read": false, "usage.budget.create": false}))
	require.NoError(t, rc.Set(ctx, "perm:"+tenant+":"+user+":actions",
		`{"actions":["usage.budget.read"]}`, time.Hour).Err())

	az := authz.NewOPAClient(opaURL, redisAddr)
	in := func(action string) authz.Input {
		return authz.Input{Subject: authz.Subject{ID: user, Typ: "user"}, Action: action, Tenant: tenant}
	}
	require.True(t, az.Allow(ctx, in("usage.budget.read")), "granted action allowed by real OPA")
	require.False(t, az.Allow(ctx, in("usage.budget.create")), "ungranted action denied by real OPA")

	// Sanity: the shared loader reads the same projection this decision used.
	loader := opaclient.NewLoader(redisx.New(redisAddr))
	p, err := loader.Load(ctx, &opaclient.Input{Subject: opaclient.Subject{ID: user, Typ: "user"}, Action: "usage.budget.read", Tenant: tenant})
	require.NoError(t, err)
	require.True(t, p.TenantActions.Found)
}

func (h *harness) queryStr(t *testing.T, sql string, args ...any) string {
	t.Helper()
	ctx := context.Background()
	conn, err := h.appPool.Acquire(ctx)
	require.NoError(t, err)
	defer conn.Release()
	var s string
	require.NoError(t, conn.QueryRow(ctx, sql, args...).Scan(&s))
	return s
}

func (h *harness) queryBool(t *testing.T, sql string, args ...any) bool {
	t.Helper()
	ctx := context.Background()
	conn, err := h.appPool.Acquire(ctx)
	require.NoError(t, err)
	defer conn.Release()
	var b bool
	require.NoError(t, conn.QueryRow(ctx, sql, args...).Scan(&b))
	return b
}
