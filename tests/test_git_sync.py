"""Unit tests for commit-differential incremental Git repository sync engine (PLAN-010 Phase 4)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from engramory.core.git_sync import FileChangeStatus, GitSyncEngine


def _git(cmd: list[str], cwd: Path) -> str:
    res = subprocess.run(["git"] + cmd, cwd=str(cwd), capture_output=True, text=True, check=True)
    return res.stdout.strip()


def test_git_sync_incremental_diff(tmp_path: Path) -> None:
    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()

    # Initialize a temporary git repository
    _git(["init", "-b", "main"], cwd=repo_dir)
    _git(["config", "user.name", "Tester"], cwd=repo_dir)
    _git(["config", "user.email", "tester@example.com"], cwd=repo_dir)

    # 1. First commit: add doc1.md and doc2.md
    f1 = repo_dir / "doc1.md"
    f1.write_text("# Document One\nInitial content.", encoding="utf-8")
    f2 = repo_dir / "doc2.md"
    f2.write_text("# Document Two\nInitial content.", encoding="utf-8")

    _git(["add", "."], cwd=repo_dir)
    _git(["commit", "-m", "initial commit"], cwd=repo_dir)
    base_sha = _git(["rev-parse", "HEAD"], cwd=repo_dir)

    # 2. Second commit: modify doc1.md, add doc3.md, delete doc2.md
    f1.write_text("# Document One\nModified content.", encoding="utf-8")
    f3 = repo_dir / "doc3.md"
    f3.write_text("# Document Three\nNew content.", encoding="utf-8")
    f2.unlink()

    _git(["add", "-A"], cwd=repo_dir)
    _git(["commit", "-m", "second commit"], cwd=repo_dir)
    head_sha = _git(["rev-parse", "HEAD"], cwd=repo_dir)

    engine = GitSyncEngine()
    changed = engine.get_changed_files(repo_dir, base_sha=base_sha, head_sha=head_sha)

    status_map = {c.rel_path: c.status for c in changed}
    assert status_map.get("doc1.md") == FileChangeStatus.MODIFIED
    assert status_map.get("doc3.md") == FileChangeStatus.ADDED
    assert status_map.get("doc2.md") == FileChangeStatus.DELETED

    # 3. Test section extraction
    sections = engine.extract_sync_sections(
        repo_dir,
        changed_file=[c for c in changed if c.rel_path == "doc3.md"][0],
        project_id="test-proj",
        head_sha=head_sha,
        version=1,
    )
    assert len(sections) == 1
    assert sections[0].doc_id == "test-proj:doc3.md"
    assert sections[0].project_id == "test-proj"
    assert sections[0].version == 1
    assert "doc3.md#L" in sections[0].citation
