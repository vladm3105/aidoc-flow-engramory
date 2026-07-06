"""SecretsPort — read-only secret resolution.

Dev: env / SOPS / Infisical. Cloud: Secret Manager / Key Vault. Resolved values never
land in logs or the canonical store.
"""
from __future__ import annotations

from typing import Protocol


class SecretsPort(Protocol):
    async def get(self, *, name: str) -> str:
        """Resolve a secret by logical name. Raises if it is undefined."""
        ...
