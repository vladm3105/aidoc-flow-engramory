"""Unit tests for multimodal extraction, GraphRAG, and MCP/AccessSurface (PLAN-023)."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from engramory.access.authz import ActorContext
from engramory.access.surface import AccessSurface
from engramory.adapters.dev.storage_local import LocalFsStorageAdapter
from engramory.core.extraction.multimodal import (
    VideoAudioExtractor,
    parse_image_dimensions,
)
from engramory.core.extraction.router import SmartExtractionRouter
from engramory.core.extraction.types import FileCategory
from engramory.core.kb_graph import entity_labels_for, extract_knowledge_and_media_graph
from engramory.core.models import GraphEntity, KBSection, ValidationError
from engramory.core.parsers.base import DocType
from engramory.core.parsers.detector import DocumentDetector
from engramory.mcp.server import McpServer


def _make_png_bytes(width: int = 320, height: int = 240) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00" * 16


def _make_mp4_bytes() -> bytes:
    return b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 128


def test_detector_magic_bytes_and_dimensions(tmp_path: Path) -> None:
    detector = DocumentDetector()

    png_bytes = _make_png_bytes(640, 480)
    assert detector.detect(png_bytes, filename="unknown.bin") == DocType.IMAGE
    assert parse_image_dimensions(png_bytes) == (640, 480)

    gif_bytes = b"GIF89a" + struct.pack("<HH", 128, 96) + b"\x00" * 16
    assert detector.detect(gif_bytes) == DocType.IMAGE
    assert parse_image_dimensions(gif_bytes) == (128, 96)

    mp4_bytes = _make_mp4_bytes()
    assert detector.detect(mp4_bytes, filename="stream.bin") == DocType.VIDEO

    mkv_bytes = b"\x1a\x45\xdf\xa3" + b"\x00" * 32
    assert detector.detect(mkv_bytes, filename="recording.mkv") == DocType.VIDEO

    wav_bytes = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32
    assert detector.detect(wav_bytes, filename="voice.bin") == DocType.AUDIO


def test_video_audio_extractor_never_calls_read_bytes_and_rejects_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_file = tmp_path / "lecture.mp4"
    video_file.write_bytes(_make_mp4_bytes())

    orig_read_bytes = Path.read_bytes

    def _forbid_read_bytes(self: Path) -> bytes:
        if self.resolve() == video_file.resolve():
            raise AssertionError("Path.read_bytes() must never be called on video files!")
        return orig_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _forbid_read_bytes)

    extractor = VideoAudioExtractor(segment_seconds=60)
    res = extractor.extract(video_file)
    assert res.file_category == FileCategory.VIDEO
    assert len(res.sections) >= 1
    assert res.sections[0].citation == "#t=00:00:00-00:01:00"
    assert res.sections[0].metadata["modality"] == "video"
    assert res.sections[0].metadata["start_ms"] == 0
    assert res.sections[0].metadata["end_ms"] == 60000

    # Reject symlink media paths
    sym = tmp_path / "sym_video.mp4"
    sym.symlink_to(video_file)
    with pytest.raises(ValidationError, match="Symlink"):
        extractor.extract(sym)

    # Reject leading-dash paths
    with pytest.raises(ValidationError, match="must not start with '-'"):
        extractor.extract(Path("-protocol_whitelist.mp4"))


def test_smart_router_routes_image_and_video_without_utf8_fallback(tmp_path: Path) -> None:
    router = SmartExtractionRouter(enable_docling=False)

    img_path = tmp_path / "diagram.png"
    img_path.write_bytes(_make_png_bytes(800, 600))
    img_res = router.extract_file(img_path)
    assert img_res.file_category == FileCategory.IMAGE
    assert img_res.sections[0].citation == "#image"
    assert img_res.sections[0].metadata["width"] == 800
    assert img_res.sections[0].metadata["height"] == 600

    vid_path = tmp_path / "demo.mp4"
    vid_path.write_bytes(_make_mp4_bytes())
    vid_res = router.extract_file(vid_path)
    assert vid_res.file_category == FileCategory.VIDEO
    assert vid_res.sections[0].citation == "#t=00:00:00-00:01:00"


def test_knowledge_and_media_graph_extraction_and_label_hygiene() -> None:
    sdd_ent = GraphEntity(id="SPEC-01", entity_type="SPEC", name="Access Surface", tenant_id="t1")
    assert entity_labels_for(sdd_ent) == ["SPEC", "SDD"]

    sec1 = KBSection(
        tenant_id="t1",
        project_id="p1",
        doc_id="p1:demo.mp4",
        citation="demo.mp4#t=00:00:00-00:01:00",
        text="Intro discussing @spec: SPEC-01 and [[Vector Search]].",
        modality="video",
        blob_sha256="a" * 64,
        blob_uri=f"pgblob://t1/{'a' * 64}",
        media_metadata={
            "start_ms": 0,
            "end_ms": 60000,
            "mime_type": "video/mp4",
            "byte_size": 1024,
        },
    )
    sec2 = KBSection(
        tenant_id="t1",
        project_id="p1",
        doc_id="p1:demo.mp4",
        citation="demo.mp4#t=00:01:00-00:02:00",
        text="Segment 2 continuing ADR-03 discussion.",
        modality="video",
        blob_sha256="a" * 64,
        blob_uri=f"pgblob://t1/{'a' * 64}",
        media_metadata={
            "start_ms": 60000,
            "end_ms": 120000,
            "mime_type": "video/mp4",
            "byte_size": 1024,
        },
    )

    entities, relations = extract_knowledge_and_media_graph(
        [sec1, sec2],
        tenant_id="t1",
        project_id="p1",
    )
    ent_by_id = {e.id: e for e in entities}
    assert "p1:demo.mp4" in ent_by_id
    assert ent_by_id["p1:demo.mp4"].entity_type == "DOCUMENT"
    assert entity_labels_for(ent_by_id["p1:demo.mp4"]) == ["DOCUMENT", "Knowledge"]

    blob_node_id = f"blob:{'a' * 16}"
    assert blob_node_id in ent_by_id
    assert ent_by_id[blob_node_id].entity_type == "MEDIA_BLOB"
    assert entity_labels_for(ent_by_id[blob_node_id]) == ["MEDIA_BLOB", "Knowledge"]

    assert "concept:vector-search" in ent_by_id
    rel_types = {(r.src_id, r.dst_id, r.rel_type) for r in relations}
    assert ("p1:demo.mp4", "p1:demo.mp4#t=00:00:00-00:01:00", "CONTAINS_SECTION") in rel_types
    assert ("p1:demo.mp4#t=00:00:00-00:01:00", blob_node_id, "DERIVED_FROM_BLOB") in rel_types
    assert (
        "p1:demo.mp4#t=00:00:00-00:01:00",
        "p1:demo.mp4#t=00:01:00-00:02:00",
        "NEXT_SEGMENT",
    ) in rel_types
    assert ("p1:demo.mp4#t=00:00:00-00:01:00", "SPEC-01", "MENTIONS_ENTITY") in rel_types
    assert (
        "p1:demo.mp4#t=00:00:00-00:01:00",
        "concept:vector-search",
        "MENTIONS_ENTITY",
    ) in rel_types


@pytest.mark.asyncio
async def test_access_surface_multimodal_ingest_and_graphrag_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    storage = LocalFsStorageAdapter(base_dir=tmp_path / "cas")

    captured_sections: list[KBSection] = []
    mock_repo = AsyncMock()

    async def _fake_upsert_batch(sections: list[KBSection]) -> dict[Any, str]:
        out: dict[Any, str] = {}
        for idx, s in enumerate(sections, 1):
            captured_sections.append(s)
            out[(s.tenant_id, s.doc_id, s.citation, s.version)] = f"sec-{idx}"
        return out

    mock_repo.upsert_kb_sections_batch.side_effect = _fake_upsert_batch
    mock_repo.get_kb_section.side_effect = lambda sid, tenant_id="default": (
        captured_sections[0] if captured_sections else None
    )
    mock_repo.find_neighbors.return_value = [
        {"id": "SPEC-03", "labels": ["SPEC"], "properties": {"name": "Retrieval Graph"}}
    ]

    # Graph adapter is None -> verifies PostgreSQL find_neighbors CTE fallback!
    surface = AccessSurface(mock_repo, graph=None, llm=None, storage=storage)
    ctx = ActorContext(
        agent_id="agent-mm",
        project_id="proj-mm",
        tenant_id="tenant-mm",
        scopes=frozenset({"agent", "project"}),
    )

    img_file = tmp_path / "arch.png"
    img_file.write_bytes(_make_png_bytes(1024, 768))

    ingest_res = await surface.knowledge_ingest_media(
        ctx,
        file_path=img_file,
        evidence_ref="CHG-008",
    )
    assert ingest_res["modality"] == "image"
    assert ingest_res["blob_sha256"] is not None
    assert ingest_res["blob_uri"] == f"pgblob://tenant-mm/{ingest_res['blob_sha256']}"
    assert len(captured_sections) == 1
    assert captured_sections[0].citation.endswith("arch.png#image")

    # Search with expand_graph=True using PostgreSQL find_neighbors fallback
    mock_repo.search_kb_sections.return_value = [captured_sections[0]]
    hits = await surface.knowledge_search(
        ctx,
        "arch",
        modality="image",
        expand_graph=True,
        graph_depth=2,
    )
    assert len(hits) == 1
    assert hits[0].modality == "image"
    assert "graph_context" in hits[0].media_metadata
    assert hits[0].media_metadata["graph_context"][0]["id"] == "SPEC-03"

    # Verify MCP server serializes multimodal fields while preserving default call signature
    mcp = McpServer(surface, ctx)
    mcp_res = await mcp.handle_call_tool(
        "knowledge_search",
        {"query": "arch", "modality": "image", "expand_graph": True, "graph_depth": 2},
    )
    payload = json.loads(mcp_res["content"][0]["text"])
    assert payload[0]["modality"] == "image"
    assert payload[0]["blob_sha256"] == ingest_res["blob_sha256"]
    assert payload[0]["media_metadata"]["graph_context"][0]["id"] == "SPEC-03"


def test_multimodal_magic_headers_dimensions_and_ffmpeg_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from engramory.core.extraction.multimodal import (
        ImageExtractor,
        VideoAudioExtractor,
        infer_media_mime_type,
        parse_image_dimensions,
    )

    # 1. infer_media_mime_type across all magic headers
    p = tmp_path / "test.bin"
    assert infer_media_mime_type(p, b"\xff\xd8\xff\xe0") == ("image", "image/jpeg")
    assert infer_media_mime_type(p, b"GIF89a1234") == ("image", "image/gif")
    assert infer_media_mime_type(p, b"RIFF\x00\x00\x00\x00WEBP") == ("image", "image/webp")
    assert infer_media_mime_type(p, b"RIFF\x00\x00\x00\x00WAVE") == ("audio", "audio/wav")
    assert infer_media_mime_type(p, b"ID3\x04\x00") == ("audio", "audio/mpeg")
    assert infer_media_mime_type(p, b"fLaC\x00\x00") == ("audio", "audio/flac")
    assert infer_media_mime_type(p, b"OggS\x00\x02") == ("audio", "audio/ogg")
    assert infer_media_mime_type(p, b"\x00\x00\x00\x18ftypM4A ") == ("audio", "audio/mp4")
    assert infer_media_mime_type(p, b"\x00\x00\x00\x18ftypqt  ") == ("video", "video/quicktime")
    assert infer_media_mime_type(p, b"\x00\x00\x00\x18ftypisom") == ("video", "video/mp4")
    assert infer_media_mime_type(tmp_path / "clip.webm", b"\x1a\x45\xdf\xa3") == (
        "video",
        "video/webm",
    )
    assert infer_media_mime_type(tmp_path / "clip.mkv", b"\x1a\x45\xdf\xa3") == (
        "video",
        "video/x-matroska",
    )

    # 2. parse_image_dimensions for GIF, WebP (VP8X, VP8, VP8L), and JPEG
    gif_hdr = b"GIF89a" + struct.pack("<HH", 320, 240)
    assert parse_image_dimensions(gif_hdr) == (320, 240)

    vp8x_hdr = (
        b"RIFF"
        + struct.pack("<I", 30)
        + b"WEBPVP8X"
        + (b"\x00" * 8)
        + (639).to_bytes(3, "little")
        + (479).to_bytes(3, "little")
    )
    assert parse_image_dimensions(vp8x_hdr) == (640, 480)

    vp8_hdr = (
        b"RIFF"
        + struct.pack("<I", 30)
        + b"WEBPVP8 "
        + (b"\x00" * 10)
        + struct.pack("<HH", 800, 600)
    )
    assert parse_image_dimensions(vp8_hdr) == (800, 600)

    bits = (1024 - 1) | ((768 - 1) << 14)
    vp8l_hdr = (
        b"RIFF"
        + struct.pack("<I", 36)
        + b"WEBPVP8L"
        + (b"\x00" * 5)
        + bits.to_bytes(4, "little")
        + (b"\x00" * 10)
    )
    assert parse_image_dimensions(vp8l_hdr) == (1024, 768)

    jpeg_hdr = (
        b"\xff\xd8"
        + b"\xff\xe0\x00\x04\x00\x00"
        + b"\xff\xc0\x00\x11\x08"
        + struct.pack(">HH", 1080, 1920)
        + b"\x03\x01\x11\x00"
    )
    assert parse_image_dimensions(jpeg_hdr) == (1920, 1080)

    # 3. ImageExtractor with caption_text and ocr_text
    img_path = tmp_path / "screen.png"
    img_path.write_bytes(_make_png_bytes(640, 480))
    img_res = ImageExtractor().extract(
        img_path,
        caption_text="System architecture diagram",
        ocr_text="PostgreSQL -> Neo4j",
    )
    assert "Visual Description" in img_res.sections[0].content
    assert "Extracted Text (OCR)" in img_res.sections[0].content

    # 4. VideoAudioExtractor validation and mocked ffprobe/ffmpeg helpers
    with pytest.raises(ValidationError):
        VideoAudioExtractor(segment_seconds=2)

    va = VideoAudioExtractor(segment_seconds=30, max_segments=4)
    monkeypatch.setattr("shutil.which", lambda binary: f"/usr/bin/{binary}")

    probe_json = json.dumps(
        {
            "format": {"duration": "75.0", "format_name": "mov,mp4"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }
    )

    def _fake_run(cmd: list[str], **kwargs: Any) -> Any:
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout=probe_json)
        return SimpleNamespace(returncode=0, stdout=b"FAKE_MEDIA_BYTES")

    monkeypatch.setattr("subprocess.run", _fake_run)
    vid_path = tmp_path / "demo.mp4"
    vid_path.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 128)

    info = va.probe_media(vid_path)
    assert info["duration_seconds"] == 75.0
    assert info["video_codec"] == "h264"
    assert info["audio_codec"] == "aac"
    assert va.extract_keyframe_png(vid_path, 10.0) == b"FAKE_MEDIA_BYTES"
    assert va.extract_audio_window_wav(vid_path, 0.0, 30.0) == b"FAKE_MEDIA_BYTES"

    ext_res = va.extract(
        vid_path,
        segment_transcripts={0: "Hello world", 1: "Second segment"},
        segment_captions={0: "Title slide", 1: "Architecture slide"},
    )
    assert len(ext_res.sections) == 3
    assert "Keyframe Visual Summary" in ext_res.sections[0].content
    assert "Audio Transcript" in ext_res.sections[0].content


def test_docling_extractor_mocked_conversion_and_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    import engramory.core.extraction.docling as docling_mod
    from engramory.core.extraction.docling import DoclingExtractor

    monkeypatch.setattr(docling_mod, "DOCLING_AVAILABLE", True)

    fake_table = SimpleNamespace(export_to_markdown=lambda: "| A | B |\n|---|---|\n| 1 | 2 |")
    fake_pic = SimpleNamespace(
        caption_text=lambda doc: "Figure 1: Pipeline",
        prov=[SimpleNamespace(page_no=2)],
    )
    fake_doc = SimpleNamespace(
        export_to_markdown=lambda: "# Spec Document\n\nBody text.",
        tables=[fake_table],
        pictures=[fake_pic],
    )
    fake_converter = SimpleNamespace(convert=lambda path_str: SimpleNamespace(document=fake_doc))
    monkeypatch.setattr(docling_mod, "DocumentConverter", lambda: fake_converter, raising=False)

    cache_dir = tmp_path / "docling_cache"
    extractor = DoclingExtractor(enable_cache=True, cache_dir=cache_dir)
    extractor._converter = fake_converter

    pdf_file = tmp_path / "design.pdf"
    pdf_file.write_bytes(b"%PDF-1.7 sample content")

    res1 = extractor.extract(pdf_file)
    assert res1.cache_hit is False
    assert len(res1.tables) == 1
    assert len(res1.images) == 1
    assert res1.images[0]["caption"] == "Figure 1: Pipeline"
    assert res1.images[0]["page_no"] == 2

    # Second call hits JSON cache
    res2 = extractor.extract(pdf_file)
    assert res2.cache_hit is True
    assert res2.images[0]["caption"] == "Figure 1: Pipeline"


@pytest.mark.asyncio
async def test_litellm_adapter_describe_image_and_transcribe_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engramory.adapters.dev.llm_litellm import LiteLLMAdapter

    llm = LiteLLMAdapter(base_url="http://127.0.0.1:4000", api_key="sk-test")

    with pytest.raises(ValueError, match="non-empty"):
        await llm.describe_image(image_bytes=b"")

    with pytest.raises(ValueError, match="non-empty"):
        await llm.transcribe_audio(audio_bytes=b"")

    monkeypatch.setattr(
        llm,
        "_sync_request",
        lambda path, payload: {"choices": [{"message": {"content": "Vision summary"}}]},
    )
    monkeypatch.setattr(
        llm,
        "_sync_multipart_request",
        lambda path, **kwargs: {
            "text": "Transcribed speech",
            "segments": [{"start": 0.0, "end": 5.0, "text": "Transcribed speech"}],
        },
    )

    desc = await llm.describe_image(image_bytes=b"PNG_DATA", mime_type="image/png")
    assert desc == "Vision summary"

    tr = await llm.transcribe_audio(audio_bytes=b"WAV_DATA", filename="clip.wav")
    assert tr["text"] == "Transcribed speech"
    assert len(tr["segments"]) == 1
