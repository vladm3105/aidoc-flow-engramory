"""Unit tests for line-level citation generation and doc_id namespacing (PLAN-010 Phase 3)."""

from __future__ import annotations

from pathlib import Path

from engramory.core.citations import (
    build_doc_id,
    calculate_line_numbers,
    format_github_citation,
    format_local_citation,
    generate_evidence_ref,
)


def test_build_doc_id_namespacing() -> None:
    # Project-scoped document
    doc_id_proj = build_doc_id("trading-core", "sdd/01_BRD/BRD-01.md")
    assert doc_id_proj == "trading-core:sdd/01_BRD/BRD-01.md"

    # Local document without project
    doc_id_local = build_doc_id(None, "notes/design.txt")
    assert doc_id_local == "local:notes/design.txt"

    # Windows backslash normalization
    doc_id_win = build_doc_id("my-proj", "docs\\api\\spec.md")
    assert doc_id_win == "my-proj:docs/api/spec.md"


def test_calculate_line_numbers() -> None:
    text = "line 1\nline 2\nline 3\nline 4\nline 5\n"
    # Find offsets of line 2 and line 3
    start = text.find("line 2")
    end = text.find("line 4")  # end before line 4 begins
    l_start, l_end = calculate_line_numbers(text, start, end)

    assert l_start == 2
    assert l_end == 4


def test_format_github_citation() -> None:
    url = format_github_citation(
        repo="vladm3105/aidoc-flow-engramory",
        commit_sha="a1b2c3d4e5f6",
        path="src/engramory/core/knowledge.py",
        line_start=15,
        line_end=35,
    )
    expected = (
        "https://github.com/vladm3105/aidoc-flow-engramory/blob/a1b2c3d4e5f6/"
        "src/engramory/core/knowledge.py#L15-L35"
    )
    assert url == expected

    # Single line
    url_single = format_github_citation(
        repo="vladm3105/aidoc-flow-engramory",
        commit_sha="a1b2c3d4e5f6",
        path="README.md",
        line_start=1,
        line_end=1,
    )
    assert url_single.endswith("#L1")


def test_format_local_citation() -> None:
    cit_lines = format_local_citation("docs/arch.md", line_start=10, line_end=20)
    assert cit_lines == "docs/arch.md#L10-L20"

    cit_slug = format_local_citation("docs/arch.md", section_slug="storage-layer")
    assert cit_slug == "docs/arch.md#storage-layer"


def test_generate_evidence_ref(tmp_path: Path) -> None:
    # 1. Valid git commit SHA
    ref_git = generate_evidence_ref(tmp_path / "foo.txt", git_sha="d3b07384d113edec")
    assert ref_git == "d3b07384d113edec"

    # 2. Local uncommitted file falls back to sha256
    local_file = tmp_path / "sample.md"
    local_file.write_text("Deterministic content for hash test", encoding="utf-8")

    ref_hash = generate_evidence_ref(local_file, git_sha="local-uncommitted")
    assert ref_hash.startswith("sha256:")
    assert len(ref_hash) == 7 + 64  # "sha256:" + 64-char hex
