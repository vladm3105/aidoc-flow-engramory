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


MAX_PDF_PAGES: int = 500
MAX_PDF_TOTAL_CHARS: int = 5_000_000


class PDFParser(BaseParser):
    """Parser for PDF documents extracting page-level and structural sections."""

    supported_extensions = ["pdf"]
    doc_type = DocType.PDF

    def __init__(
        self,
        min_page_words: int = 10,
        *,
        max_pages: int = MAX_PDF_PAGES,
        max_total_chars: int = MAX_PDF_TOTAL_CHARS,
    ) -> None:
        self.min_page_words = min_page_words
        self.max_pages = max_pages
        self.max_total_chars = max_total_chars

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
        try:
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        except Exception as exc:  # noqa: BLE001  # reason: malformed PDF bytes must degrade to empty ParseResult warning
            return ParseResult(
                sections=[],
                doc_type=self.doc_type,
                title=filename or "PDF Document",
                warnings=[f"Failed to parse PDF via pypdf: {exc}"],
            )
        sections: list[Section] = []
        warnings: list[str] = []
        doc_metadata: dict[str, Any] = {}

        try:
            if reader.metadata:
                for k, v in reader.metadata.items():
                    if v and isinstance(v, str):
                        clean_k = k.lstrip("/").lower()
                        doc_metadata[clean_k] = v
        except Exception:  # noqa: BLE001  # reason: malformed PDF metadata must not abort page extraction
            doc_metadata = {}

        title = doc_metadata.get("title") or filename or "PDF Document"
        total_chars = 0

        for page_idx, page in enumerate(reader.pages):
            if page_idx >= self.max_pages:
                warnings.append(f"PDF truncated at max_pages={self.max_pages}")
                break
            page_num = page_idx + 1
            try:
                page_text = page.extract_text() or ""
            except Exception as exc:  # noqa: BLE001  # reason: skip corrupt individual PDF page cleanly
                warnings.append(f"Skipped unreadable PDF page {page_num}: {exc}")
                continue
            page_clean = page_text.strip()
            if len(page_clean.split()) < self.min_page_words:
                continue
            if total_chars + len(page_clean) > self.max_total_chars:
                warnings.append(f"PDF truncated at max_total_chars={self.max_total_chars}")
                break
            total_chars += len(page_clean)

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

        if not sections:
            warnings.append("No text extracted from PDF pages")
        return ParseResult(
            sections=sections,
            doc_type=self.doc_type,
            title=title,
            metadata=doc_metadata,
            warnings=warnings,
        )

    def _parse_with_pymupdf(self, pdf_bytes: bytes, filename: str | None) -> ParseResult:
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:  # noqa: BLE001  # reason: malformed PDF bytes must degrade to empty ParseResult warning
            return ParseResult(
                sections=[],
                doc_type=self.doc_type,
                title=filename or "PDF Document",
                warnings=[f"Failed to parse PDF via PyMuPDF: {exc}"],
            )
        sections: list[Section] = []
        warnings: list[str] = []
        doc_metadata: dict[str, Any] = {}
        title = filename or "PDF Document"
        total_chars = 0
        try:
            doc_metadata = doc.metadata or {}
            title = doc_metadata.get("title") or filename or "PDF Document"
            page_count = len(doc)
            for page_idx in range(page_count):
                if page_idx >= self.max_pages:
                    warnings.append(f"PDF truncated at max_pages={self.max_pages}")
                    break
                page = doc[page_idx]
                page_num = page_idx + 1
                page_text = page.get_text() or ""
                page_clean = page_text.strip()
                if len(page_clean.split()) < self.min_page_words:
                    continue
                if total_chars + len(page_clean) > self.max_total_chars:
                    warnings.append(f"PDF truncated at max_total_chars={self.max_total_chars}")
                    break
                total_chars += len(page_clean)

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
        except Exception as exc:  # noqa: BLE001  # reason: malformed PDF page stream must not crash parser
            warnings.append(f"PDF extraction error: {exc}")
        finally:
            doc.close()

        if not sections:
            warnings.append("No text extracted from PDF pages")
        return ParseResult(
            sections=sections,
            doc_type=self.doc_type,
            title=title,
            metadata=doc_metadata,
            warnings=warnings,
        )
