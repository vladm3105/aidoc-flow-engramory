"""Citation generation, line offset calculation, and doc_id namespacing (PLAN-010 Phase 3)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Citation:
    """Line-anchored citation reference for document sections."""

    source_type: str  # "github" | "local" | "web"
    url: str
    repo: str | None = None
    path: str | None = None
    commit_sha: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    section_slug: str | None = None

    def to_string(self) -> str:
        """Return formatted string citation."""
        return self.url


def build_doc_id(project_id: str | None, file_path: str | Path) -> str:
    """Build deterministic namespaced doc_id preventing cross-project collisions (ADR-07/ADR-08).

    Format:
        '{project_id}:{rel_path}' if project_id is provided, else 'local:{rel_path}'
    """
    clean_path = str(file_path).replace("\\", "/").strip("/")
    prefix = project_id if project_id else "local"
    return f"{prefix}:{clean_path}"


def calculate_line_numbers(text: str, char_start: int, char_end: int) -> tuple[int, int]:
    """Calculate 1-based start and end line numbers from character offsets in text."""
    if char_start < 0:
        char_start = 0
    if char_end > len(text):
        char_end = len(text)
    if char_end < char_start:
        char_end = char_start

    line_start = text.count("\n", 0, char_start) + 1
    line_end = text.count("\n", 0, char_end) + 1
    return line_start, line_end


def format_github_citation(
    repo: str,
    commit_sha: str,
    path: str,
    line_start: int | None = None,
    line_end: int | None = None,
) -> str:
    """Format full GitHub line-anchored URL citation."""
    clean_repo = repo.strip("/")
    clean_path = path.strip("/")
    base_url = f"https://github.com/{clean_repo}/blob/{commit_sha}/{clean_path}"
    if line_start is not None:
        if line_end is not None and line_end > line_start:
            return f"{base_url}#L{line_start}-L{line_end}"
        return f"{base_url}#L{line_start}"
    return base_url


def format_local_citation(
    path: str,
    line_start: int | None = None,
    line_end: int | None = None,
    section_slug: str | None = None,
) -> str:
    """Format local relative citation with line range and optional section anchor."""
    clean_path = path.replace("\\", "/").strip("/")
    anchor = ""
    if line_start is not None:
        if line_end is not None and line_end > line_start:
            anchor = f"#L{line_start}-L{line_end}"
        else:
            anchor = f"#L{line_start}"
    elif section_slug:
        anchor = f"#{section_slug.lstrip('#')}"
    return f"{clean_path}{anchor}"


def generate_evidence_ref(file_path: Path, git_sha: str | None = None) -> str:
    """Generate approved evidence_ref per ADR-06.

    Returns the git commit SHA if valid, otherwise computes sha256 of file contents
    as deterministic proof of content for uncommitted or non-git files.
    """
    if git_sha and git_sha not in ("local-uncommitted", "unknown", ""):
        return git_sha

    if file_path.exists():
        content_bytes = file_path.read_bytes()
        digest = hashlib.sha256(content_bytes).hexdigest()
        return f"sha256:{digest}"

    return "local-ephemeral"
