"""PDF document parser with page-level sectioning and graceful fallbacks (PLAN-010)."""

from __future__ import annotations

import io
from typing import Any

from engramory.core.parsers.base import (
    BaseParser,
    DocType,
    ParseResult,
    Section,
)

# Optional PDF libraries check
try:
    import pypdf

    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

try:
    import fitz  # PyMuPDF

    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False


class PDFParser(BaseParser):
    """Parser for PDF documents extracting page-level and structural sections."""

    supported_extensions = ["pdf"]
    doc_type = DocType.PDF

    def __init__(self, min_page_words: int = 10) -> None:
        self.min_page_words = min_page_words

    def parse(self, content: str | bytes, filename: str | None = None) -> ParseResult:
        """Parse PDF content into page-bounded sections."""
        if isinstance(content, str):
            pdf_bytes = content.encode("latin-1", errors="replace")
        else:
            pdf_bytes = content

        if HAS_PYPDF:
            return self._parse_with_pypdf(pdf_bytes, filename)
        if HAS_PYMUPDF:
            return self._parse_with_pymupdf(pdf_bytes, filename)

        return ParseResult(
            sections=[],
            doc_type=self.doc_type,
            title=filename,
            warnings=[
                "PDF parsing libraries (pypdf or pymupdf) not installed. "
                "Install pypdf to enable PDF document extraction."
            ],
        )

    def _parse_with_pypdf(self, pdf_bytes: bytes, filename: str | None) -> ParseResult:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        sections: list[Section] = []
        doc_metadata: dict[str, Any] = {}

        if reader.metadata:
            for k, v in reader.metadata.items():
                if v and isinstance(v, str):
                    clean_k = k.lstrip("/").lower()
                    doc_metadata[clean_k] = v

        title = doc_metadata.get("title") or filename or "PDF Document"

        for page_idx, page in enumerate(reader.pages):
            page_num = page_idx + 1
            page_text = page.extract_text() or ""
            page_clean = page_text.strip()
            if len(page_clean.split()) < self.min_page_words:
                continue

            order = len(sections) + 1
            sec = Section(
                section_id=self._generate_section_id(order),
                name=f"Page {page_num}",
                order=order,
                level=1,
                content=page_clean,
                citation=f"§page-{page_num}",
                page_start=page_num,
                page_end=page_num,
                source_tag="pdf_page",
                metadata={"page_number": page_num},
            )
            sections.append(sec)

        return ParseResult(
            sections=sections,
            doc_type=self.doc_type,
            title=title,
            metadata=doc_metadata,
            warnings=["No text extracted from PDF pages"] if not sections else [],
        )

    def _parse_with_pymupdf(self, pdf_bytes: bytes, filename: str | None) -> ParseResult:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        sections: list[Section] = []
        doc_metadata: dict[str, Any] = doc.metadata or {}
        title = doc_metadata.get("title") or filename or "PDF Document"

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            page_num = page_idx + 1
            page_text = page.get_text() or ""
            page_clean = page_text.strip()
            if len(page_clean.split()) < self.min_page_words:
                continue

            order = len(sections) + 1
            sec = Section(
                section_id=self._generate_section_id(order),
                name=f"Page {page_num}",
                order=order,
                level=1,
                content=page_clean,
                citation=f"§page-{page_num}",
                page_start=page_num,
                page_end=page_num,
                source_tag="pdf_page",
                metadata={"page_number": page_num},
            )
            sections.append(sec)

        return ParseResult(
            sections=sections,
            doc_type=self.doc_type,
            title=title,
            metadata=doc_metadata,
            warnings=["No text extracted from PDF pages"] if not sections else [],
        )
