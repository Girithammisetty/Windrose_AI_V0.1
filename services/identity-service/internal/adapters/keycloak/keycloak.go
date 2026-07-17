// Package keycloak provides the KeycloakAdmin adapter (domain.KeycloakAdmin):
// a Fake used by every test tier, and HTTPAdmin, a real Keycloak Admin REST
// adapter that compiles and follows the documented API but is NOT yet tested
// against a live Keycloak (see README adapter inventory).
package keycloak

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
)

// ---------------------------------------------------------------------------
// Fake — deterministic in-memory Keycloak for tests.
// ---------------------------------------------------------------------------

type Fake struct {
	mu     sync.Mutex
	Realms map[string]bool
	// Users maps realm -> idpSubject -> enabled.
	Users map[string]map[string]bool
	// RevokedSessions counts RevokeSessions calls per idpSubject.
	RevokedSessions map[string]int
	// Fail injects errors per method name (e.g. Fail["CreateRealm"] = err).
	Fail map[string]error
	// Calls counts invocations per method (idempotency assertions, AC-3).
	Calls map[string]int
}

func NewFake() *Fake {
	return &Fake{
		Realms: map[string]bool{}, Users: map[string]map[string]bool{},
		RevokedSessions: map[string]int{}, Fail: map[string]error{}, Calls: map[string]int{},
	}
}

func (f *Fake) call(name string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Calls[name]++
	return f.Fail[name]
}

func (f *Fake) CreateRealm(_ context.Context, tenantName string) error {
	if err := f.call("CreateRealm"); err != nil {
		return err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Realms[tenantName] = true
	return nil
}

func (f *Fake) DeleteRealm(_ context.Context, tenantName string) error {
	if err := f.call("DeleteRealm"); err != nil {
		return err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	delete(f.Realms, tenantName)
	return nil
}

func (f *Fake) CreateUser(_ context.Context, realm, email, fullName string) (string, error) {
	if err := f.call("CreateUser"); err != nil {
		return "", err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	sub := "kc-" + uuid.NewString()
	if f.Users[realm] == nil {
		f.Users[realm] = map[string]bool{}
	}
	f.Users[realm][sub] = true
	return sub, nil
}

func (f *Fake) DisableUser(_ context.Context, realm, idpSubject string) error {
	if err := f.call("DisableUser"); err != nil {
		return err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.Users[realm] != nil {
		f.Users[realm][idpSubject] = false
	}
	return nil
}

func (f *Fake) RevokeSessions(_ context.Context, realm, idpSubject string) error {
	if err := f.call("RevokeSessions"); err != nil {
		return err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.RevokedSessions[idpSubject]++
	return nil
}

// ---------------------------------------------------------------------------
// HTTPAdmin — real Keycloak Admin REST adapter (Keycloak 24+/26 shapes).
//
// Boot expectations against a VIRGIN Keycloak (docker-compose.dev.yml:
// quay.io/keycloak/keycloak:26.0 start-dev, bootstrap admin admin/admin on
// :8180): nothing is required at identity boot time. Tenant realms are
// created lazily by the provisioning engine (CreateKeycloakRealm step) via
// CreateRealm; CreateUser additionally self-heals a missing realm
// (create-if-missing) so tenants that predate the real adapter (provisioned
// against the Fake) still work. Realm creation and user creation are
// idempotent: 409s are treated as already-exists.
//
// Admin credentials: use PasswordGrant (master-realm admin-cli password
// grant, cached and refreshed before expiry) as the Token source — Keycloak
// admin access tokens live ~60s, so a static token is only viable for tests.
// ---------------------------------------------------------------------------

type HTTPAdmin struct {
	BaseURL string // e.g. http://localhost:8180
	Token   func(ctx context.Context) (string, error)
	Client  *http.Client
}

// PasswordGrant returns a Token func that logs into the master realm with the
// bootstrap admin credentials (grant_type=password, client_id=admin-cli) and
// caches the access token until shortly before expiry (Keycloak admin tokens
// default to 60s). client may be nil (http.DefaultClient).
func PasswordGrant(baseURL, username, password string, client *http.Client) func(ctx context.Context) (string, error) {
	var (
		mu     sync.Mutex
		token  string
		expiry time.Time
	)
	return func(ctx context.Context) (string, error) {
		mu.Lock()
		defer mu.Unlock()
		if token != "" && time.Now().Before(expiry) {
			return token, nil
		}
		form := url.Values{
			"grant_type": {"password"}, "client_id": {"admin-cli"},
			"username": {username}, "password": {password},
		}
		req, err := http.NewRequestWithContext(ctx, http.MethodPost,
			baseURL+"/realms/master/protocol/openid-connect/token",
			strings.NewReader(form.Encode()))
		if err != nil {
			return "", err
		}
		req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		cl := client
		if cl == nil {
			cl = http.DefaultClient
		}
		resp, err := cl.Do(req)
		if err != nil {
			return "", fmt.Errorf("keycloak admin token: %w", err)
		}
		defer resp.Body.Close()
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
		if resp.StatusCode != http.StatusOK {
			return "", fmt.Errorf("keycloak admin token: status %d: %s", resp.StatusCode, string(raw))
		}
		var body struct {
			AccessToken string `json:"access_token"`
			ExpiresIn   int    `json:"expires_in"`
		}
		if err := json.Unmarshal(raw, &body); err != nil {
			return "", fmt.Errorf("keycloak admin token: decode: %w", err)
		}
		if body.AccessToken == "" {
			return "", fmt.Errorf("keycloak admin token: empty access_token")
		}
		token = body.AccessToken
		// Refresh 10s early; floor at 5s so a tiny expires_in still caches.
		ttl := time.Duration(body.ExpiresIn)*time.Second - 10*time.Second
		if ttl < 5*time.Second {
			ttl = 5 * time.Second
		}
		expiry = time.Now().Add(ttl)
		return token, nil
	}
}

// statusError carries the HTTP status so callers can branch on 404/409.
type statusError struct {
	status int
	msg    string
}

func (e *statusError) Error() string { return e.msg }

func (a *HTTPAdmin) do(ctx context.Context, method, path string, body any, out any) error {
	var rd *bytes.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return err
		}
		rd = bytes.NewReader(b)
	} else {
		rd = bytes.NewReader(nil)
	}
	req, err := http.NewRequestWithContext(ctx, method, a.BaseURL+path, rd)
	if err != nil {
		return err
	}
	tok, err := a.Token(ctx)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+tok)
	req.Header.Set("Content-Type", "application/json")
	cl := a.Client
	if cl == nil {
		cl = http.DefaultClient
	}
	resp, err := cl.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return &statusError{status: resp.StatusCode,
			msg: fmt.Sprintf("keycloak %s %s: status %d: %s", method, path, resp.StatusCode, string(raw))}
	}
	if out != nil {
		return json.NewDecoder(resp.Body).Decode(out)
	}
	return nil
}

func httpStatus(err error) int {
	var se *statusError
	if errors.As(err, &se) {
		return se.status
	}
	return 0
}

// CreateRealm creates the tenant realm. Idempotent: an already-existing realm
// (409) is success, so provisioning retries and re-runs never fail here.
func (a *HTTPAdmin) CreateRealm(ctx context.Context, tenantName string) error {
	err := a.do(ctx, http.MethodPost, "/admin/realms", map[string]any{
		"realm": tenantName, "enabled": true,
	}, nil)
	if err != nil && httpStatus(err) == http.StatusConflict {
		return nil // realm already exists — idempotent create
	}
	return err
}

func (a *HTTPAdmin) DeleteRealm(ctx context.Context, tenantName string) error {
	err := a.do(ctx, http.MethodDelete, "/admin/realms/"+tenantName, nil, nil)
	if err != nil && httpStatus(err) == http.StatusNotFound {
		return nil // already gone — deprovisioning is idempotent
	}
	return err
}

func (a *HTTPAdmin) CreateUser(ctx context.Context, realm, email, fullName string) (string, error) {
	create := func() error {
		return a.do(ctx, http.MethodPost, "/admin/realms/"+realm+"/users", map[string]any{
			"username": email, "email": email, "enabled": true,
			"firstName": fullName, "requiredActions": []string{"UPDATE_PASSWORD"},
		}, nil)
	}
	err := create()
	switch httpStatus(err) {
	case 0:
		if err != nil {
			return "", err
		}
	case http.StatusConflict:
		// User already exists in the realm — fall through to the id lookup.
	case http.StatusNotFound:
		// Realm missing (tenant provisioned before the real adapter was
		// enabled): bootstrap it, then retry once (create-if-missing).
		if rerr := a.CreateRealm(ctx, realm); rerr != nil {
			return "", fmt.Errorf("keycloak realm %s missing and bootstrap failed: %w", realm, rerr)
		}
		if err = create(); err != nil && httpStatus(err) != http.StatusConflict {
			return "", err
		}
	default:
		return "", err
	}
	// Keycloak returns the id via Location header; the simple fallback is a
	// lookup by username.
	var users []struct {
		ID string `json:"id"`
	}
	if err := a.do(ctx, http.MethodGet, "/admin/realms/"+realm+"/users?exact=true&username="+url.QueryEscape(email), nil, &users); err != nil {
		return "", err
	}
	if len(users) == 0 {
		return "", fmt.Errorf("keycloak user %s not found after create", email)
	}
	return users[0].ID, nil
}

// DisableUser flips enabled=false. Keycloak's PUT replaces the whole user
// representation, so read-modify-write to avoid clearing other fields.
func (a *HTTPAdmin) DisableUser(ctx context.Context, realm, idpSubject string) error {
	var rep map[string]any
	if err := a.do(ctx, http.MethodGet, "/admin/realms/"+realm+"/users/"+idpSubject, nil, &rep); err != nil {
		return err
	}
	rep["enabled"] = false
	return a.do(ctx, http.MethodPut, "/admin/realms/"+realm+"/users/"+idpSubject, rep, nil)
}

func (a *HTTPAdmin) RevokeSessions(ctx context.Context, realm, idpSubject string) error {
	return a.do(ctx, http.MethodPost, "/admin/realms/"+realm+"/users/"+idpSubject+"/logout", nil, nil)
}
