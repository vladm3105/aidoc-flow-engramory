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
    # Fenced code block opener (excludes backticks/tildes in info string)
    _FENCE_OPEN_RE = re.compile(r"^(`{3,}|~{3,})([^\n`~]*)$")
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
            except Exception as exc:
                doc_metadata = {"_frontmatter_error": str(exc)}

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
            chunks = self._subchunk_section(sec, start_order=1) if body.strip() else [sec]
            title = doc_metadata.get("title") or filename
            return ParseResult(
                sections=chunks,
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
        in_fence = False
        fence_marker = ""
        cur_offset = 0

        for line in body.splitlines(keepends=True):
            line_no_nl = line.rstrip("\r\n")
            stripped = line_no_nl.strip()
            if not in_fence:
                m_fence = self._FENCE_OPEN_RE.match(stripped)
                if m_fence:
                    in_fence = True
                    fence_marker = m_fence.group(1)
                    cur_offset += len(line)
                    continue
                match = self.HEADER_PATTERN.match(line_no_nl)
                if match:
                    hashes = match.group(1)
                    level = len(hashes)
                    name = match.group(2).strip()
                    headers.append(
                        {
                            "level": level,
                            "name": name,
                            "start": base_offset + cur_offset,
                            "end": base_offset + cur_offset + len(line_no_nl),
                            "source_tag": hashes,
                        }
                    )
            else:
                if stripped.startswith(fence_marker) and stripped == fence_marker[0] * len(
                    stripped
                ):
                    in_fence = False
                    fence_marker = ""
            cur_offset += len(line)
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
        ctx_header = ""
        meta = section.metadata or {}
        if meta.get("id"):
            doc_id = str(meta.get("id")).strip()
            title = str(meta.get("title") or doc_id).strip()
            kind = str(meta.get("kind") or "doc").strip()
            domain = str(meta.get("domain") or "general").strip()
            tier = str(meta.get("tier") or meta.get("autonomy_tier") or "2").strip()
            ctx_header = (
                f"[Doc: {doc_id} | Title: {title} | Kind: {kind} "
                f"| Domain: {domain} | Tier: {tier} | Section: {section.name}]\n"
            )

        header_words = len(ctx_header.split()) if ctx_header else 0
        eff_max = max(1, self.max_words_per_chunk - header_words)

        words = section.content.split()
        if len(words) <= eff_max:
            section.order = start_order
            if ctx_header and not section.content.startswith("[Doc: "):
                section.content = f"{ctx_header}{section.content}"
            return [section]

        sub_chunks: list[Section] = []
        base_citation = section.citation or "§section"
        cur_order = start_order

        for i in range(0, len(words), eff_max):
            part_idx = (i // eff_max) + 1
            chunk_words = words[i : i + eff_max]
            chunk_body = " ".join(chunk_words)
            chunk_content = (
                f"{ctx_header}{chunk_body}"
                if ctx_header and not chunk_body.startswith("[Doc: ")
                else chunk_body
            )

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
