package integration

import (
	"context"
	"net/http"
	"testing"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	gcevent "github.com/datacern-ai/go-common/event"

	"github.com/datacern-ai/case-service/internal/events"
)

func aliasExists(t *testing.T, alias string) bool {
	t.Helper()
	resp, err := http.Get("http://localhost:9200/_alias/" + alias)
	require.NoError(t, err)
	defer resp.Body.Close()
	return resp.StatusCode == http.StatusOK
}

// TestTenantProvisionedCreatesEmptyCaseIndex proves the fix for the doctor.sh
// "case index MISSING ... Cases page will 503" gap: a brand-new tenant with
// ZERO cases must get a queryable cases-<tenant> index as soon as
// identity-service emits tenant.provisioned, not only after its first case is
// written or an operator runs a manual/doctor-heal reindex.
func TestTenantProvisionedCreatesEmptyCaseIndex(t *testing.T) {
	h := requireHarness(t)
	ctx := context.Background()
	a := h.newActor(t)
	alias := "cases-" + a.tenant.String()

	require.False(t, aliasExists(t, alias), "fixture precondition: brand-new tenant must not already have an index")

	env := gcevent.New("tenant.provisioned", a.tenant, gcevent.Actor{Type: "service", ID: "identity-service"},
		"urn:tenant:"+a.tenant.String(), "", nil)
	require.NoError(t, events.TenantHandler(h.server.Projector)(ctx, env))

	assert.True(t, aliasExists(t, alias), "tenant.provisioned must create the (empty) case index up front")
	assert.Equal(t, 0, openSearchCount(t, alias), "a freshly provisioned tenant has zero cases")

	// Idempotent: replaying the event (at-least-once delivery) must not error.
	require.NoError(t, events.TenantHandler(h.server.Projector)(ctx, env))
}

// TestTenantHandlerIgnoresOtherEventTypes proves the handler only acts on
// tenant.provisioned and is a safe no-op for every other identity.events.v1
// event it will see on the same shared "case-inbound" consumer group.
func TestTenantHandlerIgnoresOtherEventTypes(t *testing.T) {
	h := requireHarness(t)
	ctx := context.Background()
	tenant := uuid.New()
	alias := "cases-" + tenant.String()

	env := gcevent.New("user.invited", tenant, gcevent.Actor{Type: "service", ID: "identity-service"},
		"urn:user:"+tenant.String(), "", nil)
	require.NoError(t, events.TenantHandler(h.server.Projector)(ctx, env))

	assert.False(t, aliasExists(t, alias), "a non-tenant.provisioned event must not create an index")
}
