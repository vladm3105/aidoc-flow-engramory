"""Plain text document parser with paragraph and fixed-size chunking (PLAN-010)."""

from __future__ import annotations

import re

from engramory.core.parsers.base import (
    BaseParser,
    DocType,
    ParseResult,
    Section,
)


class TextParser(BaseParser):
    """Parser for unstructured plain text documents."""

    supported_extensions = ["txt", "text", "log"]
    doc_type = DocType.TEXT

    def __init__(
        self,
        strategy: str = "paragraph",
        chunk_words: int = 300,
        min_section_length: int = 20,
    ) -> None:
        self.strategy = strategy
        self.chunk_words = chunk_words
        self.min_section_length = min_section_length

    def parse(self, content: str | bytes, filename: str | None = None) -> ParseResult:
        """Parse text content into sections."""
        if isinstance(content, bytes):
            text = content.decode("utf-8", errors="replace")
        else:
            text = content

        text = text.replace("\r\n", "\n").replace("\r", "\n")

        if self.strategy == "chunk":
            filtered = self._parse_by_chunks(text)
        else:
            raw_sections = self._parse_by_paragraphs(text)
            filtered = [s for s in raw_sections if len(s.content) >= self.min_section_length]

        if not filtered and text.strip():
            filtered = [
                Section(
                    section_id="sec_1",
                    name="Content",
                    order=1,
                    level=1,
                    content=text.strip(),
                    citation="§content",
                    char_start=0,
                    char_end=len(text),
                    source_tag="raw",
                )
            ]

        # Re-number and assign breadcrumb citations
        for i, s in enumerate(filtered):
            s.order = i + 1
            s.section_id = self._generate_section_id(i + 1)
            s.citation = f"§part-{i + 1}"

        title = filename or (filtered[0].name if filtered else "Text Document")
        return ParseResult(
            sections=filtered,
            doc_type=self.doc_type,
            title=title,
        )

    def _parse_by_paragraphs(self, text: str) -> list[Section]:
        paragraphs = re.split(r"\n\s*\n+", text)
        sections: list[Section] = []
        cur_pos = 0

        for i, p in enumerate(paragraphs):
            p_clean = p.strip()
            if not p_clean:
                continue

            start_idx = text.find(p, cur_pos)
            if start_idx == -1:
                start_idx = cur_pos
            end_idx = start_idx + len(p)
            cur_pos = end_idx

            first_line = p_clean.split("\n")[0][:60].strip()
            name = (
                f"Paragraph {i + 1}: {first_line}" if len(first_line) > 10 else f"Paragraph {i + 1}"
            )

            sections.append(
                Section(
                    section_id=self._generate_section_id(len(sections) + 1),
                    name=name,
                    order=len(sections) + 1,
                    level=1,
                    content=p_clean,
                    char_start=start_idx,
                    char_end=end_idx,
                    source_tag="paragraph",
                )
            )

        return sections

    def _parse_by_chunks(self, text: str) -> list[Section]:
        words = text.split()
        sections: list[Section] = []
        cur_word_idx = 0

        while cur_word_idx < len(words):
            chunk_slice = words[cur_word_idx : cur_word_idx + self.chunk_words]
            chunk_text = " ".join(chunk_slice)
            part_num = len(sections) + 1

            sections.append(
                Section(
                    section_id=self._generate_section_id(part_num),
                    name=f"Part {part_num}",
                    order=part_num,
                    level=1,
                    content=chunk_text,
                    char_start=0,
                    char_end=len(chunk_text),
                    source_tag="chunk",
                )
            )
            cur_word_idx += self.chunk_words

        return sections
