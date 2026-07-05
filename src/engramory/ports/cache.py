"""CachePort — interface; implementations in engramory.adapters.{dev,gcp,azure}."""
from __future__ import annotations
from typing import Protocol


class CachePort(Protocol):
    """Define the cache operations the core needs. Keep vendor-neutral."""
    ...
