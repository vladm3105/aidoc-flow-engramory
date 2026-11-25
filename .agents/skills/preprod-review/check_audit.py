#!/usr/bin/env python3
"""Canonical standalone CLI check for audit disposition ledgers (preprod-review skill)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _ensure_harness_importable() -> None:
    try:
        import aiops_harness  # noqa: F401
        return
    except ImportError:
        pass
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "aiops_harness" / "__init__.py").is_file():
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            return


def main(argv: list[str] | None = None) -> int:
    _ensure_harness_importable()
    from aiops_harness.governance.audit_disposition import check_audit_disposition

    parser = argparse.ArgumentParser(
        description="Validate docs/audits/*.md disposition ledgers and [I-09] waiver independence."
    )
    parser.add_argument("--root", type=Path, default=Path("."), help="Repository root directory (default: '.')")
    parser.add_argument(
        "--verify-paperclip",
        action="store_true",
        help="Verify pc-task-* targets live against platform-paperclip",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON output")
    parser.add_argument("files", nargs="*", type=Path, help="Optional specific audit Markdown file(s)")
    args = parser.parse_args(argv)

    report = check_audit_disposition(
        args.root,
        files=args.files or None,
        verify_paperclip=args.verify_paperclip,
    )
    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"audit-disposition: {report['status']} "
            f"(files={len(report['files_checked'])}, findings={report['findings_total']}, "
            f"errors={len(report['errors'])})"
        )
        for err in report["errors"]:
            print(f"ERROR: {err}", file=sys.stderr)
        for warn in report["warnings"]:
            print(f"WARN: {warn}", file=sys.stderr)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
