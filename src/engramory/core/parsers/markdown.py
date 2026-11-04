"""Markdown document parser with hierarchy, frontmatter, and word chunking (PLAN-010)."""

from __future__ import annotations

import re
from typing import Any

import yaml

from engramory.core.parsers.base import (
    BaseParser,
    DocType,
    ParseResult,
    Section,
)

MAX_CHUNK_WORDS_DEFAULT = 400


class MarkdownParser(BaseParser):
    """Parser for Markdown documents with hierarchical sectioning and frontmatter support."""

    supported_extensions = ["md", "markdown", "mdown", "mkd"]
    doc_type = DocType.MARKDOWN

    # ATX headers (# Header)
    HEADER_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#*)?$", re.MULTILINE)
    # Frontmatter block
    FRONTMATTER_PATTERN = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)

    def __init__(self, max_words_per_chunk: int = MAX_CHUNK_WORDS_DEFAULT) -> None:
        self.max_words_per_chunk = max_words_per_chunk

    def parse(self, content: str | bytes, filename: str | None = None) -> ParseResult:
        """Parse Markdown content into structured sections."""
        if isinstance(content, bytes):
            text = content.decode("utf-8", errors="replace")
        else:
            text = content

        # Normalize line endings
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # 1. Extract YAML frontmatter
        doc_metadata: dict[str, Any] = {}
        content_offset = 0
        frontmatter_match = self.FRONTMATTER_PATTERN.match(text)
        if frontmatter_match:
            fm_text = frontmatter_match.group(1)
            content_offset = frontmatter_match.end()
            try:
                loaded = yaml.safe_load(fm_text)
                if isinstance(loaded, dict):
                    doc_metadata = loaded
            except Exception:
                pass

        body = text[content_offset:]

        # 2. Find headers
        raw_headers = self._find_headers(body, base_offset=content_offset)

        if not raw_headers:
            # Single section fallback
            sec = Section(
                section_id="sec_1",
                name="Content",
                order=1,
                level=1,
                content=body.strip(),
                citation="§content",
                char_start=content_offset,
                char_end=len(text),
                source_tag="none",
                metadata=dict(doc_metadata),
            )
            title = doc_metadata.get("title") or filename
            return ParseResult(
                sections=[sec],
                doc_type=self.doc_type,
                title=title,
                metadata=doc_metadata,
                warnings=["No headers found in markdown document"]
                if body.strip()
                else ["Empty document"],
            )

        # 3. Build preliminary sections
        raw_sections = self._build_sections(text, body, raw_headers, content_offset, doc_metadata)

        # 4. Link hierarchy and compute breadcrumb citations
        self._link_subsections(raw_sections)

        # 5. Word-boundary sub-chunking for oversized sections
        final_sections: list[Section] = []
        order = 1
        for sec in raw_sections:
            chunks = self._subchunk_section(sec, start_order=order)
            for ch in chunks:
                final_sections.append(ch)
                order += 1

        # Extract title from frontmatter or first H1
        title = doc_metadata.get("title") or self._extract_title(final_sections) or filename

        return ParseResult(
            sections=final_sections,
            doc_type=self.doc_type,
            title=title,
            metadata=doc_metadata,
        )

    def _find_headers(self, body: str, base_offset: int) -> list[dict[str, Any]]:
        headers: list[dict[str, Any]] = []
        for match in self.HEADER_PATTERN.finditer(body):
            hashes = match.group(1)
            level = len(hashes)
            name = match.group(2).strip()
            headers.append(
                {
                    "level": level,
                    "name": name,
                    "start": base_offset + match.start(),
                    "end": base_offset + match.end(),
                    "source_tag": hashes,
                }
            )
        return headers

    def _build_sections(
        self,
        full_text: str,
        body: str,
        headers: list[dict[str, Any]],
        content_offset: int,
        doc_metadata: dict[str, Any],
    ) -> list[Section]:
        sections: list[Section] = []

        # Preamble before first header (if any)
        first_header_start = headers[0]["start"]
        if first_header_start > content_offset:
            preamble_content = full_text[content_offset:first_header_start].strip()
            if preamble_content:
                sections.append(
                    Section(
                        section_id=self._generate_section_id(1),
                        name="Preamble",
                        order=1,
                        level=1,
                        content=preamble_content,
                        char_start=content_offset,
                        char_end=first_header_start,
                        source_tag="preamble",
                        metadata=dict(doc_metadata),
                    )
                )

        for i, h in enumerate(headers):
            order = len(sections) + 1
            sec_id = self._generate_section_id(order)
            sec_start = h["start"]
            sec_end = headers[i + 1]["start"] if i + 1 < len(headers) else len(full_text)
            sec_content = full_text[sec_start:sec_end].strip()

            sections.append(
                Section(
                    section_id=sec_id,
                    name=h["name"],
                    order=order,
                    level=h["level"],
                    content=sec_content,
                    char_start=sec_start,
                    char_end=sec_end,
                    source_tag=h["source_tag"],
                    metadata=dict(doc_metadata),
                )
            )

        return sections

    def _subchunk_section(self, section: Section, start_order: int) -> list[Section]:
        """Split section if word count exceeds max_words_per_chunk."""
        words = section.content.split()
        if len(words) <= self.max_words_per_chunk:
            section.order = start_order
            return [section]

        sub_chunks: list[Section] = []
        base_citation = section.citation or "§section"
        cur_order = start_order

        for i in range(0, len(words), self.max_words_per_chunk):
            part_idx = (i // self.max_words_per_chunk) + 1
            chunk_words = words[i : i + self.max_words_per_chunk]
            chunk_content = " ".join(chunk_words)

            sub_citation = f"{base_citation}-p{part_idx}" if part_idx > 1 else base_citation
            sub_chunks.append(
                Section(
                    section_id=f"{section.section_id}_p{part_idx}",
                    name=f"{section.name} (Part {part_idx})" if part_idx > 1 else section.name,
                    order=cur_order,
                    level=section.level,
                    content=chunk_content,
                    parent_id=section.parent_id,
                    citation=sub_citation,
                    char_start=section.char_start,
                    char_end=section.char_end,
                    source_tag=section.source_tag,
                    metadata=dict(section.metadata),
                )
            )
            cur_order += 1

        return sub_chunks
