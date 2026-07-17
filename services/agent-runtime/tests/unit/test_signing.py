"""Signing contract: args_digest matches tool-plane's Go, and the issued grant
verifies exactly as tool-plane's ProposalVerifier does (RS256, iss, exp,
tenant/tool/tier/args_digest binding). Forged/tampered grants are rejected."""

from __future__ import annotations

import jwt as pyjwt
import pytest

from app.constants import GRANT_ISSUER
from app.domain.canonical import args_digest, canonical_json
from app.signing import GrantIssuer, SigningKey


def test_args_digest_matches_go_reference():
    # Pinned against tool-plane internal/domain.ArgsDigest (computed from the real
    # Go function). ASCII + non-ASCII (accented names, currency, HTML chars, and
    # the U+2028/U+2029 separators Go escapes) must all hash identically — a
    # non-ASCII mismatch here would silently break the approved-write money path.
    _LS, _PS = chr(0x2028), chr(0x2029)
    cases = {
        "35b296568cd9e3a2efbe7ead6d90e05fd2233b3c2424081c69a751e08a7038b8":
            {"case_id": "c-91", "severity": "high", "assignee_id": "u-dana"},
        "bab977010642be5be9687dcf8208d9b7f45fa601bb6d002ce53db2f28101b947":
            {"x": "café"},
        "582776322d68a9dd78b78bc508a1c1e152e4cff2704cc8eba1fbac62a185d7a8":
            {"merchant": "Zürich Ré", "note": "Peña Auto €1.250,50"},
        "39ced192ca6f24512017b2d17b45f4960b81629fafc7836b1321d2bc00746194":
            {"amount": "€1234", "gbp": "£99", "jpy": "¥500"},
        "81868b8a19c5106c2f341856cb71be05fd60d0a968f53e1da983ebfa538b15c7":
            {"k": "a<b>&c"},
        "bd6384fe9cf1238e0427f597abf19e8ebe1b0ca0cdef70f55b84645930caca31":
            {"k": f"line{_LS}para{_PS}end"},
    }
    for expected, args in cases.items():
        assert args_digest(args) == expected, args


def test_non_ascii_grant_digest_is_go_compatible():
    # A grant over non-ASCII args carries the SAME digest tool-plane recomputes,
    # so the approved disposition actually executes (regression for the money-path bug).
    key = SigningKey(None, "k1")
    args = {"merchant": "Zürich Ré", "severity": "high", "amount": "€1.250,50"}
    grant = GrantIssuer(key).issue(
        proposal_id="p-9", tenant_id="t-42", tool_id="case.apply_disposition",
        tier="write-proposal", args=args, decided_by="u-super")
    claims = pyjwt.decode(grant, key.public_pem, algorithms=["RS256"], issuer=GRANT_ISSUER,
                          options={"require": ["exp"]})
    assert claims["args_digest"] == args_digest(args)
    # exact digest tool-plane's Go domain.ArgsDigest produces for these args
    assert claims["args_digest"] == \
        "2f47046c1bdd079714fcfa68328994e7e0b33493c4907ece5a6f2e2a4335f3ba"


def test_canonical_sorts_keys_and_is_compact():
    assert canonical_json({"b": 1, "a": "x"}) == b'{"a":"x","b":1}'
    assert canonical_json({"b": [3, 1], "a": {"z": 1, "y": 2}}) == b'{"a":{"y":2,"z":1},"b":[3,1]}'


def _verify_like_tool_plane(grant, pub_pem, *, tenant, tool_id, tier, digest):
    """Reproduces services/tool-plane/internal/authz/proposal.go VerifyGrant."""
    claims = pyjwt.decode(grant, pub_pem, algorithms=["RS256"], issuer=GRANT_ISSUER,
                          options={"require": ["exp"]})
    assert claims["proposal_id"]
    assert claims["tenant_id"] == tenant
    assert claims["tool_id"] == tool_id
    assert claims["tier"] == tier
    assert claims["args_digest"] == digest
    return claims


def test_grant_issues_and_verifies():
    key = SigningKey(None, "agent-runtime-2026-1")
    issuer = GrantIssuer(key)
    args = {"case_id": "c-91", "severity": "high", "assignee_id": "u-dana"}
    grant = issuer.issue(proposal_id="p-1", tenant_id="t-42", tool_id="case.apply_disposition",
                         tier="write-proposal", args=args, decided_by="u-super")
    # header carries kid matching the JWKS
    hdr = pyjwt.get_unverified_header(grant)
    assert hdr["alg"] == "RS256" and hdr["kid"] == key.kid
    claims = _verify_like_tool_plane(
        grant, key.public_pem, tenant="t-42", tool_id="case.apply_disposition",
        tier="write-proposal", digest=args_digest(args))
    assert claims["sub"] == "u-super"
    assert claims["iss"] == GRANT_ISSUER


def test_grant_rejected_on_arg_tampering():
    key = SigningKey(None, "k1")
    issuer = GrantIssuer(key)
    args = {"case_id": "c-91", "severity": "high"}
    grant = issuer.issue(proposal_id="p-1", tenant_id="t-42", tool_id="case.apply_disposition",
                         tier="write-proposal", args=args, decided_by="u")
    tampered = {"case_id": "c-91", "severity": "critical"}
    with pytest.raises(AssertionError):
        _verify_like_tool_plane(grant, key.public_pem, tenant="t-42",
                                tool_id="case.apply_disposition", tier="write-proposal",
                                digest=args_digest(tampered))


def test_forged_grant_wrong_key_rejected():
    real, forger = SigningKey(None, "k1"), SigningKey(None, "k2")
    issuer = GrantIssuer(forger)  # signed by the wrong key
    grant = issuer.issue(proposal_id="p-1", tenant_id="t-42", tool_id="x", tier="write-proposal",
                         args={}, decided_by="u")
    with pytest.raises(pyjwt.InvalidSignatureError):
        pyjwt.decode(grant, real.public_pem, algorithms=["RS256"], issuer=GRANT_ISSUER,
                     options={"require": ["exp"]})


def test_jwks_shape():
    key = SigningKey(None, "agent-runtime-2026-1")
    jwks = key.jwks()
    k = jwks["keys"][0]
    assert k["kty"] == "RSA" and k["kid"] == key.kid and k["alg"] == "RS256"
    assert k["n"] and k["e"]
