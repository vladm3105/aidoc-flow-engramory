"""Universal document extraction and smart routing (PLAN-010 Phase 2)."""

from engramory.core.extraction.docling import DoclingExtractor
from engramory.core.extraction.enrichment import SemanticChunker
from engramory.core.extraction.router import SmartExtractionRouter
from engramory.core.extraction.types import (
    DoclingExtractionResult,
    ExtractionResult,
    FileCategory,
)

__all__ = [
    "DoclingExtractionResult",
    "DoclingExtractor",
    "ExtractionResult",
    "FileCategory",
    "SemanticChunker",
    "SmartExtractionRouter",
]
