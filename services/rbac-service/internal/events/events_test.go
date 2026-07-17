package events

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

type fakeOutbox struct {
	rows   []OutboxEntry
	marked []int64
}

func (f *fakeOutbox) FetchUnpublishedEnvelopes(_ context.Context, limit int) ([]OutboxEntry, error) {
	var out []OutboxEntry
	for _, r := range f.rows {
		if !contains(f.marked, r.ID) {
			out = append(out, r)
			if len(out) == limit {
				break
			}
		}
	}
	return out, nil
}

func (f *fakeOutbox) MarkEnvelopesPublished(_ context.Context, ids []int64) error {
	f.marked = append(f.marked, ids...)
	return nil
}

func contains(list []int64, v int64) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}

func envOf(t *testing.T, typ string) Envelope {
	t.Helper()
	return NewEnvelope(typ, uuid.New(), Actor{Type: "user", ID: "u-1"}, "wr:t:rbac:group/g-1", "trace-1", map[string]any{"k": "v"})
}

func TestOutboxRelay_PublishesInOrderAndMarks(t *testing.T) {
	src := &fakeOutbox{rows: []OutboxEntry{
		{ID: 1, Envelope: envOf(t, "group.created")},
		{ID: 2, Envelope: envOf(t, "member.added")},
	}}
	pub := NewInMemoryPublisher()
	relay := NewOutboxRelay(src, pub)

	n, err := relay.ProcessOnce(context.Background())
	require.NoError(t, err)
	assert.Equal(t, 2, n)
	require.Len(t, pub.Events(), 2)
	assert.Equal(t, "group.created", pub.Events()[0].Envelope.EventType)
	assert.Equal(t, "member.added", pub.Events()[1].Envelope.EventType)
	assert.Equal(t, Topic, pub.Events()[0].Topic)
	assert.Equal(t, []int64{1, 2}, src.marked)

	// Nothing left.
	n, err = relay.ProcessOnce(context.Background())
	require.NoError(t, err)
	assert.Zero(t, n)
}

// A publish failure stops the pass (ordering preserved) and retries later —
// at-least-once with idempotent producers (MASTER-FR-032/034).
func TestOutboxRelay_RetryPreservesOrder(t *testing.T) {
	src := &fakeOutbox{rows: []OutboxEntry{
		{ID: 1, Envelope: envOf(t, "group.created")},
		{ID: 2, Envelope: envOf(t, "member.added")},
	}}
	pub := NewInMemoryPublisher()
	pub.FailNext = errors.New("broker down")
	relay := NewOutboxRelay(src, pub)

	n, err := relay.ProcessOnce(context.Background())
	require.NoError(t, err)
	assert.Zero(t, n, "first row failed; nothing marked")
	assert.Empty(t, src.marked)

	n, err = relay.ProcessOnce(context.Background())
	require.NoError(t, err)
	assert.Equal(t, 2, n)
	assert.Equal(t, []int64{1, 2}, src.marked)
	assert.Equal(t, "group.created", pub.Events()[0].Envelope.EventType, "row 1 still first")
}

func TestEnvelope_UUIDv7AndDefaults(t *testing.T) {
	env := envOf(t, "workspace.created")
	assert.Equal(t, uuid.Version(7), env.EventID.Version(), "uuidv7 event ids (MASTER-FR-021/031)")
	assert.NotZero(t, env.OccurredAt)
	assert.Equal(t, "trace-1", env.TraceID)
	assert.NotNil(t, env.Payload)
}

// ---- inbound handler ---------------------------------------------------------

type fakeConsumerStore struct {
	seeded         []uuid.UUID
	grants         []string
	dirtied        []string
	dropped        []string
	ownerGrants    []string
	groupAssigns   []string
	failGrant      error
	failOwnerGrant error
}

func (f *fakeConsumerStore) SeedTenantFromEvent(_ context.Context, tenant uuid.UUID, _, _ string) error {
	f.seeded = append(f.seeded, tenant)
	return nil
}

func (f *fakeConsumerStore) CreateImplicitOwnerGrantFromEvent(_ context.Context, _ uuid.UUID, ws uuid.UUID, urn, creator, _ string) error {
	if f.failGrant != nil {
		return f.failGrant
	}
	f.grants = append(f.grants, urn+"|"+creator+"|"+ws.String())
	return nil
}

func (f *fakeConsumerStore) MarkDirty(_ context.Context, _ uuid.UUID, users []string, _ string) error {
	f.dirtied = append(f.dirtied, users...)
	return nil
}

func (f *fakeConsumerStore) RemoveUserProjection(_ context.Context, _ uuid.UUID, user string) error {
	f.dropped = append(f.dropped, user)
	return nil
}

func (f *fakeConsumerStore) GrantOwnerAdminFromEvent(_ context.Context, _ uuid.UUID, userID, actorID, _ string) error {
	if f.failOwnerGrant != nil {
		return f.failOwnerGrant
	}
	f.ownerGrants = append(f.ownerGrants, userID+"|"+actorID)
	return nil
}

func (f *fakeConsumerStore) AssignUserToGroupsFromEvent(_ context.Context, _ uuid.UUID, userID string, groups []string, _, _ string) error {
	f.groupAssigns = append(f.groupAssigns, userID+"|"+strings.Join(groups, ","))
	return nil
}

func TestHandler_TenantProvisionedSeeds(t *testing.T) {
	fs := &fakeConsumerStore{}
	h := &Handler{Store: fs}
	tenant := uuid.New()
	env := NewEnvelope("tenant.provisioned", tenant, Actor{Type: "service", ID: "identity"}, "", "", nil)
	require.NoError(t, h.HandleEvent(context.Background(), env))
	assert.Equal(t, []uuid.UUID{tenant}, fs.seeded)
}

// RBC-FR-032 / AC-13: *.created with a workspace -> implicit owner grant for
// the creating user.
func TestHandler_ResourceCreatedImplicitGrant(t *testing.T) {
	fs := &fakeConsumerStore{}
	h := &Handler{Store: fs}
	tenant := uuid.New()
	ws := uuid.New()
	env := NewEnvelope("dataset.created", tenant, Actor{Type: "user", ID: "u-9"},
		"wr:t:dataset:dataset/ds-1", "", map[string]any{"workspace_id": ws.String()})
	require.NoError(t, h.HandleEvent(context.Background(), env))
	require.Len(t, fs.grants, 1)
	assert.Equal(t, "wr:t:dataset:dataset/ds-1|u-9|"+ws.String(), fs.grants[0])

	// Tenant-scoped resources (no workspace) are skipped.
	env2 := NewEnvelope("connection.created", tenant, Actor{Type: "user", ID: "u-9"},
		"wr:t:ingestion:connection/c-1", "", nil)
	require.NoError(t, h.HandleEvent(context.Background(), env2))
	assert.Len(t, fs.grants, 1)
}

// BR-7: the tenant-provisioning owner bootstrap. identity-service's
// SeedDefaults step emits user.invited with is_owner=true + user_id for the
// tenant owner ONLY — a regular admin-driven POST /users invite (same event
// type) carries neither, and must NOT auto-grant Admin.
func TestHandler_OwnerInvitedGrantsAdmin(t *testing.T) {
	fs := &fakeConsumerStore{}
	h := &Handler{Store: fs}
	tenant := uuid.New()

	owner := NewEnvelope("user.invited", tenant, Actor{Type: "service", ID: "identity-service"},
		"wr:t:identity:user/u-owner", "", map[string]any{
			"email": "owner@acme.com", "is_owner": true, "user_id": "u-owner",
		})
	require.NoError(t, h.HandleEvent(context.Background(), owner))
	assert.Equal(t, []string{"u-owner|identity-service"}, fs.ownerGrants)
}

func TestHandler_RegularInviteDoesNotGrantAdmin(t *testing.T) {
	fs := &fakeConsumerStore{}
	h := &Handler{Store: fs}
	tenant := uuid.New()

	regular := NewEnvelope("user.invited", tenant, Actor{Type: "user", ID: "u-admin"},
		"wr:t:identity:user/u-newbie", "", map[string]any{
			"email": "newbie@acme.com", "user_id": "u-newbie",
		})
	require.NoError(t, h.HandleEvent(context.Background(), regular))
	assert.Empty(t, fs.ownerGrants)
	assert.Empty(t, fs.groupAssigns) // no groups on the invite -> no assignment
}

// IDN-FR-021: a regular invite carrying initial groups places the user into
// those permission groups (so they arrive with a role), without any owner grant.
func TestHandler_RegularInviteWithGroupsAssigns(t *testing.T) {
	fs := &fakeConsumerStore{}
	h := &Handler{Store: fs}
	tenant := uuid.New()

	env := NewEnvelope("user.invited", tenant, Actor{Type: "user", ID: "u-admin"},
		"wr:t:identity:user/u-newbie", "", map[string]any{
			"email": "newbie@acme.com", "user_id": "u-newbie",
			"groups": []any{"Reviewer", "Analyst"},
		})
	require.NoError(t, h.HandleEvent(context.Background(), env))
	assert.Empty(t, fs.ownerGrants) // never auto-Admin
	assert.Equal(t, []string{"u-newbie|Reviewer,Analyst"}, fs.groupAssigns)
}

func TestHandler_OwnerInvitedMissingUserIDIsSkippedNotPanicked(t *testing.T) {
	fs := &fakeConsumerStore{}
	h := &Handler{Store: fs} // Log intentionally nil — must not panic
	tenant := uuid.New()

	malformed := NewEnvelope("user.invited", tenant, Actor{Type: "service", ID: "identity-service"},
		"", "", map[string]any{"is_owner": true})
	require.NoError(t, h.HandleEvent(context.Background(), malformed))
	assert.Empty(t, fs.ownerGrants)
}

func TestHandler_UserLifecycle(t *testing.T) {
	fs := &fakeConsumerStore{}
	h := &Handler{Store: fs}
	tenant := uuid.New()

	warm := NewEnvelope("user.created", tenant, Actor{Type: "service", ID: "identity"}, "", "",
		map[string]any{"user_id": "u-new"})
	require.NoError(t, h.HandleEvent(context.Background(), warm))
	assert.Equal(t, []string{"u-new"}, fs.dirtied)

	drop := NewEnvelope("user.deactivated", tenant, Actor{Type: "service", ID: "identity"}, "", "",
		map[string]any{"user_id": "u-old"})
	require.NoError(t, h.HandleEvent(context.Background(), drop))
	assert.Equal(t, []string{"u-old"}, fs.dropped)
}
