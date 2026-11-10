"""Tests for memory-hygiene lint.py. Drives the script via subprocess: it
executes and exits at module level, so importing it would kill pytest."""

import glob
import subprocess
import sys
from pathlib import Path

LINT = Path(__file__).resolve().parents[1] / "lint.py"


def run_lint(store, *args):
    return subprocess.run(
        [sys.executable, str(LINT), str(store), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def write(store, name, body):
    path = store / name
    path.write_text(body, encoding="utf-8")
    return path


def test_clean_store_exits_zero(tmp_path):
    write(tmp_path, "note.md", "---\nname: note\n---\nbody\n")
    write(tmp_path, "MEMORY.md", "- [Title](note.md) — hook\n")
    proc = run_lint(tmp_path)
    assert proc.returncode == 0, proc.stdout
    assert "clean" in proc.stdout


def test_name_drift_fails(tmp_path):
    write(tmp_path, "a.md", "---\nname: b\n---\nbody\n")
    write(tmp_path, "MEMORY.md", "- [Title](a.md) — hook\n")
    proc = run_lint(tmp_path)
    assert proc.returncode == 1
    assert "name == filename-stem" in proc.stdout


def test_unresolvable_link_fails(tmp_path):
    write(tmp_path, "note.md", "---\nname: note\n---\nsee [[ghost]]\n")
    write(tmp_path, "MEMORY.md", "- [Title](note.md) — hook\n")
    proc = run_lint(tmp_path)
    assert proc.returncode == 1
    assert "ghost" in proc.stdout


def test_content_in_index_fails(tmp_path):
    write(
        tmp_path,
        "MEMORY.md",
        "line one\nline two\nline three\nline four\nline five\n",
    )
    proc = run_lint(tmp_path)
    assert proc.returncode == 1
    assert "not content" in proc.stdout


def test_missing_index_fails(tmp_path):
    write(tmp_path, "note.md", "---\nname: note\n---\nbody\n")
    proc = run_lint(tmp_path)
    assert proc.returncode == 1
    assert "no MEMORY.md" in proc.stdout


def test_fix_rewrites_link_and_backs_up(tmp_path):
    write(tmp_path, "my_note.md", "---\nname: my_note\n---\nbody\n")
    write(tmp_path, "idx.md", "---\nname: idx\n---\nsee [[my-note]]\n")
    write(
        tmp_path,
        "MEMORY.md",
        "- [A](my_note.md) — hook\n- [B](idx.md) — hook\n",
    )
    proc = run_lint(tmp_path, "--fix")
    assert proc.returncode == 0, proc.stdout
    assert "[[my_note]]" in (tmp_path / "idx.md").read_text(encoding="utf-8")
    assert glob.glob(str(tmp_path / ".backups" / "lint-fix-*.tar.gz"))


def test_fix_leaves_ambiguous_alias_unresolved(tmp_path):
    # Two memories share the drifted name "shared": the alias is ambiguous.
    write(tmp_path, "first.md", "---\nname: shared\n---\nbody\n")
    write(tmp_path, "second.md", "---\nname: shared\n---\nbody\n")
    write(tmp_path, "idx.md", "---\nname: idx\n---\nsee [[shared]]\n")
    write(
        tmp_path,
        "MEMORY.md",
        "- [A](first.md) — hook\n- [B](second.md) — hook\n- [C](idx.md) — hook\n",
    )
    proc = run_lint(tmp_path, "--fix")
    assert "[[shared]]" in (tmp_path / "idx.md").read_text(encoding="utf-8")
    assert proc.returncode == 1, proc.stdout
    assert "shared" in proc.stdout


def test_fix_never_touches_content_defect(tmp_path):
    write(tmp_path, "note.md", "---\nname: note\n---\nbody\n")
    before = "- [Title](note.md) — hook\nfact one\nfact two\nfact three\nfact four\nfact five\n"
    write(tmp_path, "MEMORY.md", before)
    proc = run_lint(tmp_path, "--fix")
    assert proc.returncode == 1
    assert (tmp_path / "MEMORY.md").read_text(encoding="utf-8") == before


def test_brief_always_exits_zero(tmp_path):
    write(tmp_path, "note.md", "---\nname: note\n---\nbody\n")
    proc = run_lint(tmp_path, "--brief")
    assert proc.returncode == 0
    assert len(proc.stdout.strip().splitlines()) == 1


def test_missing_store_skips(tmp_path):
    proc = run_lint(tmp_path / "nope")
    assert proc.returncode == 0
    assert "skipping" in proc.stdout
