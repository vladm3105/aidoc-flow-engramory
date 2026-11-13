"""Multimodal extraction for images, audio, and 20 GB+ video assets (PLAN-023 Phase 3).

Guarantees:
- Never calls ``Path.read_bytes()`` on media files; computes SHA-256 via
  ``hashlib.file_digest`` in constant ``O(1)`` Python heap memory.
- Locks down ``ffprobe`` and ``ffmpeg`` subprocess invocations with
  ``-protocol_whitelist file,pipe``, ``stdin=subprocess.DEVNULL``, ``-fs``
  output caps, ``--`` option terminators (where supported), and symlink /
  leading-dash path rejection.
- Emits anchor-only section citations (``#image``, ``#t=00:00:00-00:01:00``)
  compatible with ``format_local_citation`` and ``format_github_citation``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import struct
import subprocess  # nosec B404 # reason: hardened ffprobe/ffmpeg media inspection with protocol_whitelist and DEVNULL
import time
from pathlib import Path
from typing import Any

from engramory.core.extraction.types import ExtractionResult, FileCategory
from engramory.core.models import ValidationError
from engramory.core.parsers.base import Section

logger = logging.getLogger(__name__)

MAX_KEYFRAME_BYTES: int = 20 * 1024 * 1024  # 20 MiB cap per extracted keyframe
MAX_AUDIO_WINDOW_BYTES: int = 25 * 1024 * 1024  # 25 MiB cap per audio window
DEFAULT_SEGMENT_SECONDS: int = 60
MAX_DEFAULT_SEGMENTS: int = 120

IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "tif"}
)
AUDIO_EXTENSIONS: frozenset[str] = frozenset({"wav", "mp3", "m4a", "flac", "ogg", "aac"})
VIDEO_EXTENSIONS: frozenset[str] = frozenset({"mp4", "mov", "webm", "mkv", "avi", "m4v"})
MEDIA_EXTENSIONS: frozenset[str] = IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS

_EXT_TO_MIME: dict[str, tuple[str, str]] = {
    "png": ("image", "image/png"),
    "jpg": ("image", "image/jpeg"),
    "jpeg": ("image", "image/jpeg"),
    "webp": ("image", "image/webp"),
    "gif": ("image", "image/gif"),
    "bmp": ("image", "image/bmp"),
    "tiff": ("image", "image/tiff"),
    "tif": ("image", "image/tiff"),
    "wav": ("audio", "audio/wav"),
    "mp3": ("audio", "audio/mpeg"),
    "m4a": ("audio", "audio/mp4"),
    "flac": ("audio", "audio/flac"),
    "ogg": ("audio", "audio/ogg"),
    "aac": ("audio", "audio/aac"),
    "mp4": ("video", "video/mp4"),
    "mov": ("video", "video/quicktime"),
    "webm": ("video", "video/webm"),
    "mkv": ("video", "video/x-matroska"),
    "avi": ("video", "video/x-msvideo"),
    "m4v": ("video", "video/x-m4v"),
}


def format_hms(seconds: float) -> str:
    """Format non-negative seconds as ``HH:MM:SS``."""
    total = max(0, int(round(seconds)))
    hrs = total // 3600
    mins = (total % 3600) // 60
    secs = total % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


def infer_media_mime_type(file_path: Path, header: bytes = b"") -> tuple[str, str]:
    """Return ``(modality, mime_type)`` using magic bytes first, then file extension."""
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ("image", "image/png")
    if header.startswith(b"\xff\xd8\xff"):
        return ("image", "image/jpeg")
    if header.startswith((b"GIF87a", b"GIF89a")):
        return ("image", "image/gif")
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return ("image", "image/webp")
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return ("audio", "audio/wav")
    if header.startswith(b"ID3") or (len(header) >= 2 and header[:2] in (b"\xff\xfb", b"\xff\xf3")):
        return ("audio", "audio/mpeg")
    if header.startswith(b"fLaC"):
        return ("audio", "audio/flac")
    if header.startswith(b"OggS"):
        return ("audio", "audio/ogg")
    if len(header) >= 12 and header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand in (b"M4A ", b"M4B "):
            return ("audio", "audio/mp4")
        if brand == b"qt  ":
            return ("video", "video/quicktime")
        return ("video", "video/mp4")
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        ext = file_path.suffix.lstrip(".").lower()
        if ext == "webm":
            return ("video", "video/webm")
        return ("video", "video/x-matroska")

    ext = file_path.suffix.lstrip(".").lower()
    return _EXT_TO_MIME.get(ext, ("document", "application/octet-stream"))


def parse_image_dimensions(header: bytes, mime_type: str = "") -> tuple[int | None, int | None]:
    """Parse ``(width, height)`` from image header bytes using pure Python stdlib."""
    if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
        width, height = struct.unpack(">II", header[16:24])
        return int(width), int(height)

    if header.startswith((b"GIF87a", b"GIF89a")) and len(header) >= 10:
        width, height = struct.unpack("<HH", header[6:10])
        return int(width), int(height)

    if len(header) >= 30 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        chunk_type = header[12:16]
        if chunk_type == b"VP8X" and len(header) >= 30:
            w_minus_1 = int.from_bytes(header[24:27], "little")
            h_minus_1 = int.from_bytes(header[27:30], "little")
            return w_minus_1 + 1, h_minus_1 + 1
        if chunk_type == b"VP8 " and len(header) >= 30:
            width, height = struct.unpack("<HH", header[26:30])
            return int(width & 0x3FFF), int(height & 0x3FFF)
        if chunk_type == b"VP8L" and len(header) >= 25:
            bits = int.from_bytes(header[21:25], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
            return width, height

    if header.startswith(b"\xff\xd8"):
        idx = 2
        n = len(header)
        sof_markers = {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }
        while idx + 4 < n:
            if header[idx] != 0xFF:
                idx += 1
                continue
            while idx < n and header[idx] == 0xFF:
                idx += 1
            if idx >= n:
                break
            marker = header[idx]
            idx += 1
            if marker in (0xD8, 0xD9) or (0xD0 <= marker <= 0xD7):
                continue
            if idx + 2 > n:
                break
            seg_len = struct.unpack(">H", header[idx : idx + 2])[0]
            if seg_len < 2:
                break
            if marker in sof_markers and idx + 7 <= n:
                height, width = struct.unpack(">HH", header[idx + 3 : idx + 7])
                return int(width), int(height)
            idx += seg_len

    return None, None


def _validate_media_path(file_path: Path) -> Path:
    """Validate that a media path exists, is not a symlink, and does not start with ``-``."""
    raw_str = str(file_path)
    if raw_str.startswith("-") or file_path.name.startswith("-"):
        raise ValidationError(f"Media file path must not start with '-': {file_path}")
    if file_path.is_symlink():
        raise ValidationError(f"Symlink media paths are rejected: {file_path}")
    if not file_path.exists():
        raise FileNotFoundError(f"Media file not found: {file_path}")
    return file_path.resolve()


def _stream_sha256_and_header(file_path: Path, header_limit: int = 65536) -> tuple[str, int, bytes]:
    """Compute ``(sha256, byte_size, header_bytes)`` in ``O(1)`` RAM without ``read_bytes()``."""
    byte_size = file_path.stat().st_size
    with file_path.open("rb") as fh:
        header = fh.read(header_limit)
        fh.seek(0)
        digest = hashlib.file_digest(fh, "sha256").hexdigest()
    return digest, byte_size, header


def _probe_container_header_duration(file_path: Path) -> dict[str, Any]:
    """Pure-Python O(1)-RAM fallback parser for ISO-BMFF (MP4/MOV/M4A ``moov/mvhd``)
    and RIFF WAVE (``fmt /data``) container durations when ``ffprobe`` is unavailable."""
    try:
        file_size = file_path.stat().st_size
        if file_size < 16:
            return {}
        with file_path.open("rb") as fh:
            prefix = fh.read(16)
            # 1. RIFF WAVE container
            if len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WAVE":
                fh.seek(12)
                byte_rate = 0
                data_bytes = 0
                for _ in range(32):
                    chk = fh.read(8)
                    if len(chk) < 8:
                        break
                    chk_id = chk[:4]
                    chk_sz = struct.unpack("<I", chk[4:8])[0]
                    if chk_id == b"fmt " and chk_sz >= 12:
                        fmt_body = fh.read(12)
                        if len(fmt_body) == 12:
                            byte_rate = struct.unpack("<I", fmt_body[8:12])[0]
                        fh.seek(chk_sz - len(fmt_body) + (chk_sz & 1), 1)
                    elif chk_id == b"data":
                        data_bytes = chk_sz
                        break
                    else:
                        fh.seek(chk_sz + (chk_sz & 1), 1)
                if byte_rate > 0 and data_bytes > 0:
                    return {
                        "duration_seconds": float(data_bytes) / float(byte_rate),
                        "format_name": "wav",
                        "audio_codec": "pcm",
                    }
                return {}

            # 2. ISO-BMFF (MP4 / MOV / M4A) — seek top-level boxes in O(1) RAM
            if len(prefix) >= 8 and prefix[4:8] in (
                b"ftyp",
                b"moov",
                b"mdat",
                b"free",
                b"wide",
                b"skip",
            ):
                pos = 0
                for _ in range(64):
                    if pos + 8 > file_size:
                        break
                    fh.seek(pos)
                    hdr = fh.read(8)
                    if len(hdr) < 8:
                        break
                    box_sz, box_type = struct.unpack(">I4s", hdr)
                    hdr_len = 8
                    if box_sz == 1:
                        ext = fh.read(8)
                        if len(ext) < 8:
                            break
                        box_sz = struct.unpack(">Q", ext)[0]
                        hdr_len = 16
                    elif box_sz == 0:
                        box_sz = file_size - pos
                    if box_sz < hdr_len:
                        break

                    if box_type == b"moov":
                        moov_end = min(file_size, pos + box_sz)
                        sub_pos = pos + hdr_len
                        for _ in range(64):
                            if sub_pos + 8 > moov_end:
                                break
                            fh.seek(sub_pos)
                            shdr = fh.read(8)
                            if len(shdr) < 8:
                                break
                            ssz, stype = struct.unpack(">I4s", shdr)
                            shdr_len = 8
                            if ssz == 1:
                                sext = fh.read(8)
                                if len(sext) < 8:
                                    break
                                ssz = struct.unpack(">Q", sext)[0]
                                shdr_len = 16
                            elif ssz == 0:
                                ssz = moov_end - sub_pos
                            if ssz < shdr_len:
                                break
                            if stype == b"mvhd":
                                mvhd_body = fh.read(min(64, ssz - shdr_len))
                                if len(mvhd_body) >= 20 and mvhd_body[0] == 0:
                                    ts, dur = struct.unpack(">II", mvhd_body[12:20])
                                    if ts > 0:
                                        return {
                                            "duration_seconds": float(dur) / float(ts),
                                            "format_name": "mov,mp4,m4a",
                                        }
                                elif len(mvhd_body) >= 32 and mvhd_body[0] == 1:
                                    ts, dur = struct.unpack(">IQ", mvhd_body[20:32])
                                    if ts > 0:
                                        return {
                                            "duration_seconds": float(dur) / float(ts),
                                            "format_name": "mov,mp4,m4a",
                                        }
                                break
                            sub_pos += ssz
                        break
                    pos += box_sz
    except Exception as exc:
        logger.debug("Container header duration fallback failed for %s: %s", file_path, exc)
    return {}


class ImageExtractor:
    """Extract structured Markdown, dimensions, and metadata from standalone images."""

    def extract(
        self,
        file_path: Path,
        *,
        caption_text: str | None = None,
        ocr_text: str | None = None,
    ) -> ExtractionResult:
        """Extract a searchable ``Section`` from an image file in ``O(1)`` memory."""
        resolved = _validate_media_path(file_path)
        start_t = time.perf_counter()
        sha256, byte_size, header = _stream_sha256_and_header(resolved)
        _, mime_type = infer_media_mime_type(resolved, header)
        width, height = parse_image_dimensions(header, mime_type)

        dim_str = f"{width}x{height}" if width and height else "unknown"
        lines = [
            f"# Image: {resolved.name}",
            f"- **Format:** `{mime_type}`",
            f"- **Dimensions:** `{dim_str}`",
            f"- **Size:** `{byte_size}` bytes",
            f"- **SHA-256:** `{sha256}`",
        ]
        if caption_text and caption_text.strip():
            lines.append("")
            lines.append("## Visual Description")
            lines.append(caption_text.strip())
        if ocr_text and ocr_text.strip():
            lines.append("")
            lines.append("## Extracted Text (OCR)")
            lines.append(ocr_text.strip())

        content = "\n".join(lines)
        meta: dict[str, Any] = {
            "modality": "image",
            "mime_type": mime_type,
            "width": width,
            "height": height,
            "sha256": sha256,
            "byte_size": byte_size,
            "source_file": resolved.name,
        }
        section = Section(
            section_id="image",
            name=resolved.name,
            order=1,
            level=1,
            content=content,
            char_start=0,
            char_end=len(content),
            citation="#image",
            metadata=meta,
        )
        duration_ms = int((time.perf_counter() - start_t) * 1000)
        return ExtractionResult(
            sections=[section],
            title=resolved.name,
            file_category=FileCategory.IMAGE,
            metadata=meta,
            extractor_used="image_extractor",
            tables=[],
            cached=False,
            duration_ms=duration_ms,
            warnings=[],
        )


class VideoAudioExtractor:
    """Streaming extractor for audio and ``20 GB+`` video files with ``ffprobe``/``ffmpeg``."""

    def __init__(
        self,
        *,
        segment_seconds: int = DEFAULT_SEGMENT_SECONDS,
        max_segments: int = MAX_DEFAULT_SEGMENTS,
    ) -> None:
        if segment_seconds < 5:
            raise ValidationError("segment_seconds must be >= 5")
        self.segment_seconds = segment_seconds
        self.max_segments = max(1, max_segments)

    @staticmethod
    def ffprobe_available() -> bool:
        """Return ``True`` when ``ffprobe`` is on ``PATH``."""
        return shutil.which("ffprobe") is not None

    @staticmethod
    def ffmpeg_available() -> bool:
        """Return ``True`` when ``ffmpeg`` is on ``PATH``."""
        return shutil.which("ffmpeg") is not None

    def probe_media(self, file_path: Path) -> dict[str, Any]:
        """Run hardened ``ffprobe`` on ``file_path`` and return normalized stream metadata."""
        resolved = _validate_media_path(file_path)
        if not self.ffprobe_available():
            return _probe_container_header_duration(resolved)

        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-protocol_whitelist",
            "file,pipe",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "--",
            str(resolved),
        ]
        try:
            proc = subprocess.run(  # nosec B603 # reason: validated local path with protocol_whitelist and DEVNULL
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                return _probe_container_header_duration(resolved)
            raw = json.loads(proc.stdout)
        except Exception as exc:
            logger.debug("ffprobe failed for %s: %s", resolved, exc)
            return _probe_container_header_duration(resolved)

        fmt = raw.get("format") or {}
        streams = raw.get("streams") or []
        duration_sec: float | None = None
        if fmt.get("duration") is not None:
            try:
                duration_sec = max(0.0, float(fmt["duration"]))
            except (TypeError, ValueError):
                duration_sec = None

        video_codec: str | None = None
        audio_codec: str | None = None
        width: int | None = None
        height: int | None = None
        for st in streams:
            codec_type = st.get("codec_type")
            if codec_type == "video" and video_codec is None:
                video_codec = st.get("codec_name")
                width = int(st["width"]) if st.get("width") else None
                height = int(st["height"]) if st.get("height") else None
                if duration_sec is None and st.get("duration"):
                    try:
                        duration_sec = max(0.0, float(st["duration"]))
                    except (TypeError, ValueError):
                        pass
            elif codec_type == "audio" and audio_codec is None:
                audio_codec = st.get("codec_name")
                if duration_sec is None and st.get("duration"):
                    try:
                        duration_sec = max(0.0, float(st["duration"]))
                    except (TypeError, ValueError):
                        pass

        return {
            "duration_seconds": duration_sec,
            "format_name": fmt.get("format_name"),
            "video_codec": video_codec,
            "audio_codec": audio_codec,
            "width": width,
            "height": height,
        }

    def extract_keyframe_png(self, file_path: Path, timestamp_sec: float) -> bytes | None:
        """Extract a single scaled PNG keyframe at ``timestamp_sec`` via hardened ``ffmpeg``."""
        resolved = _validate_media_path(file_path)
        if not self.ffmpeg_available():
            return None

        cmd = [
            "ffmpeg",
            "-nostdin",
            "-v",
            "quiet",
            "-protocol_whitelist",
            "file,pipe",
            "-ss",
            f"{max(0.0, timestamp_sec):.2f}",
            "-i",
            str(resolved),
            "-frames:v",
            "1",
            "-vf",
            "scale='min(1280,iw)':-2",
            "-fs",
            str(MAX_KEYFRAME_BYTES),
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "pipe:1",
        ]
        try:
            proc = subprocess.run(  # nosec B603 # reason: validated local path with protocol_whitelist and -fs cap
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=60,
                check=False,
            )
            if proc.returncode != 0 or not proc.stdout:
                return None
            return proc.stdout[:MAX_KEYFRAME_BYTES]
        except Exception as exc:
            logger.debug("ffmpeg keyframe extraction failed for %s: %s", resolved, exc)
            return None

    def extract_audio_window_wav(
        self,
        file_path: Path,
        start_sec: float,
        duration_sec: float,
    ) -> bytes | None:
        """Extract mono 16 kHz WAV audio for ``[start_sec, start_sec + duration_sec]``."""
        resolved = _validate_media_path(file_path)
        if not self.ffmpeg_available():
            return None

        cmd = [
            "ffmpeg",
            "-nostdin",
            "-v",
            "quiet",
            "-protocol_whitelist",
            "file,pipe",
            "-ss",
            f"{max(0.0, start_sec):.2f}",
            "-t",
            f"{max(1.0, duration_sec):.2f}",
            "-i",
            str(resolved),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-fs",
            str(MAX_AUDIO_WINDOW_BYTES),
            "-f",
            "wav",
            "pipe:1",
        ]
        try:
            proc = subprocess.run(  # nosec B603 # reason: validated local path with protocol_whitelist and -fs cap
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=60,
                check=False,
            )
            if proc.returncode != 0 or not proc.stdout:
                return None
            return proc.stdout[:MAX_AUDIO_WINDOW_BYTES]
        except Exception as exc:
            logger.debug("ffmpeg audio window extraction failed for %s: %s", resolved, exc)
            return None

    def extract(
        self,
        file_path: Path,
        *,
        segment_transcripts: dict[int, str] | None = None,
        segment_captions: dict[int, str] | None = None,
    ) -> ExtractionResult:
        """Extract time-windowed ``Section`` objects from a video or audio file in ``O(1)`` RAM."""
        resolved = _validate_media_path(file_path)
        start_t = time.perf_counter()
        sha256, byte_size, header = _stream_sha256_and_header(resolved)
        modality, mime_type = infer_media_mime_type(resolved, header)
        if modality not in ("audio", "video"):
            ext = resolved.suffix.lstrip(".").lower()
            modality = "audio" if ext in AUDIO_EXTENSIONS else "video"

        warnings: list[str] = []
        probe_info = self.probe_media(resolved)
        if not probe_info and not self.ffprobe_available():
            warnings.append("ffprobe not found on PATH; using deterministic manifest fallback.")

        duration_seconds = probe_info.get("duration_seconds")
        if duration_seconds is None or duration_seconds <= 0:
            total_windows = 1
            effective_duration = float(self.segment_seconds)
        else:
            effective_duration = float(duration_seconds)
            total_windows = min(
                self.max_segments,
                max(
                    1, int((effective_duration + self.segment_seconds - 1) // self.segment_seconds)
                ),
            )

        sections: list[Section] = []
        char_cursor = 0
        for idx in range(total_windows):
            win_start = float(idx * self.segment_seconds)
            win_end = min(effective_duration, float((idx + 1) * self.segment_seconds))
            if win_end <= win_start:
                win_end = win_start + float(self.segment_seconds)

            start_hms = format_hms(win_start)
            end_hms = format_hms(win_end)
            citation = f"#t={start_hms}-{end_hms}"
            start_ms = int(round(win_start * 1000))
            end_ms = int(round(win_end * 1000))

            transcript = (segment_transcripts or {}).get(idx, "").strip()
            caption = (segment_captions or {}).get(idx, "").strip()

            lines = [
                f"# {modality.capitalize()} Segment: {resolved.name} ({start_hms} - {end_hms})",
                f"- **Modality:** `{modality}` (`{mime_type}`)",
                f"- **Time Window:** `{start_hms}` to `{end_hms}` (`{start_ms}..{end_ms}` ms)",
                f"- **Source Size:** `{byte_size}` bytes",
                f"- **SHA-256:** `{sha256}`",
            ]
            if probe_info.get("video_codec") or probe_info.get("audio_codec"):
                lines.append(
                    f"- **Codecs:** video=`{probe_info.get('video_codec') or 'none'}` "
                    f"audio=`{probe_info.get('audio_codec') or 'none'}`"
                )
            if caption:
                lines.append("")
                lines.append("## Keyframe Visual Summary")
                lines.append(caption)
            if transcript:
                lines.append("")
                lines.append("## Audio Transcript")
                lines.append(transcript)

            content = "\n".join(lines)
            seg_meta: dict[str, Any] = {
                "modality": modality,
                "mime_type": mime_type,
                "sha256": sha256,
                "byte_size": byte_size,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "segment_index": idx,
                "duration_seconds": duration_seconds,
                "width": probe_info.get("width"),
                "height": probe_info.get("height"),
                "video_codec": probe_info.get("video_codec"),
                "audio_codec": probe_info.get("audio_codec"),
                "source_file": resolved.name,
            }
            sections.append(
                Section(
                    section_id=f"t-{start_hms}-{end_hms}",
                    name=f"{resolved.name} [{start_hms}-{end_hms}]",
                    order=idx + 1,
                    level=1,
                    content=content,
                    char_start=char_cursor,
                    char_end=char_cursor + len(content),
                    citation=citation,
                    metadata=seg_meta,
                )
            )
            char_cursor += len(content) + 1

        duration_ms = int((time.perf_counter() - start_t) * 1000)
        file_cat = FileCategory.AUDIO if modality == "audio" else FileCategory.VIDEO
        return ExtractionResult(
            sections=sections,
            title=resolved.name,
            file_category=file_cat,
            metadata={
                "modality": modality,
                "mime_type": mime_type,
                "sha256": sha256,
                "byte_size": byte_size,
                **probe_info,
            },
            extractor_used="video_audio_extractor",
            tables=[],
            cached=False,
            duration_ms=duration_ms,
            warnings=warnings,
        )
