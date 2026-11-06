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


def chunk_markdown(
    content: str,
    doc_id: str,
    *,
    include_context_header: bool = True,
) -> list[tuple[str, str]]:
    """Split markdown text into fence-safe, Contextual-Retrieval-prefixed (citation, text) pairs."""
    import yaml

    lines = content.splitlines()
    fm_meta: dict[str, Any] = {}
    body_lines = lines

    # Strip YAML frontmatter from body_lines and extract metadata if present
    if lines and lines[0].strip() == "---":
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                try:
                    loaded = yaml.safe_load("\n".join(lines[1:idx]))
                    if isinstance(loaded, dict):
                        fm_meta = loaded
                except Exception:
                    pass
                body_lines = lines[idx + 1 :]
                break

    eff_doc_id = str(fm_meta.get("id") or doc_id)
    doc_title = str(fm_meta.get("title") or doc_id)
    doc_kind = str(fm_meta.get("kind") or "doc")
    doc_domain = str(fm_meta.get("domain") or "default")
    doc_tier = str(fm_meta.get("autonomy_tier") or "green")

    sections: list[tuple[str, str, list[str]]] = []
    current_citation = "§preamble"
    current_breadcrumb = doc_title
    current_lines: list[str] = []
    heading_stack: list[tuple[int, str]] = []
    seen_citations: dict[str, int] = {}
    in_fence = False
    fence_marker = ""

    for line in body_lines:
        stripped = line.strip()
        if not in_fence:
            m_fence = re.match(r"^(`{3,}|~{3,})", stripped)
            if m_fence:
                in_fence = True
                fence_marker = m_fence.group(1)
                current_lines.append(line)
                continue
        else:
            if stripped.startswith(fence_marker) and stripped == fence_marker[0] * len(stripped):
                in_fence = False
                fence_marker = ""
            current_lines.append(line)
            continue

        if re.match(r"^#{1,3}\s+", line):
            if current_lines:
                sections.append((current_citation, current_breadcrumb, current_lines))
                current_lines = []
            m_h = re.match(r"^(#{1,3})\s+(.+)$", line)
            level = len(m_h.group(1)) if m_h else 1
            heading_title = re.sub(r"^#{1,3}\s+", "", line).strip()
            if level == 1 and doc_title == doc_id:
                doc_title = heading_title
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, heading_title))
            current_breadcrumb = " > ".join(h for _, h in heading_stack)
            # Clean heading for citation anchor, disambiguating duplicate headings
            slug = re.sub(r"[^\w\-_]", "-", heading_title.lower()).strip("-")
            base_cit = f"§{slug or 'section'}"
            count = seen_citations.get(base_cit, 0) + 1
            seen_citations[base_cit] = count
            current_citation = base_cit if count == 1 else f"{base_cit}-{count}"
        current_lines.append(line)

    if current_lines:
        sections.append((current_citation, current_breadcrumb, current_lines))

    # Sub-chunk sections that exceed word ceiling while preserving line breaks and code fences
    final_chunks: list[tuple[str, str]] = []
    for citation, breadcrumb, s_lines in sections:
        text = "\n".join(s_lines).strip()
        if not text:
            continue
        ctx_header = (
            f"[Doc: {eff_doc_id} | Title: {doc_title} | Kind: {doc_kind} | "
            f"Domain: {doc_domain} | Tier: {doc_tier} | Section: {breadcrumb}]\n"
            if include_context_header
            else ""
        )
        words = text.split()
        if len(words) <= MAX_CHUNK_WORDS:
            final_chunks.append((citation, f"{ctx_header}{text}" if ctx_header else text))
        else:
            # Line-preserving sub-chunking for multi-line; word-splitting for single huge lines
            parts: list[str] = []
            buf_lines: list[str] = []
            buf_words = 0
            sub_in_fence = False
            sub_fence_marker = ""

            for ln in s_lines:
                st = ln.strip()
                if not sub_in_fence:
                    mf = re.match(r"^(`{3,}|~{3,})", st)
                    if mf:
                        sub_in_fence = True
                        sub_fence_marker = mf.group(1)
                elif st.startswith(sub_fence_marker) and st == sub_fence_marker[0] * len(st):
                    sub_in_fence = False
                    sub_fence_marker = ""

                ln_words = ln.split()
                if len(ln_words) > MAX_CHUNK_WORDS and not sub_in_fence:
                    if buf_lines:
                        parts.append("\n".join(buf_lines).strip())
                        buf_lines = []
                        buf_words = 0
                    for i in range(0, len(ln_words), MAX_CHUNK_WORDS):
                        parts.append(" ".join(ln_words[i : i + MAX_CHUNK_WORDS]))
                else:
                    exceeds = buf_words + len(ln_words) > MAX_CHUNK_WORDS
                    if exceeds and buf_lines and not sub_in_fence:
                        parts.append("\n".join(buf_lines).strip())
                        buf_lines = [ln]
                        buf_words = len(ln_words)
                    else:
                        buf_lines.append(ln)
                        buf_words += len(ln_words)

            if buf_lines:
                parts.append("\n".join(buf_lines).strip())

            for idx_part, part_text in enumerate([p for p in parts if p]):
                sub_cit = f"{citation}-p{idx_part + 1}" if idx_part > 0 else citation
                final_chunks.append(
                    (sub_cit, f"{ctx_header}{part_text}" if ctx_header else part_text)
                )

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
