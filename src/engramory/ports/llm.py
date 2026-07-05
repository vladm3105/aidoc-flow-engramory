"""LLMPort — interface; implementations in engramory.adapters.{dev,gcp,azure}."""
from __future__ import annotations
from typing import Protocol


class LLMPort(Protocol):
    """Define the llm operations the core needs. Keep vendor-neutral."""
    ...
