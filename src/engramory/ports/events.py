"""EventsPort — interface; implementations in engramory.adapters.{dev,gcp,azure}."""
from __future__ import annotations
from typing import Protocol


class EventsPort(Protocol):
    """Define the events operations the core needs. Keep vendor-neutral."""
    ...
