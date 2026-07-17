"""Shared config, JWKS + token minting for the Windrose e2e journey.

The harness plays the role of the platform IdP (Keycloak in prod): it holds one
RSA private key, mints real RS256 platform JWTs, and publishes the matching
public key as a real JWKS over HTTP. Every service verifies these tokens with
its real verifier. identity-service (which has no external-JWKS verifier) is
additionally seeded with the same public key in its signing_keys table so it
accepts the harness super-admin token at its real provisioning endpoint.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization

# ---- issuer / audience (identity-service hardcodes these; everyone matches) ----
ISS = "https://identity.windrose.ai"
AUD = "windrose"
KID = "e2e-harness-key-1"
NIL_TENANT = "00000000-0000-0000-0000-000000000000"

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY_DIR = os.path.join(HERE, "keys")
PRIV_PEM_PATH = os.path.join(KEY_DIR, "idp_private.pem")
PUB_PEM_PATH = os.path.join(KEY_DIR, "idp_public.pem")

# ---- service base URLs (ports chosen to avoid every infra port) ----
def _u(env, default):
    return os.environ.get(env, default)

JWKS_PORT = int(_u("E2E_JWKS_PORT", "8300"))
IDENTITY = _u("IDENTITY_URL", "http://localhost:8301")
RBAC = _u("RBAC_URL", "http://localhost:8302")
INGESTION = _u("INGESTION_URL", "http://localhost:8303")
DATASET = _u("DATASET_URL", "http://localhost:8304")
REALTIME = _u("REALTIME_URL", "http://localhost:8305")
REALTIME_INTERNAL = _u("REALTIME_INTERNAL_URL", "http://localhost:8315")
AGENT_RUNTIME = _u("AGENT_RUNTIME_URL", "http://localhost:8306")
MEMORY = _u("MEMORY_URL", "http://localhost:8307")
CASE = _u("CASE_URL", "http://localhost:8308")
TOOL_REGISTRY = _u("TOOL_REGISTRY_URL", "http://localhost:8310")
MCP_GATEWAY = _u("MCP_GATEWAY_URL", "http://localhost:8311")
AI_GATEWAY = _u("AI_GATEWAY_URL", "http://localhost:8312")
PIPELINE = _u("PIPELINE_URL", "http://localhost:8313")
EXPERIMENT = _u("EXPERIMENT_URL", "http://localhost:8314")
INFERENCE = _u("INFERENCE_URL", "http://localhost:8316")

# ---- infra ----
PG = _u("E2E_PG", "postgres://windrose:windrose_dev@localhost:5432")
REDIS_ADDR = _u("E2E_REDIS", "localhost:6379")
KAFKA = _u("E2E_KAFKA", "localhost:9092")
SCHEMA_REGISTRY = _u("E2E_SCHEMA_REGISTRY", "http://localhost:8081")
OPA = _u("E2E_OPA", "http://localhost:8281")
OLLAMA = _u("E2E_OLLAMA", "http://localhost:11434")
S3_ENDPOINT = _u("E2E_S3", "http://localhost:9000")
S3_KEY = "windrose"
S3_SECRET = "windrose_dev"
ICEBERG = _u("E2E_ICEBERG", "http://localhost:8181")
OPENSEARCH = _u("E2E_OPENSEARCH", "http://localhost:9200")
VAULT = _u("E2E_VAULT", "http://localhost:8200")
JWKS_URL = _u("E2E_JWKS_URL", f"http://localhost:{JWKS_PORT}/jwks.json")
MLFLOW = _u("MLFLOW_URL", "http://localhost:5500")

AGENT_ID = "case-triage"
AGENT_VERSION = "1.0.0"


def _load_private():
    with open(PRIV_PEM_PATH, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def _b64u(n: int) -> str:
    length = (n.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()


def jwks_document() -> dict:
    pub = _load_private().public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "kid": KID, "use": "sig", "alg": "RS256",
                      "n": _b64u(pub.n), "e": _b64u(pub.e)}]}


def public_pem() -> str:
    with open(PUB_PEM_PATH) as f:
        return f.read()


def _jwk_private() -> dict:
    """The harness RSA private key as an RFC7517 JWK (for ui-web's dev IdP so it
    signs user tokens with the SAME key every backend verifies)."""
    priv = _load_private()
    n = priv.private_numbers()
    pub = n.public_numbers
    return {"kty": "RSA", "kid": KID, "alg": "RS256", "use": "sig",
            "n": _b64u(pub.n), "e": _b64u(pub.e), "d": _b64u(n.d),
            "p": _b64u(n.p), "q": _b64u(n.q), "dp": _b64u(n.dmp1),
            "dq": _b64u(n.dmq1), "qi": _b64u(n.iqmp)}


def _jwk_public() -> dict:
    pub = _load_private().public_key().public_numbers()
    return {"kty": "RSA", "kid": KID, "alg": "RS256", "use": "sig",
            "n": _b64u(pub.n), "e": _b64u(pub.e)}


def _mint(claims: dict, ttl: int = 3600) -> str:
    now = int(time.time())
    body = {"iss": ISS, "aud": AUD, "iat": now, "nbf": now, "exp": now + ttl,
            "jti": str(uuid.uuid4()), **claims}
    return pyjwt.encode(body, _load_private(), algorithm="RS256", headers={"kid": KID})


def superadmin_token() -> str:
    # platform.admin -> identity super-admin; super_admin -> rbac RequireSuperAdmin;
    # operator/tenant.admin -> agent-runtime registry; "*" -> scope wildcard.
    return _mint({"sub": "svc:e2e-bootstrap", "tenant_id": NIL_TENANT,
                  "typ": "service",
                  "scopes": ["platform.admin", "super_admin", "operator",
                             "tenant.admin", "*"]})


def user_token(user_id: str, tenant_id: str, scopes: list[str], workspace_id: str | None = None) -> str:
    claims = {"sub": user_id, "tenant_id": tenant_id, "typ": "user", "scopes": scopes}
    if workspace_id:
        claims["workspace_id"] = workspace_id
    return _mint(claims)


def service_token(sub: str, tenant_id: str, scopes: list[str]) -> str:
    return _mint({"sub": sub, "tenant_id": tenant_id, "typ": "service", "scopes": scopes})


def agent_obo_token(user_id: str, tenant_id: str, scopes: list[str], session_id: str,
                    workspace_id: str | None = None) -> str:
    claims = {"sub": f"agent:{AGENT_ID}@{AGENT_VERSION}", "tenant_id": tenant_id,
              "typ": "agent_obo", "agent_id": AGENT_ID, "agent_version": AGENT_VERSION,
              "obo_sub": user_id, "scopes": scopes, "session_id": session_id}
    if workspace_id:
        claims["workspace_id"] = workspace_id
    return _mint(claims)


def args_digest(args: dict) -> str:
    """sha256 over canonical JSON — must match agent-runtime/tool-plane digest."""
    canon = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def proposal_grant(sub: str, tenant_id: str, tool_id: str, args_digest_hex: str,
                   proposal_id: str, tier: str = "write-proposal", ttl: int = 120,
                   issuer: str = "windrose-agent-runtime") -> str:
    """Mint the RS256-signed proposal-execution grant tool-plane verifies
    (TPL-FR-035). Signed with the SAME harness key + kid agent-runtime uses
    (AR_GRANT_PRIVATE_KEY_PEM=idp_private.pem, AR_GRANT_KID=e2e-harness-key-1) and
    the agent-runtime issuer, so tool-plane's grant verifier (PROPOSAL_JWKS_URL =
    agent-runtime JWKS, PROPOSAL_ISSUER = windrose-agent-runtime) accepts it. This
    is exactly the grant agent-runtime issues after a human approves a proposal;
    the harness mints it to drive the full federated write deterministically."""
    now = int(time.time())
    body = {"iss": issuer, "sub": sub, "iat": now, "nbf": now, "exp": now + ttl,
            "jti": str(uuid.uuid4()), "proposal_id": proposal_id, "tenant_id": tenant_id,
            "tool_id": tool_id, "tier": tier, "args_digest": args_digest_hex}
    return pyjwt.encode(body, _load_private(), algorithm="RS256", headers={"kid": KID})


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "jwks":
        print(json.dumps(jwks_document(), indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "superadmin":
        print(superadmin_token())
    elif len(sys.argv) > 1 and sys.argv[1] == "jwk_private":
        print(json.dumps(_jwk_private()))
    elif len(sys.argv) > 1 and sys.argv[1] == "jwk_public":
        print(json.dumps(_jwk_public()))
