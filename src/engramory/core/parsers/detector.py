"""Document type detector and universal parser dispatcher (PLAN-010)."""

from __future__ import annotations

from engramory.core.parsers.base import (
    BaseParser,
    DocType,
    ParseResult,
)
from engramory.core.parsers.html import HTMLParser
from engramory.core.parsers.markdown import MarkdownParser
from engramory.core.parsers.pdf import PDFParser
from engramory.core.parsers.template import TemplateParser
from engramory.core.parsers.text import TextParser


class DocumentDetector:
    """Detects document format from filename, content patterns, and routes to appropriate parser."""

    EXTENSION_MAP: dict[str, DocType] = {
        # Markdown
        "md": DocType.MARKDOWN,
        "markdown": DocType.MARKDOWN,
        "mdown": DocType.MARKDOWN,
        "mkd": DocType.MARKDOWN,
        # HTML
        "html": DocType.HTML,
        "htm": DocType.HTML,
        "xhtml": DocType.HTML,
        # PDF
        "pdf": DocType.PDF,
        # Text
        "txt": DocType.TEXT,
        "text": DocType.TEXT,
        "log": DocType.TEXT,
        # Template
        "template": DocType.TEMPLATE,
        "tmpl": DocType.TEMPLATE,
    }

    CONTENT_PATTERNS: dict[DocType, list[bytes]] = {
        DocType.TEMPLATE: [
            b"<!-- @section:",
            b"<!--@section:",
            b"# @section:",
            b"@section:",
            b"--- section:",
        ],
        DocType.PDF: [
            b"%PDF-",
        ],
        DocType.HTML: [
            b"<!DOCTYPE html",
            b"<html",
            b"<head>",
            b"<body>",
        ],
        DocType.MARKDOWN: [
            b"# ",
            b"## ",
            b"### ",
            b"```",
        ],
    }

    def __init__(self) -> None:
        self.parsers: dict[DocType, BaseParser] = {
            DocType.MARKDOWN: MarkdownParser(),
            DocType.HTML: HTMLParser(),
            DocType.TEMPLATE: TemplateParser(),
            DocType.TEXT: TextParser(),
            DocType.PDF: PDFParser(),
        }

    def detect(self, content: str | bytes, filename: str | None = None) -> DocType:
        """Detect document format from content and optional filename."""
        if isinstance(content, str):
            content_bytes = content.encode("utf-8", errors="ignore")
        else:
            content_bytes = content

        # 1. Explicit template markers have highest priority
        for pattern in self.CONTENT_PATTERNS[DocType.TEMPLATE]:
            if pattern in content_bytes:
                return DocType.TEMPLATE

        # 2. Binary PDF check
        if content_bytes.startswith(b"%PDF-") or (filename and filename.lower().endswith(".pdf")):
            return DocType.PDF

        # 3. Filename extension mapping
        if filename and "." in filename:
            ext = filename.lower().split(".")[-1]
            if ext in self.EXTENSION_MAP:
                return self.EXTENSION_MAP[ext]

        # 4. Content sniffing
        for doc_type in [DocType.HTML, DocType.MARKDOWN]:
            for pattern in self.CONTENT_PATTERNS[doc_type]:
                if pattern in content_bytes:
                    return doc_type

        # 5. Default fallback to plain text
        return DocType.TEXT

    def get_parser(self, doc_type: DocType) -> BaseParser:
        """Return parser instance for given DocType."""
        return self.parsers.get(doc_type, self.parsers[DocType.TEXT])

    def parse(self, content: str | bytes, filename: str | None = None) -> ParseResult:
        """Auto-detect format and parse content into structured sections."""
        doc_type = self.detect(content, filename)
        parser = self.get_parser(doc_type)
        return parser.parse(content, filename=filename)
