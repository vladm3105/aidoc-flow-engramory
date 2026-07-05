"""VectorPort — interface; implementations in engramory.adapters.{dev,gcp,azure}."""
from __future__ import annotations
from typing import Protocol


class VectorPort(Protocol):
    """Define the vector operations the core needs. Keep vendor-neutral."""
    ...
