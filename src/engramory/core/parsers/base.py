"""Base classes and data structures for document parsers (PLAN-010 Phase 1)."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class DocType(StrEnum):
    """Supported document types."""

    MARKDOWN = "markdown"
    HTML = "html"
    PDF = "pdf"
    TEMPLATE = "template"
    TEXT = "text"
    UNKNOWN = "unknown"


def slugify(text: str) -> str:
    """Convert a title or heading into a clean URL/citation slug."""
    slug = re.sub(r"[^\w\-_]", "-", text.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug or "section"


@dataclass
class Section:
    """Uniform parsed document section."""

    section_id: str
    name: str
    order: int
    level: int
    content: str
    parent_id: str | None = None
    citation: str | None = None
    char_start: int = 0
    char_end: int = 0
    page_start: int | None = None
    page_end: int | None = None
    source_tag: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def content_length(self) -> int:
        """Length of section content in characters."""
        return len(self.content)

    def to_dict(self) -> dict[str, Any]:
        """Serialize section to dictionary."""
        return {
            "section_id": self.section_id,
            "name": self.name,
            "order": self.order,
            "level": self.level,
            "content": self.content,
            "content_length": self.content_length,
            "parent_id": self.parent_id,
            "citation": self.citation,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "source_tag": self.source_tag,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Section:
        """Hydrate section from dictionary."""
        d = dict(data)
        d.pop("content_length", None)
        return cls(**d)


@dataclass
class ParseResult:
    """Structured result of parsing a document."""

    sections: list[Section]
    doc_type: DocType
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def section_count(self) -> int:
        return len(self.sections)

    @property
    def section_names(self) -> list[str]:
        return [s.name for s in self.sections]

    def get_section_by_order(self, order: int) -> Section | None:
        """Lookup section by sequential order."""
        for s in self.sections:
            if s.order == order:
                return s
        return None

    def get_section_by_name(self, name: str, fuzzy: bool = True) -> Section | None:
        """Lookup section by title."""
        target = name.lower()
        for s in self.sections:
            if s.name.lower() == target:
                return s
        if fuzzy:
            for s in self.sections:
                if target in s.name.lower():
                    return s
        return None

    def get_section_by_citation(self, citation: str) -> Section | None:
        """Lookup section by citation slug."""
        for s in self.sections:
            if s.citation == citation:
                return s
        return None


class BaseParser(ABC):
    """Abstract base class for document parsers."""

    supported_extensions: list[str] = []
    doc_type: DocType = DocType.UNKNOWN

    @abstractmethod
    def parse(self, content: str | bytes, filename: str | None = None) -> ParseResult:
        """Parse document content into structured sections."""
        ...

    def parse_file(self, file_path: Path) -> ParseResult:
        """Read and parse a file from disk."""
        filename = file_path.name
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = file_path.read_bytes()  # type: ignore[assignment]
        return self.parse(content, filename=filename)

    def can_parse(self, filename: str) -> bool:
        """Check if parser handles given filename extension."""
        if not filename or "." not in filename:
            return False
        ext = filename.lower().split(".")[-1]
        return ext in self.supported_extensions

    def _generate_section_id(self, order: int) -> str:
        return f"sec_{order}"

    def _link_subsections(self, sections: list[Section]) -> None:
        """Establish parent_id links and hierarchical breadcrumb citations."""
        parent_stack: list[tuple[int, str, str]] = []  # (level, section_id, slug)
        seen_slugs: dict[str, int] = {}

        for section in sections:
            while parent_stack and parent_stack[-1][0] >= section.level:
                parent_stack.pop()

            cur_slug = slugify(section.name)
            if parent_stack:
                section.parent_id = parent_stack[-1][1]
                path_prefix = "/".join(s[2] for s in parent_stack)
                base_citation = f"§{path_prefix}/{cur_slug}"
            else:
                base_citation = f"§{cur_slug}"

            # Ensure uniqueness within document by indexing duplicate citations
            seen_count = seen_slugs.get(base_citation, 0) + 1
            seen_slugs[base_citation] = seen_count
            if seen_count > 1:
                section.citation = f"{base_citation}-{seen_count}"
            else:
                section.citation = base_citation

            parent_stack.append((section.level, section.section_id, cur_slug))

    def _extract_title(self, sections: list[Section]) -> str | None:
        """Extract title from highest-level section or first section."""
        for s in sections:
            if s.level == 1:
                return s.name
        return sections[0].name if sections else None
