"""Unit tests for universal document parsers (PLAN-010 Phase 1)."""

from __future__ import annotations

from engramory.core.parsers.base import DocType, Section, slugify
from engramory.core.parsers.detector import DocumentDetector
from engramory.core.parsers.html import HTMLParser
from engramory.core.parsers.markdown import MarkdownParser
from engramory.core.parsers.pdf import PDFParser
from engramory.core.parsers.template import TemplateParser
from engramory.core.parsers.text import TextParser


def test_slugify() -> None:
    assert slugify("System Overview") == "system-overview"
    assert slugify("API Reference (v1.0) & Details!") == "api-reference-v1-0-details"
    assert slugify("   --Hello World--  ") == "hello-world"
    assert slugify("") == "section"


def test_section_dataclass_serialization() -> None:
    sec = Section(
        section_id="sec_1",
        name="Overview",
        order=1,
        level=1,
        content="This is the overview.",
        citation="§overview",
        metadata={"author": "AI team"},
    )
    assert sec.content_length == 21
    data = sec.to_dict()
    assert data["section_id"] == "sec_1"
    assert data["metadata"]["author"] == "AI team"

    hydrated = Section.from_dict(data)
    assert hydrated.section_id == "sec_1"
    assert hydrated.name == "Overview"
    assert hydrated.metadata["author"] == "AI team"


def test_markdown_parser_hierarchy_and_breadcrumbs() -> None:
    md_content = """---
title: Sample Architecture Guide
author: Engramory Team
tags: [architecture, docs]
---
Welcome to the guide. This is the preamble.

# Architecture
High level architecture notes.

## Storage Layer
Details on PostgreSQL and vector indexes.

### Configuration
Postgres connection pooling config.

## Access Layer
Details on MCP and CLI access surface.

### Configuration
MCP port and authentication settings.
"""
    parser = MarkdownParser(max_words_per_chunk=300)
    result = parser.parse(md_content, filename="arch.md")

    assert result.doc_type == DocType.MARKDOWN
    assert result.title == "Sample Architecture Guide"
    assert result.metadata.get("author") == "Engramory Team"

    sections = result.sections
    assert len(sections) == 6

    # 1. Preamble
    assert sections[0].name == "Preamble"
    assert sections[0].citation == "§preamble"

    # 2. Architecture (H1)
    sec_arch = sections[1]
    assert sec_arch.name == "Architecture"
    assert sec_arch.level == 1
    assert sec_arch.citation == "§architecture"
    assert sec_arch.parent_id is None

    # 3. Storage Layer (H2 under Architecture)
    sec_storage = sections[2]
    assert sec_storage.name == "Storage Layer"
    assert sec_storage.level == 2
    assert sec_storage.parent_id == sec_arch.section_id
    assert sec_storage.citation == "§architecture/storage-layer"

    # 4. First Configuration (H3 under Storage Layer)
    sec_cfg1 = sections[3]
    assert sec_cfg1.name == "Configuration"
    assert sec_cfg1.level == 3
    assert sec_cfg1.parent_id == sec_storage.section_id
    assert sec_cfg1.citation == "§architecture/storage-layer/configuration"

    # 5. Access Layer (H2 under Architecture)
    sec_access = sections[4]
    assert sec_access.name == "Access Layer"
    assert sec_access.level == 2
    assert sec_access.parent_id == sec_arch.section_id
    assert sec_access.citation == "§architecture/access-layer"

    # 6. Second Configuration (H3 under Access Layer) - Breadcrumb must differ!
    sec_cfg2 = sections[5]
    assert sec_cfg2.name == "Configuration"
    assert sec_cfg2.level == 3
    assert sec_cfg2.parent_id == sec_access.section_id
    assert sec_cfg2.citation == "§architecture/access-layer/configuration"

    # Crucial assertion: Two headings named 'Configuration' must NOT collide!
    assert sec_cfg1.citation != sec_cfg2.citation


def test_markdown_parser_duplicate_sibling_slugs() -> None:
    # Test identical headings under the same parent
    md_content = """# Main
## Step
First step text.
## Step
Second step text.
"""
    parser = MarkdownParser()
    result = parser.parse(md_content)
    citations = [s.citation for s in result.sections]

    assert "§main/step" in citations
    assert "§main/step-2" in citations
    assert len(citations) == len(set(citations))  # All citations unique!


def test_markdown_parser_word_chunking() -> None:
    words = ["word"] * 550
    long_content = "# Big Section\n" + " ".join(words)

    parser = MarkdownParser(max_words_per_chunk=200)
    result = parser.parse(long_content)

    assert result.section_count == 3
    assert result.sections[0].citation == "§big-section"
    assert result.sections[1].citation == "§big-section-p2"
    assert result.sections[2].citation == "§big-section-p3"


def test_markdown_parser_no_headers() -> None:
    content = "Just plain notes with no headings at all."
    parser = MarkdownParser()
    result = parser.parse(content, filename="plain.md")

    assert result.section_count == 1
    assert result.sections[0].content == content
    assert result.sections[0].citation == "§content"


def test_html_parser_headers_and_title() -> None:
    html_content = """<!DOCTYPE html>
<html>
<head>
    <title>HTML Documentation</title>
    <script>alert("ignore");</script>
</head>
<body>
    <h1>Getting Started</h1>
    <p>Introductory paragraph.</p>
    <h2>Installation</h2>
    <p>Run pip install engramory.</p>
</body>
</html>
"""
    parser = HTMLParser()
    result = parser.parse(html_content)

    assert result.doc_type == DocType.HTML
    assert result.title == "HTML Documentation"
    assert result.section_count == 2

    assert result.sections[0].name == "Getting Started"
    assert "Introductory paragraph" in result.sections[0].content
    assert "alert" not in result.sections[0].content  # script removed

    assert result.sections[1].name == "Installation"
    assert result.sections[1].citation == "§getting-started/installation"


def test_template_parser_markers_and_variables() -> None:
    template_content = """
<!-- @section: executive_summary -->
This project aims to deliver {project_goal} within {deadline_months} months.
<!-- @end -->

# @section: architecture_overview
The platform architecture utilizes {primary_database} and Neo4j.
<!-- @end -->
"""
    parser = TemplateParser()
    result = parser.parse(template_content)

    assert result.doc_type == DocType.TEMPLATE
    assert result.section_count == 2
    assert result.sections[0].name == "executive_summary"
    assert "{project_goal}" in result.sections[0].content
    assert result.sections[0].metadata["variables"] == ["project_goal", "deadline_months"]

    assert result.sections[1].name == "architecture_overview"
    assert result.sections[1].metadata["variables"] == ["primary_database"]

    assert sorted(result.metadata["template_variables"]) == [
        "deadline_months",
        "primary_database",
        "project_goal",
    ]


def test_text_parser_paragraphs_and_chunks() -> None:
    text_content = """First major paragraph describing system goals.
It spans across multiple lines.

Second paragraph detailing runtime considerations.
Another sentence here.

Third paragraph with conclusion.
"""
    # Paragraph strategy
    p_parser = TextParser(strategy="paragraph")
    res_p = p_parser.parse(text_content)
    assert res_p.section_count == 3
    assert res_p.sections[0].citation == "§part-1"
    assert res_p.sections[1].citation == "§part-2"

    # Chunk strategy
    c_parser = TextParser(strategy="chunk", chunk_words=5)
    res_c = c_parser.parse("One two three four five six seven eight nine ten eleven.")
    assert res_c.section_count == 3


def test_pdf_parser_fallback_warning() -> None:
    # Test that PDFParser doesn't crash when passing binary bytes
    fake_pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
    parser = PDFParser()
    result = parser.parse(fake_pdf, filename="test.pdf")

    assert result.doc_type == DocType.PDF
    assert result.title == "test.pdf"


def test_document_detector_routing() -> None:
    detector = DocumentDetector()

    # 1. Template detection by marker
    assert detector.detect("<!-- @section: intro -->\nHello") == DocType.TEMPLATE
    assert detector.detect("# @section: intro\nHello") == DocType.TEMPLATE

    # 2. PDF detection by magic or filename
    assert detector.detect(b"%PDF-1.5 ...") == DocType.PDF
    assert detector.detect("fake", filename="report.pdf") == DocType.PDF

    # 3. HTML detection
    assert detector.detect("<html><body><h1>Hi</h1></body></html>") == DocType.HTML
    assert detector.detect("some text", filename="index.html") == DocType.HTML

    # 4. Markdown detection
    assert detector.detect("# Title\n## Section\nBody") == DocType.MARKDOWN
    assert detector.detect("Some notes", filename="README.md") == DocType.MARKDOWN

    # 5. Text fallback
    assert (
        detector.detect("Just a simple log entry with no markup", filename="app.log")
        == DocType.TEXT
    )

    # 6. End-to-end parse via detector
    res = detector.parse("# Hello from Markdown\nThis works.", filename="doc.md")
    assert res.doc_type == DocType.MARKDOWN
    assert res.section_count == 1
    assert res.sections[0].name == "Hello from Markdown"
