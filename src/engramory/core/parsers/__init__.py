"""Universal document parsers and section normalization (PLAN-010 Phase 1)."""

from engramory.core.parsers.base import (
    BaseParser,
    DocType,
    ParseResult,
    Section,
    slugify,
)
from engramory.core.parsers.detector import DocumentDetector
from engramory.core.parsers.html import HTMLParser
from engramory.core.parsers.markdown import MarkdownParser
from engramory.core.parsers.pdf import PDFParser
from engramory.core.parsers.template import TemplateParser
from engramory.core.parsers.text import TextParser

__all__ = [
    "BaseParser",
    "DocType",
    "DocumentDetector",
    "HTMLParser",
    "MarkdownParser",
    "PDFParser",
    "ParseResult",
    "Section",
    "TemplateParser",
    "TextParser",
    "slugify",
]
