"""Knowledge chunking & file ingestion utilities (PLAN-005 Phase 3).

Splits markdown documentation into citation-anchored, token-bounded sections,
computes git evidence hashes, and ingests sections into the knowledge core.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from engramory.access.authz import ActorContext
from engramory.access.surface import AccessSurface
from engramory.core.repository import Repository

MAX_CHUNK_WORDS = 300


def get_git_commit_hash(file_path: Path) -> str:
    """Return latest commit hash for file, or current HEAD."""
    try:
        res = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", str(file_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        out = res.stdout.strip()
        if out:
            return out
        head_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        return head_res.stdout.strip() or "local-uncommitted"
    except Exception:
        return "local-uncommitted"


def chunk_markdown(content: str, doc_id: str) -> list[tuple[str, str]]:
    """Split markdown text into (citation, chunk_text) pairs based on headings."""
    lines = content.splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_citation = "§preamble"
    current_lines: list[str] = []

    for line in lines:
        if re.match(r"^#{1,3}\s+", line):
            if current_lines:
                sections.append((current_citation, current_lines))
                current_lines = []
            heading_title = re.sub(r"^#{1,3}\s+", "", line).strip()
            # Clean heading for citation anchor
            slug = re.sub(r"[^\w\-_]", "-", heading_title.lower()).strip("-")
            current_citation = f"§{slug or 'section'}"
        current_lines.append(line)

    if current_lines:
        sections.append((current_citation, current_lines))

    # Sub-chunk sections that exceed word ceiling
    final_chunks: list[tuple[str, str]] = []
    for citation, s_lines in sections:
        text = "\n".join(s_lines).strip()
        if not text:
            continue
        words = text.split()
        if len(words) <= MAX_CHUNK_WORDS:
            final_chunks.append((citation, text))
        else:
            for i in range(0, len(words), MAX_CHUNK_WORDS):
                part_idx = (i // MAX_CHUNK_WORDS) + 1
                part_text = " ".join(words[i : i + MAX_CHUNK_WORDS])
                sub_cit = f"{citation}-p{part_idx}" if i > 0 else citation
                final_chunks.append((sub_cit, part_text))

    return final_chunks


async def ingest_file(
    file_path: Path,
    surface: AccessSurface,
    repo: Repository,
    ctx: ActorContext,
    scope: str = "project",
    embed: bool = True,
    adapters: Any = None,
) -> int:
    """Ingest a single document into kb_sections."""
    doc_id = file_path.stem
    content = file_path.read_text(encoding="utf-8")
    git_sha = get_git_commit_hash(file_path)
    chunks = chunk_markdown(content, doc_id)

    count = 0
    for citation, text in chunks:
        sec_id = await surface.knowledge_ingest(
            ctx,
            doc_id=doc_id,
            citation=citation,
            text=text,
            evidence_ref=git_sha,
            scope=scope,
            version=1,
        )
        if embed and adapters is not None and getattr(adapters, "llm", None) is not None:
            try:
                embeddings = await adapters.llm.embed(texts=[text])
                if embeddings:
                    await repo.set_kb_section_embedding(sec_id, embedding=embeddings[0])
            except Exception:
                pass
        count += 1

    return count
