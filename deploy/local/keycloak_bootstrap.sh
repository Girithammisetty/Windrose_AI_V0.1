#!/usr/bin/env bash
# Bootstrap the running local Keycloak (:8180) as a REAL OIDC IdP for Windrose
# (BYO-P4 real OIDC login). Idempotent: creates the `windrose` realm, a public
# `windrose-web` client (authorization code + PKCE S256, redirect to ui-web's
# callback), and a test user whose email matches a seeded Windrose user so the
# /token/oidc login resolves it. Keycloak is a standards-compliant OIDC provider
# — it's the local stand-in for a customer's Okta/Auth0/Entra tenant.
#
# Usage: deploy/local/keycloak_bootstrap.sh [user-email] [user-password]
set -uo pipefail

KC="${KEYCLOAK_URL:-http://localhost:8180}"
ADMIN_USER="${KEYCLOAK_ADMIN_USER:-admin}"
ADMIN_PASS="${KEYCLOAK_ADMIN_PASSWORD:-admin}"
REALM="${OIDC_REALM:-windrose}"
CLIENT_ID="${OIDC_CLIENT_ID:-windrose-web}"
REDIRECT="${OIDC_REDIRECT:-http://localhost:3000/api/auth/callback}"
WEB_ORIGIN="${OIDC_WEB_ORIGIN:-http://localhost:3000}"
USER_EMAIL="${1:-datascientist@demo.windrose}"
USER_PASS="${2:-Passw0rd!}"

GRN=$'\e[32m'; YLW=$'\e[33m'; BLU=$'\e[36m'; RED=$'\e[31m'; NC=$'\e[0m'
say()  { echo "${BLU}==>${NC} $*"; }
ok()   { echo "${GRN}  ok${NC} $*"; }
warn() { echo "${YLW}  !!${NC} $*"; }
die()  { echo "${RED}FATAL:${NC} $*" >&2; exit 1; }

say "getting Keycloak admin token from $KC"
TOKEN="$(curl -s -m10 "$KC/realms/master/protocol/openid-connect/token" \
  -d grant_type=password -d client_id=admin-cli \
  -d "username=$ADMIN_USER" -d "password=$ADMIN_PASS" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))')"
[ -n "$TOKEN" ] || die "could not obtain admin token (is Keycloak up at $KC?)"
AUTH=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")

# --- realm -------------------------------------------------------------------
code="$(curl -s -o /dev/null -w '%{http_code}' -m10 "${AUTH[@]}" "$KC/admin/realms/$REALM")"
if [ "$code" = "200" ]; then ok "realm '$REALM' exists"
else
  say "creating realm '$REALM'"
  curl -s -m10 "${AUTH[@]}" -X POST "$KC/admin/realms" \
    -d "{\"realm\":\"$REALM\",\"enabled\":true,\"sslRequired\":\"none\"}" >/dev/null && ok "realm created" || die "realm create failed"
fi
# Dev only: allow plain-HTTP on localhost (Keycloak defaults sslRequired=external).
curl -s -m10 "${AUTH[@]}" -X PUT "$KC/admin/realms/$REALM" \
  -d "{\"realm\":\"$REALM\",\"enabled\":true,\"sslRequired\":\"none\"}" >/dev/null

# --- client (public, authorization code + PKCE S256) -------------------------
existing="$(curl -s -m10 "${AUTH[@]}" "$KC/admin/realms/$REALM/clients?clientId=$CLIENT_ID" \
  | python3 -c 'import sys,json; a=json.load(sys.stdin); print(a[0]["id"] if a else "")')"
CLIENT_BODY="{\"clientId\":\"$CLIENT_ID\",\"enabled\":true,\"publicClient\":true,\"protocol\":\"openid-connect\",\"standardFlowEnabled\":true,\"directAccessGrantsEnabled\":true,\"redirectUris\":[\"$REDIRECT\"],\"webOrigins\":[\"$WEB_ORIGIN\"],\"attributes\":{\"pkce.code.challenge.method\":\"S256\"}}"
if [ -n "$existing" ]; then
  say "updating client '$CLIENT_ID'"
  curl -s -m10 "${AUTH[@]}" -X PUT "$KC/admin/realms/$REALM/clients/$existing" -d "$CLIENT_BODY" >/dev/null && ok "client updated"
else
  say "creating client '$CLIENT_ID' (public, PKCE S256, redirect $REDIRECT)"
  curl -s -m10 "${AUTH[@]}" -X POST "$KC/admin/realms/$REALM/clients" -d "$CLIENT_BODY" >/dev/null && ok "client created" || die "client create failed"
fi

# --- test user (email == a seeded Windrose user) -----------------------------
uid="$(curl -s -m10 "${AUTH[@]}" "$KC/admin/realms/$REALM/users?email=$USER_EMAIL&exact=true" \
  | python3 -c 'import sys,json; a=json.load(sys.stdin); print(a[0]["id"] if a else "")')"
if [ -z "$uid" ]; then
  say "creating user '$USER_EMAIL'"
  curl -s -m10 "${AUTH[@]}" -X POST "$KC/admin/realms/$REALM/users" \
    -d "{\"username\":\"$USER_EMAIL\",\"email\":\"$USER_EMAIL\",\"emailVerified\":true,\"enabled\":true,\"firstName\":\"Data\",\"lastName\":\"Scientist\"}" >/dev/null
  uid="$(curl -s -m10 "${AUTH[@]}" "$KC/admin/realms/$REALM/users?email=$USER_EMAIL&exact=true" \
    | python3 -c 'import sys,json; a=json.load(sys.stdin); print(a[0]["id"] if a else "")')"
  [ -n "$uid" ] || die "user create failed"
  ok "user created ($uid)"
else ok "user '$USER_EMAIL' exists ($uid)"; fi

say "setting password"
curl -s -m10 "${AUTH[@]}" -X PUT "$KC/admin/realms/$REALM/users/$uid/reset-password" \
  -d "{\"type\":\"password\",\"value\":\"$USER_PASS\",\"temporary\":false}" >/dev/null && ok "password set"

echo
ok "Keycloak OIDC ready:"
echo "   issuer        : $KC/realms/$REALM"
echo "   discovery     : $KC/realms/$REALM/.well-known/openid-configuration"
echo "   client_id     : $CLIENT_ID  (public, PKCE S256)"
echo "   redirect_uri  : $REDIRECT"
echo "   login         : $USER_EMAIL / $USER_PASS"
