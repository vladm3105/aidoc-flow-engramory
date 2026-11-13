"""Smart extraction router coordinating Docling and native parsers (PLAN-010 Phase 2)."""

from __future__ import annotations

import time
from pathlib import Path

from engramory.core.extraction.docling import DoclingExtractor
from engramory.core.extraction.enrichment import SemanticChunker
from engramory.core.extraction.multimodal import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    ImageExtractor,
    VideoAudioExtractor,
    infer_media_mime_type,
)
from engramory.core.extraction.types import ExtractionResult, FileCategory
from engramory.core.parsers.detector import DocumentDetector
from engramory.core.parsers.markdown import MarkdownParser

MAX_NATIVE_TEXT_BYTES: int = 10 * 1024 * 1024  # 10 MiB guard for native text decoding


class SmartExtractionRouter:
    """Smart router dispatching binary documents to Docling and text to native parsers."""

    DOCLING_EXTENSIONS = {
        "pdf",
        "docx",
        "doc",
        "xlsx",
        "xls",
        "pptx",
        "ppt",
        "png",
        "jpg",
        "jpeg",
        "webp",
        "tiff",
    }

    def __init__(
        self,
        enable_docling: bool = True,
        cache_dir: Path | None = None,
        min_words_per_chunk: int = 30,
        max_words_per_chunk: int = 400,
    ) -> None:
        self.enable_docling = enable_docling
        self.detector = DocumentDetector()
        self.markdown_parser = MarkdownParser(max_words_per_chunk=max_words_per_chunk)
        self.chunker = SemanticChunker(min_words=min_words_per_chunk, max_words=max_words_per_chunk)
        self.docling = DoclingExtractor(cache_dir=cache_dir) if enable_docling else None
        self.image_extractor = ImageExtractor()
        self.video_audio_extractor = VideoAudioExtractor()

    def extract_file(
        self,
        file_path: Path,
        prefer_docling: bool = True,
        refine_chunks: bool = True,
    ) -> ExtractionResult:
        """Extract sections and structured content from a disk file."""
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        start_t = time.perf_counter()
        ext = file_path.suffix.lstrip(".").lower()
        filename = file_path.name
        warnings: list[str] = []

        # Inspect magic header (64 bytes) in O(1) memory to detect binary media
        header = b""
        if not file_path.is_symlink():
            try:
                with file_path.open("rb") as fh:
                    header = fh.read(64)
            except OSError:
                header = b""
        inferred_modality, _ = infer_media_mime_type(file_path, header)

        # 0a. Audio / Video streaming extraction (never calls read_bytes() or refine_sections())
        if ext in (AUDIO_EXTENSIONS | VIDEO_EXTENSIONS) or inferred_modality in ("audio", "video"):
            return self.video_audio_extractor.extract(file_path)

        # 0b. Image extraction (pure-stdlib header + optional Docling OCR)
        if ext in IMAGE_EXTENSIONS or inferred_modality == "image":
            ocr_text: str | None = None
            if (
                prefer_docling
                and self.enable_docling
                and self.docling is not None
                and ext in self.DOCLING_EXTENSIONS
                and self.docling.is_available()
            ):
                try:
                    doc_res = self.docling.extract(file_path)
                    ocr_text = doc_res.text
                except Exception as exc:
                    warnings.append(f"Docling image OCR failed, using header extractor: {exc}")
            img_res = self.image_extractor.extract(file_path, ocr_text=ocr_text)
            if warnings:
                img_res.warnings.extend(warnings)
            return img_res

        # 1. Try Docling for supported binary/complex documents
        if (
            prefer_docling
            and self.enable_docling
            and self.docling is not None
            and ext in self.DOCLING_EXTENSIONS
            and self.docling.is_available()
        ):
            try:
                doc_res = self.docling.extract(file_path)
                # Parse Docling markdown output into sections
                md_parse = self.markdown_parser.parse(doc_res.text, filename=filename)
                sections = md_parse.sections
                if refine_chunks:
                    sections = self.chunker.refine_sections(sections)

                duration_ms = int((time.perf_counter() - start_t) * 1000)
                return ExtractionResult(
                    sections=sections,
                    title=md_parse.title or filename,
                    file_category=doc_res.file_category,
                    metadata={
                        **md_parse.metadata,
                        **doc_res.metadata,
                        "images": doc_res.images,
                    },
                    extractor_used="docling",
                    tables=doc_res.tables,
                    cached=doc_res.cache_hit,
                    duration_ms=duration_ms,
                    warnings=warnings,
                )
            except Exception as e:
                warnings.append(f"Docling extraction failed, falling back to native: {e}")

        # 2. Native parser fallback (bounded to MAX_NATIVE_TEXT_BYTES)
        file_size = file_path.stat().st_size
        content: str | bytes
        if file_size > MAX_NATIVE_TEXT_BYTES:
            with file_path.open("rb") as fh:
                content = fh.read(65536)
        else:
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                with file_path.open("rb") as fh:
                    content = fh.read(65536)

        native_res = self.detector.parse(content, filename=filename)
        sections = native_res.sections
        if refine_chunks and all(
            (s.metadata or {}).get("modality") not in {"image", "audio", "video"} for s in sections
        ):
            sections = self.chunker.refine_sections(sections)

        duration_ms = int((time.perf_counter() - start_t) * 1000)
        return ExtractionResult(
            sections=sections,
            title=native_res.title or filename,
            file_category=FileCategory.TEXT,
            metadata=native_res.metadata,
            extractor_used="native",
            tables=[],
            cached=False,
            duration_ms=duration_ms,
            warnings=warnings + native_res.warnings,
        )

    def extract_content(
        self,
        content: str | bytes,
        filename: str | None = None,
        refine_chunks: bool = True,
    ) -> ExtractionResult:
        """Extract sections directly from in-memory content using native parsers."""
        start_t = time.perf_counter()
        native_res = self.detector.parse(content, filename=filename)
        sections = native_res.sections
        if refine_chunks:
            sections = self.chunker.refine_sections(sections)

        duration_ms = int((time.perf_counter() - start_t) * 1000)
        return ExtractionResult(
            sections=sections,
            title=native_res.title or filename,
            file_category=FileCategory.TEXT,
            metadata=native_res.metadata,
            extractor_used="native",
            tables=[],
            cached=False,
            duration_ms=duration_ms,
            warnings=native_res.warnings,
        )

    extract_document = extract_file
