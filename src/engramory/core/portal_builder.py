"""Astro Starlight + Scalar portal bundle stager with Rule D14 secret redaction (PLAN-019).

Stages governed repository `docs/*.md` files into the Astro Starlight content collection
(`portal/src/content/docs/reference`), enforcing workspace/repo path confinement, symlink escape
prevention, synthesized YAML frontmatter escaping, and fail-closed Rule D14 secret & P14 Unicode
scrubbing prior to writing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engramory.core.docs_tools import (
    _is_sdd_doc_path,
    _is_within_root,
    _scrub_manifest_text,
    parse_markdown_document,
    resolve_confined_repo_path,
)
from engramory.core.models import ValidationError


def _synthesize_starlight_frontmatter(raw_text: str, *, fallback_stem: str) -> str:
    """Ensure Markdown has YAML frontmatter with JSON-escaped `title` and `description`."""
    stripped = raw_text.lstrip()
    if stripped.startswith("---\n") or stripped.startswith("---\r\n"):
        return raw_text

    resolved_title = fallback_stem
    for line in raw_text.splitlines():
        if line.startswith("# "):
            resolved_title = line[2:].strip() or fallback_stem
            break

    safe_title = json.dumps(resolved_title)
    safe_desc = json.dumps(f"Governed documentation reference for {resolved_title}.")
    return f"---\ntitle: {safe_title}\ndescription: {safe_desc}\n---\n\n{raw_text}"


def stage_portal_bundle(
    repo_root: Path,
    *,
    output_dir: Path | None = None,
    dry_run: bool = False,
    allowed_root: Path | None = None,
) -> dict[str, Any]:
    """Stage governed `docs/*.md` files into a Starlight content collection with D14 scrubbing."""
    try:
        confined_repo = resolve_confined_repo_path(repo_root, allowed_root=allowed_root)
    except ValidationError as exc:
        raise ValidationError(f"Repository path escapes allowed boundary: {repo_root}") from exc

    if output_dir is None:
        target_dir = (confined_repo / "portal" / "src" / "content" / "docs" / "reference").resolve()
        if not _is_within_root(target_dir, confined_repo):
            raise ValidationError(f"Output directory escapes allowed boundary: {target_dir}")
    else:
        try:
            target_dir = resolve_confined_repo_path(output_dir, allowed_root=allowed_root)
        except ValidationError as exc:
            raise ValidationError(
                f"Output directory escapes allowed boundary: {output_dir}"
            ) from exc

    docs_dir = confined_repo / "docs"
    scan_root = docs_dir if docs_dir.is_dir() else confined_repo
    raw_md_files = sorted(scan_root.rglob("*.md")) if scan_root.is_dir() else []
    md_files = [f for f in raw_md_files if not _is_sdd_doc_path(f, confined_repo)]

    staged_files: list[str] = []
    scrubbed_count = 0

    for md_file in md_files:
        # Prevent recursive self-staging if target_dir sits inside scan_root
        if _is_within_root(md_file, target_dir):
            continue
        if not _is_within_root(md_file, confined_repo):
            raise ValidationError(f"Markdown file escapes repository boundary: {md_file}")

        parsed = parse_markdown_document(md_file, repo_root=confined_repo)
        if parsed.kb_document is not None and parsed.kb_document.status in {
            "deprecated",
            "archived",
        }:
            continue
        fm_status = str(parsed.frontmatter.get("status", "active")).lower()
        if fm_status in {"deprecated", "archived"}:
            continue

        raw_text = md_file.read_text(encoding="utf-8")
        with_fm = _synthesize_starlight_frontmatter(raw_text, fallback_stem=md_file.stem)
        clean_text = _scrub_manifest_text(with_fm)
        if clean_text != with_fm:
            scrubbed_count += 1

        rel_under_docs = md_file.relative_to(scan_root)
        dest = target_dir / rel_under_docs
        if dest.is_symlink() or not _is_within_root(dest, target_dir):
            raise ValidationError(f"Destination path escapes output boundary: {dest}")

        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(clean_text, encoding="utf-8")
        staged_files.append(str(rel_under_docs))

    return {
        "status": "ok",
        "repo": confined_repo.name,
        "output_dir": str(target_dir),
        "dry_run": dry_run,
        "files_staged": len(staged_files),
        "staged_count": len(staged_files),
        "scrubbed_files": scrubbed_count,
        "staged_paths": staged_files,
        "staged_files": staged_files,
        "portal_framework": "@astrojs/starlight",
        "api_playground": "@scalar/api-reference",
    }
