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
    check_token_revocation_async,
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


def test_c7_fail_closed_claims_and_revocation_hardening() -> None:
    # 1. Missing tenant_id or project_id fails closed
    with pytest.raises(AuthenticationError, match="tenant_id"):
        claims_to_actor_context(
            TokenClaims(
                sub="a1", iss="iss", aud="aud", exp=9999999999, raw_claims={"projects": ["p1"]}
            )
        )
    with pytest.raises(AuthenticationError, match="project_id"):
        claims_to_actor_context(
            TokenClaims(
                sub="a1", iss="iss", aud="aud", exp=9999999999, raw_claims={"tenant_id": "t1"}
            )
        )

    # 2. String 'false' is_superuser cannot grant 'space', and domain_id never grants 'domain'
    claims_str_false = TokenClaims(
        sub="a1",
        iss="iss",
        aud="aud",
        exp=9999999999,
        raw_claims={
            "tenant_id": "t1",
            "projects": ["p1"],
            "domain_id": "billing",
            "is_superuser": "false",
        },
    )
    ctx = claims_to_actor_context(claims_str_false)
    assert ctx.scopes == frozenset({"agent", "project"})
    assert "domain" not in ctx.scopes
    assert "space" not in ctx.scopes

    # 3. Non-list memory_scopes or groups fail closed
    with pytest.raises(AuthenticationError, match="memory_scopes"):
        claims_to_actor_context(
            TokenClaims(
                sub="a1",
                iss="iss",
                aud="aud",
                exp=9999999999,
                raw_claims={"tenant_id": "t1", "projects": ["p1"], "memory_scopes": "space"},
            )
        )
    with pytest.raises(AuthenticationError, match="groups"):
        _ = TokenClaims(
            sub="a1",
            iss="iss",
            aud="aud",
            exp=9999999999,
            raw_claims={"tenant_id": "t1", "projects": ["p1"], "groups": "admin"},
        ).groups

    # 4. External plaintext http security_gate_url is rejected & null revocations fails closed
    from unittest.mock import MagicMock

    from engramory.access.jwt_auth import validate_security_gate_url

    with pytest.raises(ValueError, match="Plaintext HTTP is prohibited"):
        validate_security_gate_url("http://evil.example.com:8095")
    assert validate_security_gate_url("http://security-gate:8095")[1] is None

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"revocations": null}'
    with patch("httpx.Client.get", return_value=mock_resp):
        with pytest.raises(AuthenticationError, match="fail-closed"):
            check_token_revocation(
                claims_str_false,
                security_gate_url="http://127.0.0.1:8095",
                fail_closed=True,
            )


def test_wave_p3_token_claims_immutability_actor_and_jwt_rejections(
    rsa_keypair: tuple[rsa.RSAPrivateKey, bytes],
) -> None:
    priv_key, _ = rsa_keypair
    jwks_data = build_jwks_dict(priv_key.public_key(), kid="test-key-1")

    # 1. Deep immutability of TokenClaims.raw_claims + independent mutable actor copy
    claims = TokenClaims(
        sub="sub-immut",
        iss="https://auth-stage.aiops-flow.io/application/o/engramory/",
        aud="engramory",
        exp=9999999999,
        raw_claims={
            "roles": ["developer", "reviewer"],
            "act": {"sub": "supervisor-1", "nested": {"scope": "project"}},
        },
    )
    with pytest.raises(TypeError):
        claims.raw_claims["sub"] = "hijacked"  # type: ignore[index]  # reason: immutable
    with pytest.raises(TypeError):
        claims.raw_claims["act"]["sub"] = "hijacked"
    assert isinstance(claims.raw_claims["roles"], tuple)

    actor = claims.actor
    assert actor == {"sub": "supervisor-1", "nested": {"scope": "project"}}
    assert actor is not None
    actor["sub"] = "mutated-copy"
    assert claims.raw_claims["act"]["sub"] == "supervisor-1"

    validator = JWKSValidator(
        jwks_url="https://mock.auth/jwks/",
        issuer="https://auth-stage.aiops-flow.io/application/o/engramory/",
        audience="engramory",
    )

    with patch.object(validator._jwks_client, "fetch_data", return_value=jwks_data):
        # 2. Unsigned 'none' algorithm token rejected
        unsigned_token = jwt.encode(
            {
                "sub": "agent-none",
                "iss": "https://auth-stage.aiops-flow.io/application/o/engramory/",
                "aud": "engramory",
                "iat": int(time.time()),
                "exp": int(time.time()) + 600,
            },
            key=None,
            algorithm="none",
        )
        with pytest.raises(AuthenticationError):
            validator.validate_token(unsigned_token)

        # 3. Missing kid rejected when JWKS keys require matching kid
        no_kid_token = jwt.encode(
            {
                "sub": "agent-nokid",
                "iss": "https://auth-stage.aiops-flow.io/application/o/engramory/",
                "aud": "engramory",
                "iat": int(time.time()),
                "exp": int(time.time()) + 600,
            },
            priv_key,
            algorithm="RS256",
        )
        with pytest.raises(AuthenticationError):
            validator.validate_token(no_kid_token)

        # 4. Missing or invalid exp rejected
        missing_exp = jwt.encode(
            {
                "sub": "agent-noexp",
                "iss": "https://auth-stage.aiops-flow.io/application/o/engramory/",
                "aud": "engramory",
            },
            priv_key,
            algorithm="RS256",
            headers={"kid": "test-key-1"},
        )
        with pytest.raises(AuthenticationError):
            validator.validate_token(missing_exp)

        bad_exp = jwt.encode(
            {
                "sub": "agent-badexp",
                "iss": "https://auth-stage.aiops-flow.io/application/o/engramory/",
                "aud": "engramory",
                "exp": "not-a-timestamp",
            },
            priv_key,
            algorithm="RS256",
            headers={"kid": "test-key-1"},
        )
        with pytest.raises(AuthenticationError):
            validator.validate_token(bad_exp)


@pytest.mark.asyncio
async def test_security_gate_revocation_bearer_header_handling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polling Security Gate attaches Authorization Bearer only when a token is provided."""
    import asyncio
    from unittest.mock import MagicMock

    claims = TokenClaims(
        sub="agent-dev-01",
        iss="https://auth-stage.aiops-flow.io/application/o/engramory/",
        aud="engramory",
        exp=9999999999,
        raw_claims={"jti": "jti-ok"},
    )

    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.content = b'{"revocations": [], "count": 0}'

    # 1. Sync + env var over https:// TCP (with "Bearer " prefix stripped case-insensitively)
    monkeypatch.setenv("SECURITY_GATE_TOKEN", "bearer gate-env-secret")
    captured_sync: list[tuple[str, dict[str, str]]] = []

    def fake_sync_get(_self, url: str, headers: dict[str, str] | None = None):
        captured_sync.append((url, dict(headers or {})))
        return ok_resp

    with patch("httpx.Client.get", new=fake_sync_get):
        check_token_revocation(claims, security_gate_url="https://security-gate.internal:8443")
        check_token_revocation(claims, security_gate_url="unix:///run/platform-security/gate.sock")
    assert captured_sync[0][1]["Authorization"] == "Bearer gate-env-secret"
    assert captured_sync[0][1]["User-Agent"] == "Engramory-Auth/1.0"
    assert captured_sync[1][1]["Authorization"] == "Bearer gate-env-secret"

    # 2. Async + explicit bearer_token kwarg overrides env over unix:// UDS and https://
    captured_async: list[tuple[str, dict[str, str]]] = []

    async def fake_async_get(_self, url: str, headers: dict[str, str] | None = None):
        captured_async.append((url, dict(headers or {})))
        return ok_resp

    with patch("httpx.AsyncClient.get", new=fake_async_get):
        await check_token_revocation_async(
            claims,
            security_gate_url="unix:///run/platform-security/gate.sock",
            bearer_token="explicit-gate-secret",
        )
        await check_token_revocation_async(
            claims,
            security_gate_url="https://security-gate.internal:8443",
            bearer_token="explicit-gate-secret",
        )
    assert captured_async[0][1]["Authorization"] == "Bearer explicit-gate-secret"
    assert captured_async[1][1]["Authorization"] == "Bearer explicit-gate-secret"

    # 3. Credential-bearing requests over plain http:// TCP are rejected without hitting httpx
    def fail_if_called_sync(*_args, **_kwargs):
        raise AssertionError("httpx.Client.get must not run for plaintext HTTP + Bearer")

    async def fail_if_called_async(*_args, **_kwargs):
        raise AssertionError("httpx.AsyncClient.get must not run for plaintext HTTP + Bearer")

    with patch("httpx.Client.get", new=fail_if_called_sync):
        with pytest.raises(ValueError, match="Plaintext HTTP TCP is prohibited"):
            check_token_revocation(
                claims,
                security_gate_url="http://127.0.0.1:8095",
                fail_closed=False,
            )

    with patch("httpx.AsyncClient.get", new=fail_if_called_async):
        with pytest.raises(ValueError, match="Plaintext HTTP TCP is prohibited"):
            await check_token_revocation_async(
                claims,
                security_gate_url="http://127.0.0.1:8095",
                bearer_token="explicit-gate-secret",
                fail_closed=False,
            )

    # 4. Unset/blank omits Authorization header even on plain loopback HTTP
    monkeypatch.setenv("SECURITY_GATE_TOKEN", "   ")
    with patch("httpx.Client.get", new=fake_sync_get):
        check_token_revocation(claims, security_gate_url="http://127.0.0.1:8095")
    assert "Authorization" not in captured_sync[-1][1]

    with patch("httpx.AsyncClient.get", new=fake_async_get):
        await check_token_revocation_async(claims, security_gate_url="http://127.0.0.1:8095")
    assert "Authorization" not in captured_async[-1][1]
    assert asyncio.iscoroutinefunction(check_token_revocation_async)
