#!/usr/bin/env python3
"""Validate tracked Claude skill and agent frontmatter with strict YAML."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_DESCRIPTION = 512


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("missing opening ---")
    # The block ends at the first `---` on its own line; without one, nothing is frontmatter.
    lines = text.split("\n")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("missing closing ---") from exc
    raw = "\n".join(lines[1:end])
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a mapping")
    return data


def validate(path: Path, kind: str) -> list[str]:
    errors: list[str] = []
    try:
        data = frontmatter(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return [f"{path}: invalid YAML frontmatter: {exc}"]

    if kind == "rule":
        paths = data.get("paths")
        if (
            not isinstance(paths, list)
            or not paths
            or not all(isinstance(p, str) and p for p in paths)
        ):
            errors.append(f"{path}: rule must declare a non-empty paths list")
        for key in data:
            if key != "paths":
                errors.append(f"{path}: unknown rule frontmatter key: {key!r}")
        return errors

    name = data.get("name")
    description = data.get("description")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        errors.append(f"{path}: invalid or missing name")
    if not isinstance(description, str) or not description.strip():
        errors.append(f"{path}: missing description")
    elif len(description) > MAX_DESCRIPTION:
        errors.append(
            f"{path}: description is {len(description)} chars; maximum is {MAX_DESCRIPTION}"
        )
    elif "\\n" in description:
        errors.append(f"{path}: description contains literal \\n escapes")

    if kind == "skill" and isinstance(name, str) and name != path.parent.name:
        errors.append(f"{path}: skill name must match directory {path.parent.name!r}")
    if kind == "agent" and data.get("model") != "inherit":
        errors.append(f"{path}: agents must declare model: inherit")
    allowed_tools = data.get("allowed-tools")
    if isinstance(allowed_tools, str):
        for command in re.findall(r"Bash\(([^)]*\*)\)", allowed_tools):
            if command != "*" and not command.endswith(":*"):
                errors.append(f"{path}: Bash wildcard must use command:* syntax: {command!r}")
    return errors


def _unmanaged_line_count(path: Path) -> int:
    count = 0
    skip = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line in ("<!-- aidoc-local-flow-ci:begin -->", "<!-- agents-canon:begin -->"):
            skip = True
            continue
        if line in ("<!-- aidoc-local-flow-ci:end -->", "<!-- agents-canon:end -->"):
            skip = False
            continue
        if not skip:
            count += 1
    return count


def main() -> int:
    files = [
        *((path, "agent") for path in sorted((ROOT / ".agents/agents").rglob("*.md"))),
        *((path, "skill") for path in sorted((ROOT / ".agents/skills").glob("*/SKILL.md"))),
        *((path, "rule") for path in sorted((ROOT / ".agents/rules").glob("*.md"))),
    ]
    errors = [error for path, kind in files for error in validate(path, kind)]
    instruction_lines = {
        name: _unmanaged_line_count(ROOT / name) for name in ("AGENTS.md",)
    }
    for name, line_count in instruction_lines.items():
        if line_count > 200:
            errors.append(f"{name}: {line_count} lines; target is at most 200")

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    counts = "; ".join(
        f"{name} {line_count} lines" for name, line_count in instruction_lines.items()
    )
    print(f"ok — {len(files)} frontmatter blocks; {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
