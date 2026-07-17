"""RSA signing keys + JWKS publication.

agent-runtime is the ISSUER of proposal-execution grants and A2A card signatures.
tool-plane fetches our PUBLIC key from our JWKS endpoint (PROPOSAL_JWKS_URL) and
verifies our RS256 grants. In prod the private key comes from Vault/config
(``AR_GRANT_PRIVATE_KEY_PEM``); when unset we generate a real RSA keypair at boot
(dev/tests) and serve it — a REAL signature over a REAL JWKS, never a stub.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _b64url_uint(n: int) -> str:
    length = (n.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()


class SigningKey:
    """Holds one RSA private key + its ``kid`` and exposes a JWKS document."""

    def __init__(self, private_pem: str | None, kid: str) -> None:
        self.kid = kid
        if private_pem:
            self._private = serialization.load_pem_private_key(
                private_pem.encode(), password=None)
        else:
            self._private = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    @property
    def private_pem(self) -> str:
        return self._private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()

    @property
    def public_pem(self) -> str:
        return self._private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    def jwks(self) -> dict:
        """Standard JWKS with RSA public params (kty, kid, n, e, use, alg) — the
        exact shape tool-plane's ProposalVerifier.refresh() parses."""
        numbers = self._private.public_key().public_numbers()
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": self.kid,
                    "use": "sig",
                    "alg": "RS256",
                    "n": _b64url_uint(numbers.n),
                    "e": _b64url_uint(numbers.e),
                }
            ]
        }
