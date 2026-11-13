"""Document type detector and universal parser dispatcher (PLAN-010 & PLAN-023)."""

from __future__ import annotations

from engramory.core.parsers.base import (
    BaseParser,
    DocType,
    ParseResult,
    Section,
)
from engramory.core.parsers.html import HTMLParser
from engramory.core.parsers.markdown import MarkdownParser
from engramory.core.parsers.pdf import PDFParser
from engramory.core.parsers.template import TemplateParser
from engramory.core.parsers.text import TextParser


class _MediaPlaceholderParser(BaseParser):
    """Safe fallback parser for binary image/audio/video content so binary payloads
    never fall through to TextParser UTF-8 replacement-character decoding."""

    def __init__(self, doc_type: DocType) -> None:
        self.doc_type = doc_type
        self.supported_extensions = []

    def parse(self, content: str | bytes, filename: str | None = None) -> ParseResult:
        raw_len = (
            len(content)
            if isinstance(content, (bytes, bytearray))
            else len(content.encode("utf-8"))
        )
        name = filename or f"media.{self.doc_type.value}"
        modality = self.doc_type.value
        anchor = "#image" if modality == "image" else "#t=00:00:00-00:01:00"
        sec = Section(
            section_id="sec_1",
            name=name,
            order=1,
            level=1,
            content=f"# {name}\n\nModality: `{modality}` ({raw_len} bytes).",
            citation=anchor,
            metadata={"modality": modality, "byte_size": raw_len},
        )
        return ParseResult(
            sections=[sec],
            doc_type=self.doc_type,
            title=name,
            metadata={"modality": modality, "byte_size": raw_len},
        )


def _detect_binary_media_magic(header: bytes) -> DocType | None:
    """Inspect magic bytes in the first 32 bytes to classify image, audio, or video."""
    if not header:
        return None
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return DocType.IMAGE
    if header.startswith(b"\xff\xd8\xff"):
        return DocType.IMAGE
    if header.startswith((b"GIF87a", b"GIF89a")):
        return DocType.IMAGE
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return DocType.IMAGE
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return DocType.AUDIO
    if header.startswith(b"ID3") or (
        len(header) >= 2 and header[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    ):
        return DocType.AUDIO
    if header.startswith(b"fLaC") or header.startswith(b"OggS"):
        return DocType.AUDIO
    # ISO Base Media File Format (MP4, MOV, M4V, M4A) has 'ftyp' at bytes 4..8
    if len(header) >= 12 and header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand in (b"M4A ", b"M4B "):
            return DocType.AUDIO
        return DocType.VIDEO
    # Matroska / WebM EBML magic header
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return DocType.VIDEO
    return None


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
        # Image
        "png": DocType.IMAGE,
        "jpg": DocType.IMAGE,
        "jpeg": DocType.IMAGE,
        "webp": DocType.IMAGE,
        "gif": DocType.IMAGE,
        "tiff": DocType.IMAGE,
        "bmp": DocType.IMAGE,
        # Audio
        "wav": DocType.AUDIO,
        "mp3": DocType.AUDIO,
        "m4a": DocType.AUDIO,
        "flac": DocType.AUDIO,
        "ogg": DocType.AUDIO,
        # Video
        "mp4": DocType.VIDEO,
        "mov": DocType.VIDEO,
        "webm": DocType.VIDEO,
        "mkv": DocType.VIDEO,
        "avi": DocType.VIDEO,
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
            DocType.IMAGE: _MediaPlaceholderParser(DocType.IMAGE),
            DocType.AUDIO: _MediaPlaceholderParser(DocType.AUDIO),
            DocType.VIDEO: _MediaPlaceholderParser(DocType.VIDEO),
        }

    def detect(self, content: str | bytes, filename: str | None = None) -> DocType:
        """Detect document format from content and optional filename."""
        if isinstance(content, str):
            content_bytes = content.encode("utf-8", errors="ignore")
        else:
            content_bytes = content

        # 1. Binary PDF or media magic-byte check first (prevents binary masquerading)
        if content_bytes.startswith(b"%PDF-") or (filename and filename.lower().endswith(".pdf")):
            return DocType.PDF

        media_type = _detect_binary_media_magic(content_bytes[:32])
        if media_type is not None:
            return media_type

        # 2. Explicit template markers
        for pattern in self.CONTENT_PATTERNS[DocType.TEMPLATE]:
            if pattern in content_bytes:
                return DocType.TEMPLATE

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
