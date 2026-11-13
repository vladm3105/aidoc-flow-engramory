"""Universal document extraction using IBM Docling with content-hash caching (PLAN-010 Phase 2)."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from engramory.core.extraction.types import DoclingExtractionResult, FileCategory
from engramory.core.models import ValidationError

logger = logging.getLogger(__name__)

MAX_DOCLING_FILE_BYTES: int = 100 * 1024 * 1024  # 100 MiB guard for Docling extraction

# Dynamic import guard for IBM Docling
try:
    from docling.document_converter import DocumentConverter

    DOCLING_AVAILABLE = True
except ImportError:
    DOCLING_AVAILABLE = False
    DocumentConverter = None  # type: ignore[assignment, misc]  # reason: optional dependency fallback


class DoclingExtractor:
    """Extract text and tables from complex documents using IBM Docling with SHA256 caching."""

    EXTENSION_CATEGORIES: dict[str, FileCategory] = {
        "pdf": FileCategory.PDF,
        "docx": FileCategory.DOCUMENT,
        "doc": FileCategory.DOCUMENT,
        "odt": FileCategory.DOCUMENT,
        "rtf": FileCategory.DOCUMENT,
        "xlsx": FileCategory.SPREADSHEET,
        "xls": FileCategory.SPREADSHEET,
        "csv": FileCategory.SPREADSHEET,
        "pptx": FileCategory.PRESENTATION,
        "ppt": FileCategory.PRESENTATION,
        "png": FileCategory.IMAGE,
        "jpg": FileCategory.IMAGE,
        "jpeg": FileCategory.IMAGE,
        "webp": FileCategory.IMAGE,
        "tiff": FileCategory.IMAGE,
    }

    def __init__(
        self,
        cache_dir: Path | None = None,
        enable_cache: bool = True,
    ) -> None:
        self.enable_cache = enable_cache
        self.cache_dir = cache_dir or (Path.home() / ".cache" / "engramory" / "extraction")
        if self.enable_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._converter = DocumentConverter() if DOCLING_AVAILABLE else None

    @classmethod
    def is_available(cls) -> bool:
        """Return True if docling is installed and available."""
        return DOCLING_AVAILABLE

    def get_file_category(self, filename: str) -> FileCategory:
        """Classify file extension into FileCategory."""
        ext = filename.lower().split(".")[-1] if "." in filename else ""
        return self.EXTENSION_CATEGORIES.get(ext, FileCategory.UNKNOWN)

    def extract(self, file_path: Path) -> DoclingExtractionResult:
        """Extract text and tables from document file using Docling."""
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        file_size = file_path.stat().st_size
        if file_size > MAX_DOCLING_FILE_BYTES:
            raise ValidationError(
                f"Document size {file_size} exceeds "
                f"MAX_DOCLING_FILE_BYTES ({MAX_DOCLING_FILE_BYTES})"
            )
        with file_path.open("rb") as fh:
            file_hash = hashlib.file_digest(fh, "sha256").hexdigest()
        category = self.get_file_category(file_path.name)

        # 1. Check cache
        if self.enable_cache:
            cached = self._read_cache(file_hash)
            if cached is not None:
                cached.cache_hit = True
                return cached

        # 2. Check Docling availability
        if not DOCLING_AVAILABLE or self._converter is None:
            raise ImportError(
                "IBM Docling is not installed. Install with: pip install 'engramory[extraction]' "
                "or install docling package directly."
            )

        start_t = time.perf_counter()
        conv_result = self._converter.convert(str(file_path))
        doc = conv_result.document

        # Export to markdown representation
        text_content = doc.export_to_markdown()

        # Extract tables
        tables: list[dict[str, Any]] = []
        if hasattr(doc, "tables") and doc.tables:
            for idx, tbl in enumerate(doc.tables):
                table_md = tbl.export_to_markdown() if hasattr(tbl, "export_to_markdown") else ""
                tables.append({"index": idx, "markdown": table_md})

        # Extract embedded figures / pictures (GAP-04)
        images: list[dict[str, Any]] = []
        if hasattr(doc, "pictures") and doc.pictures:
            for idx, pic in enumerate(doc.pictures):
                caption = ""
                if hasattr(pic, "caption_text"):
                    cap_attr = pic.caption_text
                    caption = str(cap_attr(doc) if callable(cap_attr) else cap_attr or "")
                elif hasattr(pic, "text") and pic.text:
                    caption = str(pic.text)
                page_no = None
                if hasattr(pic, "prov") and pic.prov:
                    first_prov = pic.prov[0]
                    page_no = getattr(first_prov, "page_no", None)
                images.append(
                    {
                        "index": idx,
                        "caption": caption,
                        "page_no": page_no,
                    }
                )

        duration_ms = int((time.perf_counter() - start_t) * 1000)

        result = DoclingExtractionResult(
            text=text_content,
            tables=tables,
            images=images,
            metadata={"source_file": file_path.name, "file_size": file_size, "sha256": file_hash},
            file_category=category,
            extraction_time_ms=duration_ms,
            cache_hit=False,
        )

        # 3. Write cache
        if self.enable_cache:
            self._write_cache(file_hash, result)

        return result

    def _read_cache(self, file_hash: str) -> DoclingExtractionResult | None:
        cache_file = self.cache_dir / f"{file_hash}.json"
        if not cache_file.exists():
            return None
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            category_str = data.get("file_category", "unknown")
            try:
                category = FileCategory(category_str)
            except ValueError:
                category = FileCategory.UNKNOWN

            return DoclingExtractionResult(
                text=data.get("text", ""),
                tables=data.get("tables", []),
                images=data.get("images", []),
                metadata=data.get("metadata", {}),
                file_category=category,
                extraction_time_ms=data.get("extraction_time_ms", 0),
                cache_hit=True,
            )
        except Exception:
            return None

    def _write_cache(self, file_hash: str, result: DoclingExtractionResult) -> None:
        cache_file = self.cache_dir / f"{file_hash}.json"
        try:
            cache_file.write_text(json.dumps(result.to_dict()), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to write extraction cache for %s: %s", file_hash, e)
