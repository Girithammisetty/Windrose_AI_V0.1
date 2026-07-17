package keycloak

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

// fakeKC simulates the Keycloak 26 admin REST surface the adapter touches:
// master-realm password grant, realm CRUD, user CRUD, logout.
type fakeKC struct {
	mu         sync.Mutex
	t          *testing.T
	tokenCalls int
	realms     map[string]bool
	users      map[string]map[string]map[string]any // realm -> username -> representation
	lastPut    map[string]any
}

func newFakeKC(t *testing.T) (*fakeKC, *httptest.Server) {
	f := &fakeKC{t: t, realms: map[string]bool{}, users: map[string]map[string]map[string]any{}}
	ts := httptest.NewServer(http.HandlerFunc(f.serve))
	t.Cleanup(ts.Close)
	return f, ts
}

func (f *fakeKC) serve(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()

	// Password grant (master realm, admin-cli).
	if r.URL.Path == "/realms/master/protocol/openid-connect/token" {
		_ = r.ParseForm()
		if r.Form.Get("grant_type") != "password" || r.Form.Get("client_id") != "admin-cli" ||
			r.Form.Get("username") != "admin" || r.Form.Get("password") != "admin" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		f.tokenCalls++
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"access_token": "tok-1", "expires_in": 60})
		return
	}

	// Everything below needs the admin bearer token.
	if r.Header.Get("Authorization") != "Bearer tok-1" {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}

	switch {
	case r.Method == http.MethodPost && r.URL.Path == "/admin/realms":
		var body struct {
			Realm string `json:"realm"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		if f.realms[body.Realm] {
			w.WriteHeader(http.StatusConflict)
			return
		}
		f.realms[body.Realm] = true
		f.users[body.Realm] = map[string]map[string]any{}
		w.WriteHeader(http.StatusCreated)

	case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/users"):
		realm := strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/admin/realms/"), "/users")
		if !f.realms[realm] {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		var rep map[string]any
		_ = json.NewDecoder(r.Body).Decode(&rep)
		username := rep["username"].(string)
		if _, dup := f.users[realm][username]; dup {
			w.WriteHeader(http.StatusConflict)
			return
		}
		rep["id"] = "kcid-" + username
		f.users[realm][username] = rep
		w.WriteHeader(http.StatusCreated)

	case r.Method == http.MethodGet && strings.Contains(r.URL.Path, "/users") && r.URL.Query().Get("username") != "":
		realm := strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/admin/realms/"), "/users")
		out := []map[string]any{}
		if u, ok := f.users[realm][r.URL.Query().Get("username")]; ok {
			out = append(out, u)
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(out)

	case r.Method == http.MethodGet && strings.Contains(r.URL.Path, "/users/"):
		parts := strings.Split(r.URL.Path, "/")
		id := parts[len(parts)-1]
		for _, us := range f.users {
			for _, rep := range us {
				if rep["id"] == id {
					w.Header().Set("Content-Type", "application/json")
					_ = json.NewEncoder(w).Encode(rep)
					return
				}
			}
		}
		w.WriteHeader(http.StatusNotFound)

	case r.Method == http.MethodPut && strings.Contains(r.URL.Path, "/users/"):
		var rep map[string]any
		_ = json.NewDecoder(r.Body).Decode(&rep)
		f.lastPut = rep
		w.WriteHeader(http.StatusNoContent)

	case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/logout"):
		w.WriteHeader(http.StatusNoContent)

	default:
		f.t.Errorf("unexpected keycloak call: %s %s", r.Method, r.URL.Path)
		w.WriteHeader(http.StatusTeapot)
	}
}

func newAdmin(ts *httptest.Server) *HTTPAdmin {
	return &HTTPAdmin{BaseURL: ts.URL, Token: PasswordGrant(ts.URL, "admin", "admin", nil)}
}

// PasswordGrant fetches once and reuses the cached token across calls.
func TestPasswordGrantCachesToken(t *testing.T) {
	fk, ts := newFakeKC(t)
	a := newAdmin(ts)
	ctx := context.Background()
	if err := a.CreateRealm(ctx, "acme"); err != nil {
		t.Fatal(err)
	}
	if err := a.CreateRealm(ctx, "globex"); err != nil {
		t.Fatal(err)
	}
	if fk.tokenCalls != 1 {
		t.Fatalf("want 1 password grant (cached), got %d", fk.tokenCalls)
	}
}

// CreateRealm is idempotent: a 409 (already exists) is success, so
// provisioning retries never fail on the realm step.
func TestCreateRealmIdempotent(t *testing.T) {
	_, ts := newFakeKC(t)
	a := newAdmin(ts)
	ctx := context.Background()
	if err := a.CreateRealm(ctx, "acme"); err != nil {
		t.Fatal(err)
	}
	if err := a.CreateRealm(ctx, "acme"); err != nil {
		t.Fatalf("second CreateRealm (409): want nil, got %v", err)
	}
}

// CreateUser against a MISSING realm bootstraps the realm then retries
// (create-if-missing for tenants that predate the real adapter).
func TestCreateUserBootstrapsMissingRealm(t *testing.T) {
	fk, ts := newFakeKC(t)
	a := newAdmin(ts)
	ctx := context.Background()

	id, err := a.CreateUser(ctx, "virginrealm", "o@x.com", "Owner")
	if err != nil {
		t.Fatalf("CreateUser on virgin realm: %v", err)
	}
	if id != "kcid-o@x.com" {
		t.Fatalf("wrong idp subject: %s", id)
	}
	if !fk.realms["virginrealm"] {
		t.Fatal("realm was not bootstrapped")
	}
	// Duplicate create resolves to the existing id (409 path).
	id2, err := a.CreateUser(ctx, "virginrealm", "o@x.com", "Owner")
	if err != nil || id2 != id {
		t.Fatalf("duplicate CreateUser: id=%s err=%v", id2, err)
	}
}

// DisableUser is read-modify-write: the PUT carries the full representation
// with enabled=false, not a partial body that would wipe other fields.
func TestDisableUserPreservesRepresentation(t *testing.T) {
	fk, ts := newFakeKC(t)
	a := newAdmin(ts)
	ctx := context.Background()
	if err := a.CreateRealm(ctx, "acme"); err != nil {
		t.Fatal(err)
	}
	id, err := a.CreateUser(ctx, "acme", "u@acme.com", "U")
	if err != nil {
		t.Fatal(err)
	}
	if err := a.DisableUser(ctx, "acme", id); err != nil {
		t.Fatal(err)
	}
	if fk.lastPut["enabled"] != false {
		t.Fatalf("PUT enabled: %v", fk.lastPut["enabled"])
	}
	if fk.lastPut["username"] != "u@acme.com" || fk.lastPut["email"] != "u@acme.com" {
		t.Fatalf("PUT dropped fields: %v", fk.lastPut)
	}
	if err := a.RevokeSessions(ctx, "acme", id); err != nil {
		t.Fatal(err)
	}
}
