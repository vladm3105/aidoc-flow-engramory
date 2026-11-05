"""Commit-differential incremental Git repository sync engine (PLAN-010 Phase 4)."""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from engramory.core.citations import (
    build_doc_id,
    calculate_line_numbers,
    format_github_citation,
)
from engramory.core.extraction.router import SmartExtractionRouter
from engramory.core.models import KBSection

if TYPE_CHECKING:
    from engramory.core.repository import Repository
    from engramory.ports.llm import LLMPort

logger = logging.getLogger(__name__)


class FileChangeStatus(StrEnum):
    """Git file differential status."""

    ADDED = "A"
    MODIFIED = "M"
    DELETED = "D"
    RENAMED = "R"
    UNCHANGED = "U"


@dataclass
class ChangedFile:
    """Individual file change in Git commit differential."""

    rel_path: str
    status: FileChangeStatus
    old_path: str | None = None


@dataclass
class SyncResult:
    """Result of repository synchronization operation."""

    success: bool
    base_sha: str
    head_sha: str
    files_added: int = 0
    files_modified: int = 0
    files_deleted: int = 0
    files_skipped: int = 0
    sections_created: int = 0
    duration_ms: int = 0
    error_message: str | None = None
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "files_added": self.files_added,
            "files_modified": self.files_modified,
            "files_deleted": self.files_deleted,
            "files_skipped": self.files_skipped,
            "sections_created": self.sections_created,
            "duration_ms": self.duration_ms,
            "error_message": self.error_message,
            "errors": list(self.errors),
        }


class GitSyncEngine:
    """Orchestrates commit-differential synchronization for Git repositories."""

    SUPPORTED_EXTENSIONS = {
        ".md",
        ".markdown",
        ".mdown",
        ".html",
        ".htm",
        ".txt",
        ".text",
        ".pdf",
        ".docx",
        ".template",
    }

    def __init__(
        self,
        router: SmartExtractionRouter | None = None,
        github_repo: str | None = None,
    ) -> None:
        self.router = router or SmartExtractionRouter()
        self.github_repo = github_repo

    def get_git_head_sha(self, repo_path: Path) -> str:
        """Resolve current HEAD commit SHA."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                check=False,
            )
            sha = res.stdout.strip()
            if sha and not res.returncode:
                return sha
        except Exception:
            pass
        return "local-head"

    def get_git_origin_repo(self, repo_path: Path) -> str | None:
        """Extract GitHub repo owner/name from remote origin if present."""
        if self.github_repo:
            return self.github_repo
        try:
            res = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                check=False,
            )
            raw = res.stdout.strip()
            if raw and ("github.com" in raw or "gitea" in raw):
                clean = raw.replace(".git", "").split("github.com/")[-1].split(":")[-1]
                return clean.strip("/")
        except Exception:
            pass
        return None

    def get_changed_files(
        self,
        repo_path: Path,
        base_sha: str | None = None,
        head_sha: str | None = None,
    ) -> list[ChangedFile]:
        """Compute git differential between base_sha and head_sha (or uncommitted diff)."""
        if not (repo_path / ".git").exists():
            # Not a git repo — treat all existing supported files as ADDED
            return [
                ChangedFile(rel_path=str(p.relative_to(repo_path)), status=FileChangeStatus.ADDED)
                for p in repo_path.rglob("*")
                if p.is_file() and p.suffix.lower() in self.SUPPORTED_EXTENSIONS
            ]

        if not base_sha:
            # Full initial baseline sync: list all tracked files in git
            res = subprocess.run(
                ["git", "ls-files"],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                check=False,
            )
            files = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            return [
                ChangedFile(rel_path=f, status=FileChangeStatus.ADDED)
                for f in files
                if Path(f).suffix.lower() in self.SUPPORTED_EXTENSIONS
            ]

        # Incremental diff between base and head
        target_head = head_sha or "HEAD"
        cmd = ["git", "diff", "--name-status", f"{base_sha}..{target_head}"]
        res = subprocess.run(
            cmd,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            check=False,
        )

        changes: list[ChangedFile] = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            status_code = parts[0][0].upper()
            file_path = parts[-1]

            if Path(file_path).suffix.lower() not in self.SUPPORTED_EXTENSIONS:
                continue

            if status_code == "A":
                changes.append(ChangedFile(rel_path=file_path, status=FileChangeStatus.ADDED))
            elif status_code == "M":
                changes.append(ChangedFile(rel_path=file_path, status=FileChangeStatus.MODIFIED))
            elif status_code == "D":
                changes.append(ChangedFile(rel_path=file_path, status=FileChangeStatus.DELETED))
            elif status_code == "R" and len(parts) >= 3:
                changes.append(
                    ChangedFile(
                        rel_path=parts[2],
                        status=FileChangeStatus.RENAMED,
                        old_path=parts[1],
                    )
                )

        return changes

    def extract_sync_sections(
        self,
        repo_path: Path,
        changed_file: ChangedFile,
        tenant_id: str = "default",
        project_id: str | None = None,
        scope: str = "project",
        head_sha: str | None = None,
        version: int = 1,
    ) -> list[KBSection]:
        """Extract sections from an added or modified file and construct KBSection records."""
        full_path = repo_path / changed_file.rel_path
        if not full_path.exists():
            return []

        doc_id = build_doc_id(project_id, changed_file.rel_path)
        sha = head_sha or self.get_git_head_sha(repo_path)
        repo_name = self.get_git_origin_repo(repo_path)

        # Extract file sections using router
        extract_res = self.router.extract_file(full_path, refine_chunks=True)
        try:
            full_text = full_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            full_text = ""

        sections: list[KBSection] = []
        for sec in extract_res.sections:
            # Calculate line numbers
            l_start, l_end = calculate_line_numbers(full_text, sec.char_start, sec.char_end)
            if repo_name:
                cit_url = format_github_citation(
                    repo=repo_name,
                    commit_sha=sha,
                    path=changed_file.rel_path,
                    line_start=l_start,
                    line_end=l_end,
                )
            else:
                cit_url = f"{changed_file.rel_path}#L{l_start}-L{l_end}"

            # Combine citation URL and section slug
            full_citation = f"{cit_url}{sec.citation or ''}"

            sections.append(
                KBSection(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    doc_id=doc_id,
                    citation=full_citation,
                    text=sec.content,
                    scope=scope,
                    version=version,
                )
            )

        return sections

    async def sync(
        self,
        repo: Repository,
        repo_path: Path,
        base_sha: str | None = None,
        head_sha: str | None = None,
        tenant_id: str = "default",
        project_id: str | None = None,
        scope: str = "project",
        llm: LLMPort | None = None,
    ) -> SyncResult:
        """Perform commit-differential sync and batch persist changes to Repository."""
        start_t = time.perf_counter()
        target_head = head_sha or self.get_git_head_sha(repo_path)
        changed_files = self.get_changed_files(repo_path, base_sha, target_head)

        files_added: list[str] = []
        files_modified: list[str] = []
        files_deleted: list[str] = []
        files_skipped: list[str] = []
        all_sections: list[KBSection] = []

        for cf in changed_files:
            if cf.status == FileChangeStatus.ADDED:
                files_added.append(cf.rel_path)
                secs = self.extract_sync_sections(
                    repo_path,
                    cf,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    scope=scope,
                    head_sha=target_head,
                    version=1,
                )
                all_sections.extend(secs)
            elif cf.status in (FileChangeStatus.MODIFIED, FileChangeStatus.RENAMED):
                files_modified.append(cf.rel_path)
                if cf.status == FileChangeStatus.RENAMED and cf.old_path:
                    old_doc_id = build_doc_id(project_id, cf.old_path)
                    await repo.delete_kb_sections_for_doc(tenant_id, old_doc_id)
                secs = self.extract_sync_sections(
                    repo_path,
                    cf,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    scope=scope,
                    head_sha=target_head,
                    version=2,
                )
                all_sections.extend(secs)
            elif cf.status == FileChangeStatus.DELETED:
                files_deleted.append(cf.rel_path)
                doc_id = build_doc_id(project_id, cf.rel_path)
                await repo.delete_kb_sections_for_doc(tenant_id, doc_id)
            else:
                files_skipped.append(cf.rel_path)

        if all_sections:
            deduped: dict[tuple[str, str, str, int], KBSection] = {}
            for s in all_sections:
                deduped[(s.tenant_id, s.doc_id, s.citation, s.version)] = s
            unique_sections = list(deduped.values())

            section_map = await repo.upsert_kb_sections_batch(unique_sections)
            if llm is not None:
                try:
                    texts = [s.text for s in unique_sections]
                    embeddings = await llm.embed(texts=texts)
                    valid_updates = []
                    for sec, vec in zip(unique_sections, embeddings, strict=False):
                        key = (sec.tenant_id, sec.doc_id, sec.citation, sec.version)
                        s_id = section_map.get(key)
                        if s_id and len(vec) == 1536:
                            valid_updates.append((s_id, vec))
                    if valid_updates:
                        await repo.set_kb_sections_embedding_batch(valid_updates)
                except Exception as e:
                    logger.warning("Failed to embed sections during git sync: %s", e)

        duration_ms = (time.perf_counter() - start_t) * 1000
        return SyncResult(
            success=True,
            base_sha=base_sha or "initial",
            head_sha=target_head,
            files_added=len(files_added),
            files_modified=len(files_modified),
            files_deleted=len(files_deleted),
            files_skipped=len(files_skipped),
            sections_created=len(all_sections),
            duration_ms=int(duration_ms),
        )
