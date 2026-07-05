"""GraphPort — interface; implementations in engramory.adapters.{dev,gcp,azure}."""
from __future__ import annotations
from typing import Protocol


class GraphPort(Protocol):
    """Define the graph operations the core needs. Keep vendor-neutral."""
    ...
