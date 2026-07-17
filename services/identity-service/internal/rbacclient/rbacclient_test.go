package rbacclient

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/domain"
	"github.com/windrose-ai/identity-service/internal/keys"
	"github.com/windrose-ai/identity-service/internal/store/memory"
)

// fakeRBAC is an httptest rbac-service exposing only POST /api/v1/authz/check.
// admins is the set of subject ids that carry the tenant admin flag.
type fakeRBAC struct {
	t       *testing.T
	admins  map[string]bool
	verify  func(token string) (*domain.Claims, error)
	checked []string // subject ids probed, in order
	fail    bool     // respond 500
}

func (f *fakeRBAC) handler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/api/v1/authz/check" {
			f.t.Errorf("unexpected call %s %s", r.Method, r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
			return
		}
		// The bearer token must be a verifiable service-typed platform JWT.
		raw := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		claims, err := f.verify(raw)
		if err != nil {
			f.t.Errorf("service token failed verification: %v", err)
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		if claims.Typ != domain.TypService || claims.Subject != "svc:identity-service" {
			f.t.Errorf("want typ=service sub=svc:identity-service, got typ=%s sub=%s", claims.Typ, claims.Subject)
		}
		if f.fail {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		var req struct {
			Subject struct {
				ID  string `json:"id"`
				Typ string `json:"typ"`
			} `json:"subject"`
			Action string `json:"action"`
			Tenant string `json:"tenant"`
		}
		_ = json.NewDecoder(r.Body).Decode(&req)
		if req.Tenant != claims.TenantID.String() {
			f.t.Errorf("check tenant %s != token tenant %s (rbac binds them)", req.Tenant, claims.TenantID)
		}
		if req.Subject.Typ != domain.TypUser || req.Action == "" {
			f.t.Errorf("bad check request: %+v", req)
		}
		f.checked = append(f.checked, req.Subject.ID)
		resp := map[string]any{"allowed": false, "reason": "deny_default"}
		if f.admins[req.Subject.ID] {
			resp = map[string]any{"allowed": true, "reason": "admin_bypass"}
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(resp)
	}
}

type fix struct {
	store    *memory.Store
	checker  *Checker
	rbac     *fakeRBAC
	tenantID uuid.UUID
}

func newFix(t *testing.T) *fix {
	t.Helper()
	st := memory.New()
	km := keys.NewKeyManager(st, keys.NewLocalSigner(), time.Now)
	if err := km.Bootstrap(context.Background()); err != nil {
		t.Fatal(err)
	}
	issuer := keys.NewIssuer(km, time.Now)
	rb := &fakeRBAC{t: t, admins: map[string]bool{}, verify: issuer.Verify}
	ts := httptest.NewServer(rb.handler())
	t.Cleanup(ts.Close)
	tenantID, _ := uuid.NewV7()
	return &fix{
		store:    st,
		rbac:     rb,
		tenantID: tenantID,
		checker:  &Checker{BaseURL: ts.URL, Store: st, Issuer: issuer},
	}
}

func (f *fix) addUser(t *testing.T, status domain.UserStatus, deleted bool) *domain.User {
	t.Helper()
	id, _ := uuid.NewV7()
	now := time.Now().UTC()
	u := &domain.User{
		ID: id, TenantID: f.tenantID, Email: id.String() + "@t.com",
		Status: status, CreatedAt: now, UpdatedAt: now,
	}
	if deleted {
		u.DeletedAt = &now
	}
	if err := f.store.CreateUser(context.Background(), u); err != nil {
		t.Fatal(err)
	}
	return u
}

func TestIsLastAdmin_SoleAdmin(t *testing.T) {
	f := newFix(t)
	admin := f.addUser(t, domain.UserActive, false)
	f.addUser(t, domain.UserActive, false) // active non-admin
	f.rbac.admins[admin.ID.String()] = true

	last, err := f.checker.IsLastAdmin(context.Background(), f.tenantID, admin.ID)
	if err != nil {
		t.Fatal(err)
	}
	if !last {
		t.Fatal("sole admin: want IsLastAdmin=true")
	}
}

func TestIsLastAdmin_AnotherActiveAdminExists(t *testing.T) {
	f := newFix(t)
	admin := f.addUser(t, domain.UserActive, false)
	other := f.addUser(t, domain.UserActive, false)
	f.rbac.admins[admin.ID.String()] = true
	f.rbac.admins[other.ID.String()] = true

	last, err := f.checker.IsLastAdmin(context.Background(), f.tenantID, admin.ID)
	if err != nil {
		t.Fatal(err)
	}
	if last {
		t.Fatal("another active admin exists: want IsLastAdmin=false")
	}
}

func TestIsLastAdmin_TargetNotAdmin(t *testing.T) {
	f := newFix(t)
	u := f.addUser(t, domain.UserActive, false)
	f.addUser(t, domain.UserActive, false)

	last, err := f.checker.IsLastAdmin(context.Background(), f.tenantID, u.ID)
	if err != nil {
		t.Fatal(err)
	}
	if last {
		t.Fatal("non-admin target: want IsLastAdmin=false")
	}
	if len(f.rbac.checked) != 1 || f.rbac.checked[0] != u.ID.String() {
		t.Fatalf("non-admin target must short-circuit after one probe, got %v", f.rbac.checked)
	}
}

// The other admin is deactivated (or soft-deleted): it must not count — the
// target is still the last ACTIVE admin (BR-9).
func TestIsLastAdmin_InactiveAdminsDoNotCount(t *testing.T) {
	f := newFix(t)
	admin := f.addUser(t, domain.UserActive, false)
	deact := f.addUser(t, domain.UserDeactivated, false)
	gone := f.addUser(t, domain.UserActive, true) // soft-deleted
	invited := f.addUser(t, domain.UserInvited, false)
	for _, u := range []*domain.User{admin, deact, gone, invited} {
		f.rbac.admins[u.ID.String()] = true
	}

	last, err := f.checker.IsLastAdmin(context.Background(), f.tenantID, admin.ID)
	if err != nil {
		t.Fatal(err)
	}
	if !last {
		t.Fatal("only inactive other admins: want IsLastAdmin=true")
	}
	for _, id := range f.rbac.checked[1:] {
		if id != admin.ID.String() {
			t.Fatalf("probed inactive user %s", id)
		}
	}
}

// rbac failure fails CLOSED: the caller gets an error, not a silent allow.
func TestIsLastAdmin_RBACErrorPropagates(t *testing.T) {
	f := newFix(t)
	u := f.addUser(t, domain.UserActive, false)
	f.rbac.fail = true

	if _, err := f.checker.IsLastAdmin(context.Background(), f.tenantID, u.ID); err == nil {
		t.Fatal("rbac 500: want error, got nil")
	}
}
