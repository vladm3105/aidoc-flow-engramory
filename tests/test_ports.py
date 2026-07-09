"""Smoke tests for the ports layer.

These assert the vendor-neutral port contract exists and is coherent — they do not
touch infrastructure. Real adapter/integration tests arrive with the first adapter.
"""
from __future__ import annotations

import inspect

import engramory.ports as ports

EXPECTED_PORTS = {
    "MemoryPort",
    "StoragePort",
    "VectorPort",
    "GraphPort",
    "CachePort",
    "LLMPort",
    "SecretsPort",
    "EventsPort",
}


def test_eight_ports_exported() -> None:
    assert set(ports.__all__) == EXPECTED_PORTS


def test_ports_are_protocols() -> None:
    for name in EXPECTED_PORTS:
        port = getattr(ports, name)
        assert inspect.isclass(port), f"{name} should be a class"
        assert getattr(port, "_is_protocol", False), f"{name} should be a typing.Protocol"


def test_memory_port_learning_loop_surface() -> None:
    """The learning loop (SPEC-01/03/04) is part of the port contract."""
    for method in ("add_episode", "search", "reflect", "consolidate",
                   "feedback", "forget", "get_profile"):
        assert hasattr(ports.MemoryPort, method), f"MemoryPort missing '{method}'"
    sig = inspect.signature(ports.MemoryPort.search)
    for param in ("include_advisory", "token_budget"):
        assert param in sig.parameters, f"MemoryPort.search missing '{param}'"


def test_memory_port_scope_surface() -> None:
    """MemoryPort.search must accept the scope model's identifiers (ADR-07)."""
    sig = inspect.signature(ports.MemoryPort.search)
    for param in ("agent_id", "project_id", "domain_id", "tenant_id", "scope"):
        assert param in sig.parameters, f"MemoryPort.search missing '{param}'"
