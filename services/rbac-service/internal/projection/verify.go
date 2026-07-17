package projection

import (
	"context"
	"fmt"

	"github.com/google/uuid"
)

// VerifyResult reports projection-vs-SQL drift for one tenant (RBC-FR-043:
// weekly verification; drift > 0 alerts; AC-12).
type VerifyResult struct {
	TenantID      string   `json:"tenant_id"`
	UsersChecked  int      `json:"users_checked"`
	DriftedUsers  []string `json:"drifted_users"`
	RepairedUsers []string `json:"repaired_users,omitempty"`
}

// Drift returns the drifted-user count (the alerting SLI).
func (r VerifyResult) Drift() int { return len(r.DriftedUsers) }

// Verify compares each sampled user's Redis projection against a freshly
// computed SQL snapshot. With repair=true, drifted users are rewritten
// immediately (detect + repair, AC-12).
func Verify(ctx context.Context, loader SnapshotLoader, reader Reader, writer *RedisWriter, tenant uuid.UUID, users []string, repair bool) (VerifyResult, error) {
	res := VerifyResult{TenantID: tenant.String(), UsersChecked: len(users), DriftedUsers: []string{}}
	for _, user := range users {
		snap, err := loader.LoadSnapshot(ctx, tenant, user)
		if err != nil {
			return res, fmt.Errorf("verify %s: %w", user, err)
		}
		want := Flatten(snap)
		same, err := matches(ctx, reader, want)
		if err != nil {
			return res, err
		}
		if !same {
			res.DriftedUsers = append(res.DriftedUsers, user)
			if repair && writer != nil {
				if err := writer.WriteUser(ctx, want); err != nil {
					return res, fmt.Errorf("repair %s: %w", user, err)
				}
				res.RepairedUsers = append(res.RepairedUsers, user)
			}
		}
	}
	return res, nil
}

func matches(ctx context.Context, reader Reader, want Flat) (bool, error) {
	tenant, user := want.TenantID.String(), want.UserID

	actions, found, err := reader.TenantActions(ctx, tenant, user)
	if err != nil {
		return false, err
	}
	if !found || !sameSet(actions, want.TenantActions) {
		return false, nil
	}
	flags, found, err := reader.UserFlags(ctx, tenant, user)
	if err != nil {
		return false, err
	}
	if !found || flags.Admin != want.Flags.Admin || len(flags.WsAdmin) != len(want.Flags.WsAdmin) {
		return false, nil
	}
	for wsID, entry := range want.WorkspaceActions {
		got, ok, err := reader.Workspace(ctx, tenant, user, wsID.String())
		if err != nil {
			return false, err
		}
		if !ok || got.Archived != entry.Archived || !sameSet(got.Actions, entry.Actions) {
			return false, nil
		}
	}
	for h, entry := range want.Resources {
		got, ok, err := reader.Resource(ctx, tenant, user, h)
		if err != nil {
			return false, err
		}
		if !ok || got.Level != entry.Level || got.Archived != entry.Archived {
			return false, nil
		}
	}
	return true, nil
}

func sameSet(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	m := make(map[string]bool, len(a))
	for _, s := range a {
		m[s] = true
	}
	for _, s := range b {
		if !m[s] {
			return false
		}
	}
	return true
}
