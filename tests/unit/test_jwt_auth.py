"""Unit tests for JWKS validation and claims-to-ActorContext mapping (SPEC-01 / ADR-10).

Adheres strictly to the platform Offline Mock Invariant (DOC-ONB-SSO-01 §8).
"""

from __future__ import annotations

import base64
import time
from typing import Any
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from engramory.access.jwt_auth import (
    AuthenticationError,
    JWKSValidator,
    TokenClaims,
    check_token_revocation,
    claims_to_actor_context,
)


@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[rsa.RSAPrivateKey, bytes]:
    """Generate ephemeral RSA private key and public key PEM in memory."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_key, public_pem


@pytest.fixture(scope="module")
def untrusted_rsa_keypair() -> tuple[rsa.RSAPrivateKey, bytes]:
    """Generate a separate RSA keypair simulating an attacker or untrusted IdP."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_key, public_pem


def _int_to_base64url(val: int) -> str:
    n_bytes = val.to_bytes((val.bit_length() + 7) // 8, byteorder="big")
    return base64.urlsafe_b64encode(n_bytes).decode("ascii").rstrip("=")


def build_jwks_dict(public_key: rsa.RSAPublicKey, kid: str = "test-key-1") -> dict[str, Any]:
    """Build a mock JWKS response dictionary for an RSA public key."""
    numbers = public_key.public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": kid,
                "n": _int_to_base64url(numbers.n),
                "e": _int_to_base64url(numbers.e),
            }
        ]
    }


def mint_token(
    private_key: rsa.RSAPrivateKey,
    *,
    sub: str = "agent-iplanic-sub",
    iss: str = "https://auth-stage.aiops-flow.io/application/o/engramory/",
    aud: str = "engramory",
    exp_offset: int = 600,
    kid: str = "test-key-1",
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Mint an RS256 token signed by the provided private key."""
    now = int(time.time())
    payload = {
        "sub": sub,
        "iss": iss,
        "aud": aud,
        "iat": now,
        "exp": now + exp_offset,
        "jti": f"jti-{now}",
    }
    if extra_claims:
        payload.update(extra_claims)

    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(payload, priv_pem, algorithm="RS256", headers={"kid": kid})


def test_jwks_validator_valid_token(rsa_keypair: tuple[rsa.RSAPrivateKey, bytes]) -> None:
    priv_key, _ = rsa_keypair
    jwks_data = build_jwks_dict(priv_key.public_key(), kid="test-key-1")

    validator = JWKSValidator(
        jwks_url="https://mock.auth/jwks/",
        issuer="https://auth-stage.aiops-flow.io/application/o/engramory/",
        audience="engramory",
    )

    with patch.object(validator._jwks_client, "fetch_data", return_value=jwks_data):
        token = mint_token(
            priv_key,
            extra_claims={
                "agent_id": "agent-iplanic",
                "projects": ["aidoc-flow-iplanic"],
                "namespaces": ["aidoc"],
                "roles": ["developer"],
            },
        )
        claims = validator.validate_token(f"Bearer {token}")
        assert claims.sub == "agent-iplanic-sub"
        assert claims.agent_id == "agent-iplanic"
        assert claims.projects == ["aidoc-flow-iplanic"]
        assert claims.namespaces == ["aidoc"]
        assert "developer" in claims.groups
        assert claims.jti is not None


def test_jwks_validator_expired_token(rsa_keypair: tuple[rsa.RSAPrivateKey, bytes]) -> None:
    priv_key, _ = rsa_keypair
    jwks_data = build_jwks_dict(priv_key.public_key())

    validator = JWKSValidator(
        jwks_url="https://mock.auth/jwks/",
        issuer="https://auth-stage.aiops-flow.io/application/o/engramory/",
        audience="engramory",
    )

    with patch.object(validator._jwks_client, "fetch_data", return_value=jwks_data):
        expired_token = mint_token(priv_key, exp_offset=-60)
        with pytest.raises(AuthenticationError, match="Token has expired"):
            validator.validate_token(expired_token)


def test_jwks_validator_untrusted_signature(
    rsa_keypair: tuple[rsa.RSAPrivateKey, bytes],
    untrusted_rsa_keypair: tuple[rsa.RSAPrivateKey, bytes],
) -> None:
    trusted_priv, _ = rsa_keypair
    untrusted_priv, _ = untrusted_rsa_keypair
    jwks_data = build_jwks_dict(trusted_priv.public_key(), kid="test-key-1")

    validator = JWKSValidator(
        jwks_url="https://mock.auth/jwks/",
        issuer="https://auth-stage.aiops-flow.io/application/o/engramory/",
        audience="engramory",
    )

    with patch.object(validator._jwks_client, "fetch_data", return_value=jwks_data):
        tampered_token = mint_token(untrusted_priv, kid="test-key-1")
        with pytest.raises(AuthenticationError, match="Token validation failed"):
            validator.validate_token(tampered_token)


def test_jwks_validator_audience_and_issuer_mismatch(
    rsa_keypair: tuple[rsa.RSAPrivateKey, bytes],
) -> None:
    priv_key, _ = rsa_keypair
    jwks_data = build_jwks_dict(priv_key.public_key())

    validator = JWKSValidator(
        jwks_url="https://mock.auth/jwks/",
        issuer="https://auth-stage.aiops-flow.io/application/o/engramory/",
        audience="engramory",
    )

    with patch.object(validator._jwks_client, "fetch_data", return_value=jwks_data):
        wrong_aud = mint_token(priv_key, aud="wrong-audience")
        with pytest.raises(AuthenticationError, match="Token audience mismatch"):
            validator.validate_token(wrong_aud)

        wrong_iss = mint_token(priv_key, iss="https://rogue-idp.example.com/")
        with pytest.raises(AuthenticationError, match="Token issuer mismatch"):
            validator.validate_token(wrong_iss)


def test_claims_to_actor_context_mapping() -> None:
    # 1. Base agent claims
    claims1 = TokenClaims(
        sub="sub-1",
        iss="iss",
        aud="aud",
        exp=1000,
        raw_claims={
            "agent_id": "agent-coder",
            "projects": ["platform-core"],
            "namespaces": ["platform"],
            "roles": ["developer"],
        },
    )
    ctx1 = claims_to_actor_context(claims1)
    assert ctx1.agent_id == "agent-coder"
    assert ctx1.project_id == "platform-core"
    assert ctx1.tenant_id == "platform"
    assert ctx1.scopes == frozenset({"agent", "project"})

    # 2. Domain admin grant
    claims2 = TokenClaims(
        sub="sub-2",
        iss="iss",
        aud="aud",
        exp=1000,
        raw_claims={
            "agent_id": "agent-lead",
            "projects": ["aidoc-support"],
            "namespaces": ["aidoc"],
            "roles": ["domain-admin"],
        },
    )
    ctx2 = claims_to_actor_context(claims2)
    assert ctx2.scopes == frozenset({"agent", "project", "domain"})

    # 3. Platform admin grant
    claims3 = TokenClaims(
        sub="sub-3",
        iss="iss",
        aud="aud",
        exp=1000,
        raw_claims={
            "agent_id": "agent-super",
            "projects": ["platform-core"],
            "namespaces": ["platform"],
            "roles": ["platform-admin"],
        },
    )
    ctx3 = claims_to_actor_context(claims3)
    assert ctx3.scopes == frozenset({"agent", "project", "domain", "space"})


def test_check_token_revocation_kill_switch() -> None:
    claims = TokenClaims(
        sub="agent-compromised",
        iss="iss",
        aud="aud",
        exp=1000,
        raw_claims={"jti": "jti-revoked-123", "agent_id": "agent-compromised"},
    )

    # In-memory kill-switch check
    with pytest.raises(AuthenticationError, match="Token jti-revoked-123 has been revoked"):
        check_token_revocation(claims, revoked_identifiers={"jti-revoked-123"})

    with pytest.raises(AuthenticationError, match="Agent agent-compromised has been revoked"):
        check_token_revocation(claims, revoked_identifiers={"agent-compromised"})

    # Unrevoked token passes
    check_token_revocation(claims, revoked_identifiers={"unrelated-jti"})
