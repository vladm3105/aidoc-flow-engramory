"""Knowledge core — ingestion, section retrieval, semantic/graph search (L0).

Orchestrates document extraction, hierarchical citations, batch database
persistence, and optional vector embeddings under Engramory's hexagonal
architecture.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from engramory.core.citations import (
    build_doc_id,
    calculate_line_numbers,
    format_local_citation,
)
from engramory.core.extraction.enrichment import SemanticChunker
from engramory.core.extraction.router import SmartExtractionRouter
from engramory.core.models import KBSection
from engramory.core.repository import Repository
from engramory.ports.llm import LLMPort

logger = logging.getLogger(__name__)


class KnowledgeCore:
    """Knowledge core orchestration (L0)."""

    def __init__(
        self,
        repo: Repository,
        *,
        llm: LLMPort | None = None,
        router: SmartExtractionRouter | None = None,
        chunker: SemanticChunker | None = None,
        expected_embedding_dims: int = 1536,
    ) -> None:
        self.repo = repo
        self.llm = llm
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
        repo_root: Path | None = None,
    ) -> dict[str, list[str]]:
        """Ingest multiple documents into kb_sections with batch upsert and batch embedding."""
        from typing import Any

        from engramory.access.poisoning_guard import MemoryPoisoningError, inspect_memory_write
        from engramory.core.models import ValidationError

        all_sections: list[KBSection] = []
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

            try:
                full_text = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                full_text = ""

            doc_id = build_doc_id(project_id, rel_key)
            extract_res = self.router.extract_document(p)
            extracted_sections = extract_res.sections

            if self.chunker is not None and self.chunker is not getattr(
                self.router, "chunker", None
            ):
                extracted_sections = self.chunker.refine_sections(extracted_sections)

            line_spans = [
                calculate_line_numbers(full_text, sec.char_start, sec.char_end)
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

                kb_sec = KBSection(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    domain_id=sec_domain,
                    scope=scope,
                    doc_id=doc_id,
                    citation=cit,
                    text=sec.content,
                    version=version,
                )
                inspect_memory_write(
                    kb_sec.text,
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

        return result

    async def search_sections(
        self,
        query: str,
        *,
        tenant_id: str = "default",
        project_id: str | None = None,
        scope: str | None = None,
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

        return await self.repo.search_kb_sections(
            query,
            tenant_id=tenant_id,
            project_id=project_id,
            query_vec=query_vec,
            scope=scope,
            limit=limit,
        )

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
