"""Unit tests for extraction router, semantic chunker, and docling caching (PLAN-010)."""

from __future__ import annotations

from pathlib import Path

from engramory.core.extraction.docling import DoclingExtractor
from engramory.core.extraction.enrichment import SemanticChunker
from engramory.core.extraction.router import SmartExtractionRouter
from engramory.core.extraction.types import DoclingExtractionResult, FileCategory
from engramory.core.parsers.base import Section


def test_semantic_chunker_splitting() -> None:
    chunker = SemanticChunker(min_words=10, max_words=50)
    sec = Section(
        section_id="sec_1",
        name="Long Section",
        order=1,
        level=1,
        content=" ".join(["word"] * 120),
        citation="§long-section",
    )
    refined = chunker.refine_sections([sec])

    assert len(refined) == 3
    assert refined[0].citation == "§long-section"
    assert refined[1].citation == "§long-section-p2"
    assert refined[2].citation == "§long-section-p3"
    assert refined[0].order == 1
    assert refined[1].order == 2
    assert refined[2].order == 3


def test_semantic_chunker_merging() -> None:
    chunker = SemanticChunker(min_words=15, max_words=100)
    sec1 = Section(
        section_id="sec_1",
        name="Intro",
        order=1,
        level=1,
        content="This is a solid introduction to the platform architecture.",
        citation="§intro",
    )
    sec2 = Section(
        section_id="sec_2",
        name="Note",
        order=2,
        level=2,
        content="Tiny note.",  # Only 2 words (< 15 min_words)
        citation="§note",
    )
    refined = chunker.refine_sections([sec1, sec2])

    assert len(refined) == 1
    assert "Tiny note." in refined[0].content


def test_docling_extractor_caching(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    extractor = DoclingExtractor(cache_dir=cache_dir, enable_cache=True)

    file_hash = "fakehash1234567890"
    mock_result = DoclingExtractionResult(
        text="# Cached Document\nExtracted content.",
        tables=[{"index": 0, "markdown": "| A | B |"}],
        metadata={"title": "Cached"},
        file_category=FileCategory.DOCUMENT,
        extraction_time_ms=42,
    )

    extractor._write_cache(file_hash, mock_result)
    read_back = extractor._read_cache(file_hash)

    assert read_back is not None
    assert read_back.text == mock_result.text
    assert read_back.tables == mock_result.tables
    assert read_back.cache_hit is True


def test_smart_router_native_file_extraction(tmp_path: Path) -> None:
    doc_file = tmp_path / "guide.md"
    doc_file.write_text(
        "# Engineering Guide\n\n## Standards\nFollow platform engineering standards.",
        encoding="utf-8",
    )

    router = SmartExtractionRouter(enable_docling=False, min_words_per_chunk=5)
    res = router.extract_file(doc_file)

    assert res.extractor_used == "native"
    assert res.title == "Engineering Guide"
    assert len(res.sections) == 2
    assert res.sections[0].name == "Engineering Guide"
    assert res.sections[1].name == "Standards"


def test_smart_router_content_extraction() -> None:
    router = SmartExtractionRouter()
    content = "# Quick Notes\nImportant item 1.\nImportant item 2."
    res = router.extract_content(content, filename="quick.md")

    assert res.extractor_used == "native"
    assert len(res.sections) == 1
    assert res.sections[0].name == "Quick Notes"
