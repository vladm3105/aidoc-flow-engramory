#!/usr/bin/env python3
"""Automated knowledge documentation chunking & ingestion script (PLAN-005 Phase 3).

Crawls markdown and YAML specs/ADRs, semantically chunks them by headings to respect
the ambient memory token budget (200-400 tokens max), computes deterministic git evidence hashes,
and calls AccessSurface.knowledge_ingest() and Repository.set_kb_section_embedding().
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from engramory.access.surface import AccessSurface
from engramory.adapters.factory import dev_adapters
from engramory.cli import _close_adapters, _load_config
from engramory.core.chunking import ingest_file
from engramory.core.kb_graph import _resolve_sdd_root
from engramory.core.repository import Repository


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Ingest markdown documentation into Engramory.")
    parser.add_argument("--dir", default="docs/sdd/05_ADR", help="directory to crawl")
    parser.add_argument("--file", help="single file to ingest")
    parser.add_argument("--scope", default="project", help="visibility scope (project | global)")
    parser.add_argument("--config", help="explicit path to .engramory/config.toml")
    parser.add_argument("--no-embed", action="store_true", help="skip generating vector embeddings")
    args = parser.parse_args()

    config = _load_config(args.config)
    repo = Repository(config.dsn)
    surface = AccessSurface(repo)
    adapters = dev_adapters(config.dsn)
    try:
        target_files: list[Path] = []
        if args.file:
            target_files.append(Path(args.file))
        else:
            crawl_dir = _resolve_sdd_root(args.dir)
            if not crawl_dir.exists():
                print(f"Directory not found: {crawl_dir}", file=sys.stderr)
                return 1
            for ext in ("*.md", "*.yaml", "*.yml"):
                target_files.extend(crawl_dir.glob(ext))

        total_sections = 0
        for p in sorted(target_files):
            try:
                cnt = await ingest_file(
                    p,
                    surface,
                    repo,
                    config.ctx,
                    scope=args.scope,
                    embed=not args.no_embed,
                    adapters=adapters,
                )
                print(f"Ingested {cnt} section(s) from {p.name}")
                total_sections += cnt
            except Exception as exc:
                print(f"Error ingesting {p}: {exc}", file=sys.stderr)

        print(f"Done: {total_sections} total sections ingested into kb_sections.")
        return 0
    finally:
        await _close_adapters(adapters)


def main() -> None:
    try:
        sys.exit(asyncio.run(main_async()))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
