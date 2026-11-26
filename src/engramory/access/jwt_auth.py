"""Cached JWKS Token Validator and Claims-to-ActorContext Engine (SPEC-01 / ADR-10).

Integrates with Authentik OIDC and platform-security 2-Axis Hybrid RBAC.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import threading
import time
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

from engramory.access.authz import ActorContext
from engramory.core.models import SCOPES, _frozen_mapping, thaw_mapping

logger = logging.getLogger(__name__)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_INTERNAL_SUFFIXES = (".internal", ".local", ".int.aiops-flow.io", ".mesh", ".cluster.local")
_MAX_REVOCATION_BODY_BYTES = 1024 * 1024


class AuthenticationError(Exception):
    """Raised when token verification or revocation checks fail."""


def _extract_str_list(raw: Any, claim_name: str) -> list[str]:
    """Validate and extract a list of non-empty strings from a JWT claim."""
    if raw is None:
        return []
    if not isinstance(raw, list | tuple):
        raise AuthenticationError(f"Malformed JWT '{claim_name}' claim: expected list")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise AuthenticationError(f"Malformed JWT '{claim_name}' claim entry: expected string")
        cleaned = item.strip()
        if cleaned:
            out.append(cleaned)
    return out


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Verified identity claims extracted from a valid Authentik JWT."""

    sub: str
    iss: str
    aud: str
    exp: int
    raw_claims: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_claims", _frozen_mapping(self.raw_claims))

    @property
    def jti(self) -> str | None:
        val = self.raw_claims.get("jti")
        return str(val).strip() if val else None

    @property
    def groups(self) -> list[str]:
        if "groups" in self.raw_claims and self.raw_claims["groups"] is not None:
            return _extract_str_list(self.raw_claims["groups"], "groups")
        if "roles" in self.raw_claims and self.raw_claims["roles"] is not None:
            return _extract_str_list(self.raw_claims["roles"], "roles")
        return []

    @property
    def namespaces(self) -> list[str]:
        """2-Axis Axis 2 disjoint root namespaces (platform, aidoc, b-local, security)."""
        extracted = _extract_str_list(self.raw_claims.get("namespaces"), "namespaces")
        for g in self.groups:
            if g.startswith("ns:"):
                ns_val = g.split(":", 1)[1].strip()
                if ns_val and ns_val not in extracted:
                    extracted.append(ns_val)
        return extracted

    @property
    def projects(self) -> list[str]:
        """Bound project identifiers."""
        return _extract_str_list(self.raw_claims.get("projects"), "projects")

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
        return thaw_mapping(act) if isinstance(act, Mapping) else None


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
        if clean_token[:7].lower() == "bearer ":
            clean_token = clean_token[7:].strip()
        if not clean_token:
            raise AuthenticationError("Missing token")

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


def _is_internal_mesh_host(hostname: str) -> bool:
    """Return True for loopback, RFC1918/private IP, single-label, or internal DNS hosts."""
    host = hostname.strip("[]").lower()
    if not host:
        return False
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        ip_obj = ipaddress.ip_address(host)
        return ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_link_local
    except ValueError:
        pass
    if "." not in host:
        return True
    return host.endswith(_INTERNAL_SUFFIXES)


def validate_security_gate_url(security_gate_url: str) -> tuple[str, str | None]:
    """Validate Security Gate URL; enforce HTTPS on external hosts, return (url, uds_path)."""
    cleaned = security_gate_url.strip()
    if not cleaned:
        raise ValueError("Empty security_gate_url")
    parsed = urllib.parse.urlparse(cleaned)
    scheme = parsed.scheme.lower()
    if scheme in {"unix", "http+unix"}:
        if not parsed.path:
            raise ValueError(f"Unix domain socket path required in security_gate_url: {cleaned}")
        return "http://localhost/v1/gate/revocations", parsed.path
    if scheme not in {"http", "https"}:
        raise ValueError(f"Invalid security gate URL scheme: {cleaned}")
    host = parsed.hostname or ""
    if not host:
        raise ValueError(f"Missing host in security_gate_url: {cleaned}")
    if parsed.username or parsed.password:
        raise ValueError("Credentials in security_gate_url are prohibited")
    if scheme == "http" and not _is_internal_mesh_host(host):
        raise ValueError(
            f"Plaintext HTTP is prohibited for external security_gate_url '{cleaned}'; "
            "use HTTPS or internal mesh host"
        )
    return f"{cleaned.rstrip('/')}/v1/gate/revocations", None


def claims_to_actor_context(claims: TokenClaims) -> ActorContext:
    """Map verified TokenClaims to canonical ActorContext with fail-closed ADR-07 scope grants."""
    agent_id = claims.agent_id or (claims.sub.strip() if isinstance(claims.sub, str) else "")
    if not agent_id:
        raise AuthenticationError("Missing agent_id/sub claim")

    raw_tenant = claims.raw_claims.get("tenant_id")
    if raw_tenant is not None and not isinstance(raw_tenant, str):
        raise AuthenticationError("Malformed tenant_id claim: expected string")
    if isinstance(raw_tenant, str) and raw_tenant.strip():
        tenant_id = raw_tenant.strip()
    elif claims.namespaces:
        tenant_id = claims.namespaces[0]
    else:
        raise AuthenticationError("Missing required tenant_id or namespaces claim")

    raw_projects = claims.projects
    raw_project_id = claims.raw_claims.get("project_id")
    if raw_project_id is not None and not isinstance(raw_project_id, str):
        raise AuthenticationError("Malformed project_id claim: expected string")
    if raw_projects:
        project_id = raw_projects[0]
    elif isinstance(raw_project_id, str) and raw_project_id.strip():
        project_id = raw_project_id.strip()
    else:
        raise AuthenticationError("Missing required project_id or projects claim")

    raw_domain = claims.raw_claims.get("domain_id") or claims.raw_claims.get("department_focus")
    domain_id: str | None = None
    if raw_domain is not None:
        if not isinstance(raw_domain, str):
            raise AuthenticationError("Malformed domain_id claim: expected string")
        domain_id = raw_domain.strip() or None
    else:
        for grp in claims.groups:
            if grp.startswith("domain:"):
                domain_id = grp.split(":", 1)[1].strip() or None
                if domain_id:
                    break

    # Evaluate role-authorized scopes (no domain auto-grant; strict bool is_superuser)
    max_scopes: set[str] = {"agent", "project"}
    roles = set(claims.groups)
    if "domain-admin" in roles:
        max_scopes.add("domain")
    if any(r in roles for r in ("platform-admin", "superuser")) or (
        claims.raw_claims.get("is_superuser") is True
    ):
        max_scopes.update({"domain", "space"})

    if "memory_scopes" in claims.raw_claims:
        custom_scopes = claims.raw_claims["memory_scopes"]
        if not isinstance(custom_scopes, list):
            raise AuthenticationError("Malformed memory_scopes claim: expected list")
        requested: set[str] = set()
        for entry in custom_scopes:
            if not isinstance(entry, str) or not entry.strip():
                raise AuthenticationError(
                    "Malformed memory_scopes entry: expected non-empty string"
                )
            requested.add(entry.strip())
        granted_scopes = requested & max_scopes
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


class RevocationCache:
    """Thread-safe short-TTL cache of revoked JTIs/principals fetched from Security Gate."""

    def __init__(self, ttl_seconds: float = 15.0) -> None:
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, frozenset[str]]] = {}

    def get(self, cache_key: str) -> frozenset[str] | None:
        now = time.monotonic()
        with self._lock:
            hit = self._entries.get(cache_key)
            if hit is None:
                return None
            fetched_at, values = hit
            if now - fetched_at > self._ttl_seconds:
                self._entries.pop(cache_key, None)
                return None
            return values

    def set(self, cache_key: str, values: frozenset[str]) -> None:
        with self._lock:
            self._entries[cache_key] = (time.monotonic(), values)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def _resolve_fail_closed(fail_closed: bool | None) -> bool:
    if fail_closed is not None:
        return fail_closed
    return os.environ.get("SECURITY_GATE_FAIL_CLOSED", "true").lower() not in (
        "0",
        "false",
        "no",
    )


def _principals_for_claims(claims: TokenClaims) -> tuple[str | None, set[str]]:
    jti = claims.jti
    agent_id = claims.agent_id or claims.sub
    actor_sub = claims.actor.get("sub") if isinstance(claims.actor, dict) else None
    principals = {p for p in (agent_id, claims.sub, actor_sub) if p}
    return jti, principals


def _assert_not_revoked(
    jti: str | None, principals: set[str], revoked_set: set[str] | frozenset[str]
) -> None:
    if jti and jti in revoked_set:
        raise AuthenticationError(f"Token {jti} has been revoked by platform kill-switch")
    for principal in principals:
        if principal in revoked_set:
            raise AuthenticationError(f"Agent {principal} has been revoked by platform kill-switch")


def _parse_revocation_response(resp: httpx.Response) -> frozenset[str]:
    if resp.status_code != 200:
        raise ValueError(f"Unexpected Security Gate status {resp.status_code}")
    raw_bytes = resp.content
    if len(raw_bytes) > _MAX_REVOCATION_BODY_BYTES:
        raise ValueError(
            f"Security Gate revocation payload exceeds {_MAX_REVOCATION_BODY_BYTES} bytes"
        )
    data = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid Security Gate revocation response")
    if "revocations" not in data or data["revocations"] is None:
        if "revocations" in data and data["revocations"] is None:
            raise ValueError("Invalid Security Gate revocation list: got null")
        revoked_list: Any = []
    else:
        revoked_list = data["revocations"]
    if not isinstance(revoked_list, list):
        raise ValueError("Invalid Security Gate revocation list: expected list")
    revoked_targets: set[str] = set()
    for item in revoked_list:
        if not isinstance(item, dict):
            raise ValueError("Invalid Security Gate revocation entry: expected object")
        target = item.get("target")
        if isinstance(target, str) and target.strip():
            revoked_targets.add(target.strip())
    return frozenset(revoked_targets)


def _build_security_gate_headers(bearer_token: str | None = None) -> dict[str, str]:
    """Build Security Gate headers with optional service Bearer token (never user JWT)."""
    headers: dict[str, str] = {"User-Agent": "Engramory-Auth/1.0"}
    raw = bearer_token if bearer_token is not None else os.environ.get("SECURITY_GATE_TOKEN", "")
    clean = raw.strip()
    if clean[:7].lower() == "bearer ":
        clean = clean[7:].strip()
    if clean:
        headers["Authorization"] = f"Bearer {clean}"
    return headers


def check_token_revocation(
    claims: TokenClaims,
    security_gate_url: str | None = None,
    *,
    revoked_identifiers: set[str] | None = None,
    fail_closed: bool | None = None,
    cache: RevocationCache | None = None,
    bearer_token: str | None = None,
) -> None:
    """Synchronously check if token jti or agent_id has been revoked by platform kill-switch."""
    eff_fail_closed = _resolve_fail_closed(fail_closed)
    jti, principals = _principals_for_claims(claims)

    if revoked_identifiers:
        _assert_not_revoked(jti, principals, revoked_identifiers)

    if security_gate_url:
        target_url, uds_path = validate_security_gate_url(security_gate_url)
        cache_key = f"{uds_path or ''}|{target_url}"
        cached = cache.get(cache_key) if cache is not None else None
        if cached is not None:
            _assert_not_revoked(jti, principals, cached)
            return
        try:
            transport = httpx.HTTPTransport(uds=uds_path) if uds_path else None
            headers = _build_security_gate_headers(bearer_token)
            with httpx.Client(timeout=2.0, follow_redirects=False, transport=transport) as client:
                resp = client.get(target_url, headers=headers)
            revoked_set = _parse_revocation_response(resp)
            if cache is not None:
                cache.set(cache_key, revoked_set)
            _assert_not_revoked(jti, principals, revoked_set)
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not reach Security Gate revocation endpoint: %s", exc)
            if eff_fail_closed:
                raise AuthenticationError(
                    f"Token revocation check failed (fail-closed): {exc}"
                ) from exc


async def check_token_revocation_async(
    claims: TokenClaims,
    security_gate_url: str | None = None,
    *,
    revoked_identifiers: set[str] | None = None,
    fail_closed: bool | None = None,
    cache: RevocationCache | None = None,
    bearer_token: str | None = None,
) -> None:
    """Asynchronously check if token jti or agent_id has been revoked by platform kill-switch."""
    eff_fail_closed = _resolve_fail_closed(fail_closed)
    jti, principals = _principals_for_claims(claims)

    if revoked_identifiers:
        _assert_not_revoked(jti, principals, revoked_identifiers)

    if security_gate_url:
        target_url, uds_path = validate_security_gate_url(security_gate_url)
        cache_key = f"{uds_path or ''}|{target_url}"
        cached = cache.get(cache_key) if cache is not None else None
        if cached is not None:
            _assert_not_revoked(jti, principals, cached)
            return
        try:
            transport = httpx.AsyncHTTPTransport(uds=uds_path) if uds_path else None
            headers = _build_security_gate_headers(bearer_token)
            async with httpx.AsyncClient(
                timeout=2.0, follow_redirects=False, transport=transport
            ) as client:
                resp = await client.get(target_url, headers=headers)
            revoked_set = _parse_revocation_response(resp)
            if cache is not None:
                cache.set(cache_key, revoked_set)
            _assert_not_revoked(jti, principals, revoked_set)
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not reach Security Gate revocation endpoint: %s", exc)
            if eff_fail_closed:
                raise AuthenticationError(
                    f"Token revocation check failed (fail-closed): {exc}"
                ) from exc
