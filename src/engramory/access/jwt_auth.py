"""Cached JWKS Token Validator and Claims-to-ActorContext Engine (SPEC-01 / ADR-10).

Integrates with Authentik OIDC and platform-security 2-Axis Hybrid RBAC.
"""

from __future__ import annotations

import logging
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

from engramory.access.authz import ActorContext
from engramory.core.models import SCOPES

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Raised when token verification or revocation checks fail."""


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Verified identity claims extracted from a valid Authentik JWT."""

    sub: str
    iss: str
    aud: str
    exp: int
    raw_claims: dict[str, Any]

    @property
    def jti(self) -> str | None:
        val = self.raw_claims.get("jti")
        return str(val).strip() if val else None

    @property
    def groups(self) -> list[str]:
        groups = self.raw_claims.get("groups") or self.raw_claims.get("roles") or []
        return [str(g) for g in groups]

    @property
    def namespaces(self) -> list[str]:
        """2-Axis Axis 2 disjoint root namespaces (platform, aidoc, b-local, security)."""
        raw_ns = self.raw_claims.get("namespaces") or []
        extracted = [str(n).strip() for n in raw_ns if str(n).strip()]
        for g in self.groups:
            if g.startswith("ns:"):
                ns_val = g.split(":", 1)[1].strip()
                if ns_val and ns_val not in extracted:
                    extracted.append(ns_val)
        return extracted

    @property
    def projects(self) -> list[str]:
        """Bound project identifiers."""
        raw_proj = self.raw_claims.get("projects") or []
        return [str(p).strip() for p in raw_proj if str(p).strip()]

    @property
    def agent_role(self) -> str | None:
        """2-Axis Axis 1 functional swarm agent role (coder, pm, triage, sec-scanner, etc.)."""
        role = self.raw_claims.get("agent_role")
        if role:
            return str(role).strip()
        for g in self.groups:
            if g.startswith("role:"):
                return g.split(":", 1)[1].strip()
            if g.startswith("agent-"):
                return g.split("-", 1)[1].strip()
        return None

    @property
    def agent_id(self) -> str | None:
        """Service account identifier for autonomous swarm agents."""
        val = self.raw_claims.get("agent_id")
        return str(val).strip() if val else None

    @property
    def actor(self) -> dict[str, Any] | None:
        """RFC 8693 act claim for delegated agent execution."""
        act = self.raw_claims.get("act")
        return act if isinstance(act, dict) else None


class JWKSValidator:
    """Thread-safe JWKS validator with caching and key rotation support."""

    def __init__(
        self,
        jwks_url: str,
        issuer: str,
        audience: str,
        *,
        algorithms: list[str] | None = None,
        cache_jwk_set: bool = True,
        lifespan: int = 300,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.jwks_url = jwks_url
        self.issuer = issuer
        self.audience = audience
        self.algorithms = algorithms or ["RS256"]
        req_headers = {"User-Agent": "Engramory-Auth/1.0"}
        if headers:
            req_headers.update(headers)
        self.headers = req_headers
        self.cache_jwk_set = cache_jwk_set
        self.lifespan = lifespan
        self._jwks_client = PyJWKClient(
            uri=jwks_url,
            cache_jwk_set=cache_jwk_set,
            lifespan=lifespan,
            headers=req_headers,
        )
        self._lock = threading.Lock()
        self._last_jwks_refresh: float = 0.0

    def validate_token(self, token: str) -> TokenClaims:
        """Validate a Bearer JWT against the JWKS endpoint.

        Args:
            token: Raw Bearer JWT string (with or without 'Bearer ' prefix).

        Returns:
            TokenClaims with verified payload.

        Raises:
            AuthenticationError: On expired, invalid signature, or wrong claims.
        """
        import time

        if not token:
            raise AuthenticationError("Missing token")

        clean_token = token.strip()
        if clean_token.startswith("Bearer "):
            clean_token = clean_token[7:].strip()

        try:
            try:
                signing_key = self._jwks_client.get_signing_key_from_jwt(clean_token)
            except PyJWKClientError:
                with self._lock:
                    now_mono = time.monotonic()
                    if now_mono - self._last_jwks_refresh >= 5.0:
                        self._jwks_client = PyJWKClient(
                            uri=self.jwks_url,
                            cache_jwk_set=self.cache_jwk_set,
                            lifespan=self.lifespan,
                            headers=self.headers,
                        )
                        self._last_jwks_refresh = now_mono
                signing_key = self._jwks_client.get_signing_key_from_jwt(clean_token)

            payload = jwt.decode(
                clean_token,
                signing_key.key,
                algorithms=self.algorithms,
                audience=self.audience,
                issuer=self.issuer,
                options={
                    "require": ["exp", "iss", "aud", "sub"],
                    "verify_exp": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
            return TokenClaims(
                sub=payload["sub"],
                iss=payload["iss"],
                aud=payload["aud"],
                exp=payload["exp"],
                raw_claims=payload,
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Token has expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthenticationError("Token audience mismatch") from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthenticationError("Token issuer mismatch") from exc
        except (jwt.PyJWTError, PyJWKClientError, Exception) as exc:
            raise AuthenticationError(f"Token validation failed: {exc}") from exc


def claims_to_actor_context(claims: TokenClaims) -> ActorContext:
    """Map verified TokenClaims to canonical ActorContext with ADR-07 scope ladder grants."""
    agent_id = claims.agent_id or claims.sub

    raw_tenant = claims.raw_claims.get("tenant_id")
    if raw_tenant:
        tenant_id = str(raw_tenant).strip()
    elif claims.namespaces:
        tenant_id = claims.namespaces[0]
    else:
        tenant_id = "default"

    raw_projects = claims.projects
    project_id = raw_projects[0] if raw_projects else "core"

    raw_domain = claims.raw_claims.get("domain_id") or claims.raw_claims.get("department_focus")
    domain_id: str | None = None
    if raw_domain:
        domain_id = str(raw_domain).strip() or None
    else:
        for grp in claims.groups:
            if grp.startswith("domain:"):
                domain_id = grp.split(":", 1)[1].strip() or None
                if domain_id:
                    break

    # Evaluate role-authorized scopes
    max_scopes: set[str] = {"agent", "project"}
    roles = set(claims.groups)
    if "domain-admin" in roles or domain_id is not None:
        max_scopes.add("domain")
    if any(r in roles for r in ("platform-admin", "superuser")) or claims.raw_claims.get(
        "is_superuser"
    ):
        max_scopes.update({"domain", "space"})

    if "memory_scopes" in claims.raw_claims:
        custom_scopes = claims.raw_claims["memory_scopes"]
        if isinstance(custom_scopes, list):
            granted_scopes = {str(s).strip() for s in custom_scopes} & max_scopes
        else:
            granted_scopes = max_scopes
    else:
        granted_scopes = max_scopes

    valid_scopes = frozenset(granted_scopes & SCOPES)
    return ActorContext(
        agent_id=agent_id,
        tenant_id=tenant_id,
        project_id=project_id,
        scopes=valid_scopes,
        domain_id=domain_id,
    )


def check_token_revocation(
    claims: TokenClaims,
    security_gate_url: str | None = None,
    *,
    revoked_identifiers: set[str] | None = None,
    fail_closed: bool | None = None,
) -> None:
    """Check if token jti or agent_id has been revoked by platform kill-switch.

    Args:
        claims: Verified token claims.
        security_gate_url: Optional Security Gate HTTP URL (e.g. http://security-gate:8095).
        revoked_identifiers: Optional in-memory set of revoked JTIs or agent IDs (for tests).
        fail_closed: If True (default unless SECURITY_GATE_FAIL_CLOSED=false), raise
            AuthenticationError when a configured security_gate_url is unreachable.
    """
    import os

    if fail_closed is None:
        fail_closed = os.environ.get("SECURITY_GATE_FAIL_CLOSED", "true").lower() not in (
            "0",
            "false",
            "no",
        )
    jti = claims.jti
    agent_id = claims.agent_id or claims.sub
    actor_sub = claims.actor.get("sub") if isinstance(claims.actor, dict) else None
    principals = {p for p in (agent_id, claims.sub, actor_sub) if p}

    # Check local in-memory set if provided
    if revoked_identifiers:
        if jti and jti in revoked_identifiers:
            raise AuthenticationError(f"Token {jti} has been revoked by platform kill-switch")
        for principal in principals:
            if principal in revoked_identifiers:
                raise AuthenticationError(
                    f"Agent {principal} has been revoked by platform kill-switch"
                )

    # Check Security Gate if configured
    if security_gate_url:
        target_url = f"{security_gate_url.rstrip('/')}/v1/gate/revocations"
        if not (target_url.startswith("http://") or target_url.startswith("https://")):
            raise ValueError(f"Invalid security gate URL scheme: {target_url}")
        try:
            req = urllib.request.Request(target_url, headers={"User-Agent": "Engramory-Auth/1.0"})
            # nosemgrep  # reason: target_url scheme validated to http/https
            with urllib.request.urlopen(req, timeout=2.0) as resp:  # nosec B310  # reason: scheme ok
                if resp.status != 200:
                    raise ValueError(f"Unexpected Security Gate status {resp.status}")
                import json

                data = json.loads(resp.read().decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("Invalid Security Gate revocation response")
                revoked_list = data.get("revocations", [])
                revoked_set = {r.get("target") for r in revoked_list if isinstance(r, dict)}
                if jti and jti in revoked_set:
                    raise AuthenticationError(
                        f"Token {jti} has been revoked by platform kill-switch"
                    )
                for principal in principals:
                    if principal in revoked_set:
                        raise AuthenticationError(
                            f"Agent {principal} has been revoked by platform kill-switch"
                        )
        except (urllib.error.URLError, ValueError, OSError) as exc:
            logger.warning("Could not reach Security Gate revocation endpoint: %s", exc)
            if fail_closed:
                raise AuthenticationError(
                    f"Token revocation check failed (fail-closed): {exc}"
                ) from exc
