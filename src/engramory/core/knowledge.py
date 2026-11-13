"""Knowledge core — ingestion, section retrieval, semantic/graph search (L0).

Orchestrates document extraction, hierarchical citations, batch database
persistence, and optional vector embeddings under Engramory's hexagonal
architecture.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from engramory.core.citations import (
    build_doc_id,
    calculate_line_numbers,
    format_local_citation,
)
from engramory.core.extraction.enrichment import SemanticChunker
from engramory.core.extraction.multimodal import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    MEDIA_EXTENSIONS,
    VIDEO_EXTENSIONS,
    infer_media_mime_type,
)
from engramory.core.extraction.router import MAX_NATIVE_TEXT_BYTES, SmartExtractionRouter
from engramory.core.kb_graph import entity_labels_for, extract_knowledge_and_media_graph
from engramory.core.models import KB_SECTION_MODALITIES, KBBlob, KBSection
from engramory.core.repository import Repository
from engramory.ports.graph import GraphPort
from engramory.ports.llm import LLMPort
from engramory.ports.storage import StoragePort

logger = logging.getLogger(__name__)

INLINE_BLOB_THRESHOLD_BYTES: int = 256 * 1024  # 256 KiB Rule D14a
_BINARY_DOC_EXTENSIONS: frozenset[str] = MEDIA_EXTENSIONS | frozenset(
    {"pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt"}
)


class KnowledgeCore:
    """Knowledge core orchestration (L0)."""

    def __init__(
        self,
        repo: Repository,
        *,
        llm: LLMPort | None = None,
        storage: StoragePort | None = None,
        graph: GraphPort | None = None,
        router: SmartExtractionRouter | None = None,
        chunker: SemanticChunker | None = None,
        expected_embedding_dims: int = 1536,
    ) -> None:
        self.repo = repo
        self.llm = llm
        self.storage = storage
        self.graph = graph
        self.router = router or SmartExtractionRouter()
        self.chunker = chunker
        self.expected_embedding_dims = expected_embedding_dims

    async def ingest_file(
        self,
        path: Path | str,
        *,
        tenant_id: str = "default",
        project_id: str | None = None,
        scope: str = "project",
        evidence_ref: str | None = None,
        version: int = 1,
        embed: bool = True,
        repo_root: Path | None = None,
    ) -> list[str]:
        """Ingest a single document into kb_sections."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Knowledge document not found: {p}")

        batch_result = await self.ingest_documents_batch(
            [p],
            tenant_id=tenant_id,
            project_id=project_id,
            scope=scope,
            evidence_ref=evidence_ref,
            version=version,
            embed=embed,
            repo_root=repo_root,
        )
        return batch_result.get(str(p), [])

    async def ingest_media_file(
        self,
        path: Path | str,
        *,
        tenant_id: str = "default",
        project_id: str | None = None,
        scope: str = "project",
        evidence_ref: str | None = None,
        version: int = 1,
        embed: bool = True,
        project_graph: bool = True,
        repo_root: Path | None = None,
    ) -> dict[str, Any]:
        """Ingest a multimodal file (image, audio, video, PDF) into CAS + kb_sections + graph."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Media file not found: {p}")
        batch_res = await self.ingest_documents_batch(
            [p],
            tenant_id=tenant_id,
            project_id=project_id,
            scope=scope,
            evidence_ref=evidence_ref,
            version=version,
            embed=embed,
            project_graph=project_graph,
            repo_root=repo_root,
        )
        sec_ids = batch_res.get(str(p), [])
        first_sec = (
            await self.repo.get_kb_section(sec_ids[0], tenant_id=tenant_id)
            if sec_ids and hasattr(self.repo, "get_kb_section")
            else None
        )
        return {
            "path": str(p),
            "section_ids": sec_ids,
            "modality": getattr(first_sec, "modality", None),
            "blob_sha256": getattr(first_sec, "blob_sha256", None),
            "blob_uri": getattr(first_sec, "blob_uri", None),
        }

    async def ingest_documents_batch(
        self,
        paths: Sequence[Path | str],
        *,
        tenant_id: str = "default",
        project_id: str | None = None,
        scope: str = "project",
        evidence_ref: str | None = None,
        version: int = 1,
        embed: bool = True,
        project_graph: bool = False,
        repo_root: Path | None = None,
    ) -> dict[str, list[str]]:
        """Ingest multiple documents into kb_sections with batch upsert and batch embedding."""
        from engramory.access.poisoning_guard import MemoryPoisoningError, inspect_memory_write
        from engramory.core.models import ValidationError

        all_sections: list[KBSection] = []
        all_blobs: list[KBBlob] = []
        path_to_sections: dict[str, list[KBSection]] = {}
        pending_doc_upserts: list[tuple[Any, Sequence[Any]]] = []
        resolved_root = repo_root.resolve() if repo_root is not None else None

        for p_raw in paths:
            p = Path(p_raw)
            if not p.exists():
                logger.warning("Skipping non-existent file in batch: %s", p)
                continue

            p_res = p.resolve()
            if resolved_root is not None:
                if not p_res.is_relative_to(resolved_root):
                    logger.warning("Skipping file outside repo_root (%s): %s", resolved_root, p)
                    continue
                rel_key = p_res.relative_to(resolved_root).as_posix()
            else:
                rel_key = p.name

            ext = p.suffix.lstrip(".").lower()
            file_size = p.stat().st_size
            header = b""
            if not p.is_symlink():
                try:
                    with p.open("rb") as fh:
                        header = fh.read(64)
                except OSError:
                    header = b""
            inferred_mod, inferred_mime = infer_media_mime_type(p, header)

            is_binary_or_large = (
                ext in _BINARY_DOC_EXTENSIONS
                or inferred_mod in {"image", "audio", "video"}
                or file_size > MAX_NATIVE_TEXT_BYTES
            )
            if is_binary_or_large:
                full_text = ""
            else:
                try:
                    full_text = p.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    full_text = ""

            doc_id = build_doc_id(project_id, rel_key)
            doc_blob: KBBlob | None = None
            if self.storage is not None and (
                file_size > INLINE_BLOB_THRESHOLD_BYTES
                or ext in _BINARY_DOC_EXTENSIONS
                or inferred_mod in {"image", "audio", "video"}
            ):
                content_type = "application/pdf" if ext == "pdf" else inferred_mime
                try:
                    doc_blob = await self.storage.put_file(
                        file_path=p_res,
                        content_type=content_type,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        metadata={"source_file": p.name, "doc_id": doc_id},
                    )
                    all_blobs.append(doc_blob)
                except Exception as exc:
                    logger.warning("Failed to store CAS blob for %s: %s", p, exc)

            # Optional multimodal LLM captioning / transcription
            if (
                (ext in IMAGE_EXTENSIONS or inferred_mod == "image")
                and self.llm is not None
                and hasattr(self.llm, "describe_image")
                and file_size <= 20 * 1024 * 1024
            ):
                caption_text: str | None = None
                try:
                    with p_res.open("rb") as fh:
                        img_bytes = fh.read(20 * 1024 * 1024)
                    caption_text = await self.llm.describe_image(
                        image_bytes=img_bytes,
                        mime_type=inferred_mime,
                    )
                except Exception as exc:
                    logger.debug("Optional vision captioning skipped for %s: %s", p, exc)
                extract_res = self.router.image_extractor.extract(p_res, caption_text=caption_text)
            elif (
                ext in (AUDIO_EXTENSIONS | VIDEO_EXTENSIONS) or inferred_mod in {"audio", "video"}
            ) and self.llm is not None:
                va_ext = self.router.video_audio_extractor
                probe = va_ext.probe_media(p_res)
                dur = probe.get("duration_seconds")
                total_wins = (
                    1
                    if not dur or dur <= 0
                    else min(
                        va_ext.max_segments,
                        max(
                            1,
                            int(
                                (float(dur) + va_ext.segment_seconds - 1) // va_ext.segment_seconds
                            ),
                        ),
                    )
                )
                seg_captions: dict[int, str] = {}
                seg_transcripts: dict[int, str] = {}
                for w_idx in range(total_wins):
                    w_start = float(w_idx * va_ext.segment_seconds)
                    if inferred_mod == "video" and hasattr(self.llm, "describe_image"):
                        frame_png = va_ext.extract_keyframe_png(p_res, w_start)
                        if frame_png:
                            try:
                                seg_captions[w_idx] = await self.llm.describe_image(
                                    image_bytes=frame_png,
                                    mime_type="image/png",
                                )
                            except Exception as exc:
                                logger.debug(
                                    "Keyframe caption skipped for %s#%d: %s", p, w_idx, exc
                                )
                    if hasattr(self.llm, "transcribe_audio"):
                        wav_bytes = va_ext.extract_audio_window_wav(
                            p_res, w_start, float(va_ext.segment_seconds)
                        )
                        if wav_bytes:
                            try:
                                tr = await self.llm.transcribe_audio(
                                    audio_bytes=wav_bytes,
                                    filename=f"{p.stem}_{w_idx}.wav",
                                    mime_type="audio/wav",
                                )
                                if isinstance(tr, dict) and tr.get("text"):
                                    seg_transcripts[w_idx] = str(tr["text"])
                            except Exception as exc:
                                logger.debug(
                                    "Audio transcription skipped for %s#%d: %s", p, w_idx, exc
                                )
                extract_res = va_ext.extract(
                    p_res,
                    segment_transcripts=seg_transcripts,
                    segment_captions=seg_captions,
                )
            else:
                extract_res = self.router.extract_document(p)

            extracted_sections = extract_res.sections

            has_media_modality = any(
                (sec.metadata or {}).get("modality") in {"image", "audio", "video"}
                for sec in extracted_sections
            )
            if (
                not has_media_modality
                and self.chunker is not None
                and self.chunker is not getattr(self.router, "chunker", None)
            ):
                extracted_sections = self.chunker.refine_sections(extracted_sections)

            line_spans = [
                (1, 1)
                if not full_text
                else calculate_line_numbers(full_text, sec.char_start, sec.char_end)
                for sec in extracted_sections
            ]
            span_counts: dict[tuple[int, int], int] = {}
            for span in line_spans:
                span_counts[span] = span_counts.get(span, 0) + 1

            seen_citations: set[str] = set()
            file_secs: list[KBSection] = []
            for idx, (sec, (l_start, l_end)) in enumerate(
                zip(extracted_sections, line_spans, strict=False), 1
            ):
                slug = (sec.citation or sec.section_id or f"sec-{idx}").lstrip("#§")
                if not full_text:
                    cit = format_local_citation(str(p), None, None, slug)
                else:
                    cit = format_local_citation(
                        str(p), l_start, l_end, sec.citation or sec.section_id
                    )
                    if span_counts[(l_start, l_end)] > 1:
                        cit = f"{cit}#{slug}"
                if cit in seen_citations:
                    cit = f"{cit}-{idx}"
                seen_citations.add(cit)
                sec_domain = (
                    str(sec.metadata["domain"]).strip()
                    if sec.metadata and sec.metadata.get("domain")
                    else None
                )
                raw_mod = str((sec.metadata or {}).get("modality") or "").strip().lower()
                if raw_mod in KB_SECTION_MODALITIES:
                    sec_modality = raw_mod
                elif ext == "pdf":
                    sec_modality = "pdf"
                elif ext in {"docx", "doc", "xlsx", "xls", "pptx", "ppt"}:
                    sec_modality = "document"
                else:
                    sec_modality = "text"

                sec_sha = (
                    doc_blob.sha256
                    if doc_blob is not None
                    else (
                        str(sec.metadata["sha256"])
                        if sec.metadata
                        and sec.metadata.get("sha256")
                        and sec_modality in {"image", "audio", "video"}
                        else None
                    )
                )
                sec_uri = f"pgblob://{tenant_id}/{sec_sha}" if sec_sha else None
                sec_media_meta = (
                    {k: v for k, v in (sec.metadata or {}).items() if k != "domain"}
                    if (sec_modality in {"image", "audio", "video", "pdf", "document"} or sec_sha)
                    else {}
                )

                kb_sec = KBSection(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    domain_id=sec_domain,
                    scope=scope,
                    doc_id=doc_id,
                    citation=cit,
                    text=sec.content,
                    version=version,
                    modality=sec_modality,
                    blob_sha256=sec_sha,
                    blob_uri=sec_uri,
                    media_metadata=sec_media_meta,
                )
                inspect_memory_write(
                    kb_sec.text,
                    tenant_id=tenant_id,
                    writer_agent_id="knowledge-core",
                    target=kb_sec.doc_id,
                )
                if kb_sec.media_metadata:
                    inspect_memory_write(
                        json.dumps(dict(kb_sec.media_metadata), sort_keys=True),
                        tenant_id=tenant_id,
                        writer_agent_id="knowledge-core",
                        target=kb_sec.doc_id,
                    )
                file_secs.append(kb_sec)
                all_sections.append(kb_sec)

            path_to_sections[str(p)] = file_secs
            if p.suffix.lower() == ".md" and hasattr(self.repo, "upsert_kb_document"):
                try:
                    from engramory.core.docs_tools import parse_markdown_document

                    if resolved_root is not None:
                        inferred_root = resolved_root
                    else:
                        inferred_root = p_res.parent
                        for anc in [inferred_root, *inferred_root.parents]:
                            if (anc / ".git").exists() or (anc / "pyproject.toml").exists():
                                inferred_root = anc
                                break

                    parsed = parse_markdown_document(
                        p,
                        repo_root=inferred_root,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        scope=scope,
                    )
                    if parsed.has_frontmatter and parsed.kb_document is not None:
                        inspect_memory_write(
                            parsed.kb_document.title,
                            tenant_id=tenant_id,
                            writer_agent_id="knowledge-core",
                            target=parsed.kb_document.doc_id,
                        )
                        for st in parsed.steps:
                            inspect_memory_write(
                                st.heading_path,
                                tenant_id=tenant_id,
                                writer_agent_id="knowledge-core",
                                target=parsed.kb_document.doc_id,
                            )
                            if st.prose_context:
                                inspect_memory_write(
                                    st.prose_context,
                                    tenant_id=tenant_id,
                                    writer_agent_id="knowledge-core",
                                    target=parsed.kb_document.doc_id,
                                )
                            inspect_memory_write(
                                st.command_body,
                                tenant_id=tenant_id,
                                writer_agent_id="knowledge-core",
                                target=parsed.kb_document.doc_id,
                            )
                        pending_doc_upserts.append((parsed.kb_document, parsed.steps))
                except (MemoryPoisoningError, ValidationError):
                    raise
                except Exception as exc:
                    logger.debug("Skipping kb_documents extraction for %s: %s", p, exc)

        for kb_doc, kb_steps in pending_doc_upserts:
            await self.repo.upsert_kb_document(kb_doc)
            await self.repo.upsert_kb_runbook_steps(tenant_id, kb_doc.doc_id, kb_steps)

        if not all_sections:
            return {}

        # Deduplicate sections for upsert and embedding
        deduped: dict[tuple[str, str, str, int], KBSection] = {}
        for s in all_sections:
            deduped[(s.tenant_id, s.doc_id, s.citation, s.version)] = s
        unique_sections = list(deduped.values())

        section_map = await self.repo.upsert_kb_sections_batch(unique_sections)

        # Batch embedding if requested and LLM port is available
        if embed and self.llm is not None:
            try:
                texts = [s.text for s in unique_sections]
                embeddings = await self.llm.embed(texts=texts)
                valid_updates = []
                for ksec, vec in zip(unique_sections, embeddings, strict=False):
                    key = (ksec.tenant_id, ksec.doc_id, ksec.citation, ksec.version)
                    s_id = section_map.get(key)
                    if s_id and len(vec) == self.expected_embedding_dims:
                        valid_updates.append((s_id, vec))
                    elif len(vec) != self.expected_embedding_dims:
                        logger.warning(
                            "Embedding dim mismatch: expected %d, got %d. Skipping projection.",
                            self.expected_embedding_dims,
                            len(vec),
                        )
                if valid_updates:
                    embed_model = next(
                        (
                            val
                            for attr in ("embed_model", "_embed_model", "_default_embed_model")
                            if isinstance((val := getattr(self.llm, attr, None)), str)
                            and val.strip()
                        ),
                        "text-embedding-3-small",
                    )
                    await self.repo.set_kb_sections_embedding_batch(
                        valid_updates,
                        embedding_model=embed_model,
                    )
            except Exception as e:
                logger.warning("Failed to compute embeddings for knowledge batch: %s", e)

        # Map back to paths
        result: dict[str, list[str]] = {}
        for p_str, secs in path_to_sections.items():
            result[p_str] = [
                section_map[(s.tenant_id, s.doc_id, s.citation, s.version)]
                for s in secs
                if (s.tenant_id, s.doc_id, s.citation, s.version) in section_map
            ]

        # Optional canonical + Neo4j knowledge/media graph projection
        if (project_graph or self.graph is not None or all_blobs) and hasattr(
            self.repo, "bulk_upsert_graph_entities"
        ):
            try:
                g_ents, g_rels = extract_knowledge_and_media_graph(
                    unique_sections,
                    blobs=all_blobs,
                    tenant_id=tenant_id,
                    project_id=project_id,
                )
                await self.repo.bulk_upsert_graph_entities(g_ents)
                await self.repo.bulk_upsert_graph_relations(g_rels)
                if self.graph is not None:
                    for ent in g_ents:
                        await self.graph.upsert_node(
                            id=ent.id,
                            labels=entity_labels_for(ent),
                            props={
                                "name": ent.name,
                                "tenant_id": ent.tenant_id,
                                "domain_id": ent.domain_id,
                                **ent.properties,
                            },
                        )
                    for rel in g_rels:
                        await self.graph.upsert_edge(
                            src=rel.src_id,
                            dst=rel.dst_id,
                            rel=rel.rel_type,
                            props={"tenant_id": rel.tenant_id, **rel.properties},
                        )
            except Exception as exc:
                logger.warning("Failed to project knowledge/media graph: %s", exc)

        return result

    async def search_sections(
        self,
        query: str,
        *,
        tenant_id: str = "default",
        project_id: str | None = None,
        scope: str | None = None,
        modality: str | None = None,
        limit: int = 5,
    ) -> list[KBSection]:
        """Search knowledge sections with semantic vector projection or text fallback."""
        query_vec = None
        if self.llm is not None:
            try:
                vecs = await self.llm.embed(texts=[query])
                if vecs:
                    query_vec = vecs[0]
            except Exception as e:
                logger.warning("Failed to generate embedding for query: %s", e)

        search_kwargs: dict[str, Any] = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "query_vec": query_vec,
            "scope": scope,
            "limit": limit,
        }
        if modality is not None:
            search_kwargs["modality"] = modality
        return await self.repo.search_kb_sections(query, **search_kwargs)

    async def get_section(self, section_id: str, *, tenant_id: str = "default") -> KBSection | None:
        """Fetch a section by its ID."""
        return await self.repo.get_kb_section(section_id, tenant_id=tenant_id)

    async def list_sections(
        self,
        *,
        tenant_id: str = "default",
        project_id: str | None = None,
        doc_id: str | None = None,
        limit: int = 50,
    ) -> list[KBSection]:
        """List sections matching tenant and optional project/doc_id."""
        return await self.repo.list_kb_sections(
            tenant_id=tenant_id,
            project_id=project_id,
            doc_id=doc_id,
            limit=limit,
        )
