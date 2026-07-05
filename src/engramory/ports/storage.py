"""StoragePort — interface; implementations in engramory.adapters.{dev,gcp,azure}."""
from __future__ import annotations
from typing import Protocol


class StoragePort(Protocol):
    """Define the storage operations the core needs. Keep vendor-neutral."""
    ...
