"""Unit tests for knowledge chunking logic (PLAN-005 Phase 3)."""

from __future__ import annotations

from engramory.core.chunking import chunk_markdown


def test_chunk_markdown_splits_headings() -> None:
    doc = """# ADR-010: Agent-Facing Packaging

This is the preamble describing context.

## Decision

All memory access must route through the unified access surface.

## Consequences

CLI provides dev/CI access; MCP gateway provides authenticated production access.
"""
    chunks = chunk_markdown(doc, "ADR-010")
    assert len(chunks) == 3
    assert chunks[0][0] == "§adr-010--agent-facing-packaging"
    assert "preamble describing context" in chunks[0][1]

    assert chunks[1][0] == "§decision"
    assert "All memory access must route" in chunks[1][1]

    assert chunks[2][0] == "§consequences"
    assert "CLI provides dev/CI access" in chunks[2][1]


def test_chunk_markdown_subchunks_large_text() -> None:
    words = ["word"] * 700
    doc = f"## BigSection\n\n{' '.join(words)}"
    chunks = chunk_markdown(doc, "DOC-BIG")
    assert len(chunks) >= 3
    assert chunks[0][0] == "§bigsection"
    assert chunks[1][0] == "§bigsection-p2"
