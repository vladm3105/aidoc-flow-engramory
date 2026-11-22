"""Universal document extraction using IBM Docling with content-hash caching (PLAN-010 Phase 2)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from engramory.core.extraction.types import DoclingExtractionResult, FileCategory
from engramory.core.models import ValidationError

logger = logging.getLogger(__name__)

MAX_DOCLING_FILE_BYTES: int = 100 * 1024 * 1024  # 100 MiB guard for Docling extraction
MAX_ZIP_ENTRIES: int = 10_000
MAX_ZIP_UNCOMPRESSED_BYTES: int = 250 * 1024 * 1024
MAX_ZIP_RATIO: float = 100.0
_ZIP_CONTAINER_EXTENSIONS = frozenset({"docx", "xlsx", "pptx", "odt"})


def _check_zip_archive_safety(file_path: Path) -> None:
    """Reject ZIP-container office files that violate entry, size, or compression ratio bounds."""
    ext = file_path.suffix.lstrip(".").lower()
    if ext not in _ZIP_CONTAINER_EXTENSIONS or not zipfile.is_zipfile(file_path):
        return
    total_uncompressed = 0
    total_compressed = 0
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise ValidationError(
                    f"ZIP archive entry count {len(infos)} exceeds "
                    f"MAX_ZIP_ENTRIES ({MAX_ZIP_ENTRIES})"
                )
            for info in infos:
                total_uncompressed += max(0, int(info.file_size))
                total_compressed += max(0, int(info.compress_size))
                if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
                    raise ValidationError(
                        f"ZIP uncompressed size {total_uncompressed} exceeds "
                        f"MAX_ZIP_UNCOMPRESSED_BYTES ({MAX_ZIP_UNCOMPRESSED_BYTES})"
                    )
            ratio = float(total_uncompressed) / float(max(total_compressed, 1))
            if total_uncompressed > 0 and ratio > MAX_ZIP_RATIO:
                raise ValidationError(
                    f"ZIP compression ratio {ratio:.1f}x exceeds MAX_ZIP_RATIO ({MAX_ZIP_RATIO}x)"
                )
    except zipfile.BadZipFile as exc:
        raise ValidationError(
            f"Corrupt ZIP-based document archive '{file_path.name}': {exc}"
        ) from exc


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

    def extract(self, file_path: Path, *, tenant_id: str | None = None) -> DoclingExtractionResult:
        """Extract text and tables from document file using Docling."""
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        file_size = file_path.stat().st_size
        if file_size > MAX_DOCLING_FILE_BYTES:
            raise ValidationError(
                f"Document size {file_size} exceeds "
                f"MAX_DOCLING_FILE_BYTES ({MAX_DOCLING_FILE_BYTES})"
            )
        _check_zip_archive_safety(file_path)
        with file_path.open("rb") as fh:
            file_hash = hashlib.file_digest(fh, "sha256").hexdigest()
        tenant_hash = hashlib.sha256((tenant_id or "default").encode("utf-8")).hexdigest()[:16]
        cache_key = f"{tenant_hash}_{file_hash}"
        category = self.get_file_category(file_path.name)

        # 1. Check tenant-isolated cache
        if self.enable_cache:
            cached = self._read_cache(cache_key)
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
            self._write_cache(cache_key, result)

        return result

    def _read_cache(self, cache_key: str) -> DoclingExtractionResult | None:
        cache_file = self.cache_dir / f"{cache_key}.json"
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

    def _write_cache(self, cache_key: str, result: DoclingExtractionResult) -> None:
        cache_file = self.cache_dir / f"{cache_key}.json"
        tmp_path: str | None = None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=self.cache_dir,
                prefix=f".{cache_key}.",
                suffix=".tmp",
                mode="w",
                encoding="utf-8",
                delete=False,
            ) as tf:
                tmp_path = tf.name
                json.dump(result.to_dict(), tf)
                tf.flush()
                os.fsync(tf.fileno())
            os.replace(tmp_path, cache_file)
            tmp_path = None
        except Exception as e:
            logger.warning("Failed to write extraction cache for %s: %s", cache_key, e)
        finally:
            if tmp_path is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
