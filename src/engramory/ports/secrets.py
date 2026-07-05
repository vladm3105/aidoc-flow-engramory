"""SecretsPort — interface; implementations in engramory.adapters.{dev,gcp,azure}."""
from __future__ import annotations
from typing import Protocol


class SecretsPort(Protocol):
    """Define the secrets operations the core needs. Keep vendor-neutral."""
    ...
