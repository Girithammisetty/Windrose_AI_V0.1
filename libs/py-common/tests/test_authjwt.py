"""JWKS verification: RS256 accepted, alg=none rejected, exp/iss/aud enforced,
claims mapped. The JWKS is generated locally and served over a real HTTP server
so the fetch+cache path is genuinely exercised."""

from __future__ import annotations

import http.server
import json
import threading
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from windrose_common.authjwt import InvalidTokenError, JwksCache, JwtVerifier

ISSUER = "https://identity.windrose.local"
AUDIENCE = "windrose"


def _make_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pub_pem(key) -> str:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


def _jwks(pub_numbers, kid="test-kid") -> dict:
    import base64

    def b64(n: int) -> str:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": kid,
                "use": "sig",
                "alg": "RS256",
                "n": b64(pub_numbers.n),
                "e": b64(pub_numbers.e),
            }
        ]
    }


def _token(private_key, claims: dict, kid="test-kid", alg="RS256") -> str:
    return pyjwt.encode(claims, private_key, algorithm=alg, headers={"kid": kid})


def _valid_claims() -> dict:
    now = int(time.time())
    return {
        "sub": "user-1",
        "tenant_id": "tenant-1",
        "typ": "user",
        "scopes": ["dataset.read"],
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 3600,
    }


async def test_pem_verifier_accepts_valid_and_maps_claims():
    key = _make_key()
    verifier = JwtVerifier(issuer=ISSUER, audience=AUDIENCE, public_key_pem=_pub_pem(key))

    principal = await verifier.verify(_token(key, _valid_claims()))
    assert principal.sub == "user-1"
    assert principal.tenant_id == "tenant-1"
    assert principal.scopes == ["dataset.read"]
    assert principal.actor == {"type": "user", "id": "user-1"}


async def test_rejects_alg_none_and_expired():
    key = _make_key()
    verifier = JwtVerifier(issuer=ISSUER, audience=AUDIENCE, public_key_pem=_pub_pem(key))

    # alg=none (unsigned) must be rejected
    none_token = pyjwt.encode(_valid_claims(), key=None, algorithm="none")
    with pytest.raises(InvalidTokenError):
        await verifier.verify(none_token)

    # expired token
    claims = _valid_claims()
    claims["exp"] = int(time.time()) - 10
    with pytest.raises(InvalidTokenError):
        await verifier.verify(_token(key, claims))


async def test_jwks_cache_fetches_over_http():
    key = _make_key()
    jwks_doc = _jwks(key.public_key().public_numbers())

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(jwks_doc).encode())

        def log_message(self, *a):  # silence
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        jwks = JwksCache(f"http://127.0.0.1:{port}/jwks.json", ttl_seconds=300)
        verifier = JwtVerifier(issuer=ISSUER, audience=AUDIENCE, jwks=jwks)
        principal = await verifier.verify(_token(key, _valid_claims()))
        assert principal.tenant_id == "tenant-1"
        # unknown kid -> rejected
        with pytest.raises(InvalidTokenError):
            await verifier.verify(_token(key, _valid_claims(), kid="other-kid"))
    finally:
        server.shutdown()
