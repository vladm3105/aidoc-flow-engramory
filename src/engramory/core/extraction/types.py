"""Types and data structures for document extraction and enrichment (PLAN-010 Phase 2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from engramory.core.parsers.base import Section


class FileCategory(StrEnum):
    """Document file category classifications."""

    DOCUMENT = "document"
    SPREADSHEET = "spreadsheet"
    PRESENTATION = "presentation"
    PDF = "pdf"
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    UNKNOWN = "unknown"


@dataclass
class DoclingExtractionResult:
    """Result from IBM Docling document extraction."""

    text: str
    tables: list[dict[str, Any]] = field(default_factory=list)
    images: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    file_category: FileCategory = FileCategory.UNKNOWN
    extraction_time_ms: int = 0
    cache_hit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "tables": self.tables,
            "images": self.images,
            "metadata": dict(self.metadata),
            "file_category": str(self.file_category),
            "extraction_time_ms": self.extraction_time_ms,
            "cache_hit": self.cache_hit,
        }


@dataclass
class ExtractionResult:
    """Consolidated result from SmartExtractionRouter."""

    sections: list[Section]
    title: str | None = None
    file_category: FileCategory = FileCategory.UNKNOWN
    metadata: dict[str, Any] = field(default_factory=dict)
    extractor_used: str = "native"
    tables: list[dict[str, Any]] = field(default_factory=list)
    cached: bool = False
    duration_ms: int = 0
    warnings: list[str] = field(default_factory=list)
