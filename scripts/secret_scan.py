"""Fast pre-flight secret pattern scanner.

Scans directories for high-entropy credential patterns (Private keys, AWS access keys,
Model API keys, GitHub tokens, and sensitive environment assignments) before commit or push.
Complements gitleaks with zero external binary dependencies.

Scope: inside a git worktree only tracked + untracked non-ignored files are scanned
(git-ignored local files such as .env are never scanned); outside git, the whole tree
minus SKIP_DIRS. The scanner never scans itself. Intentional dummy credentials are
exempted per line with a trailing `secret-scan:allow` comment, or per path/line in a
`.secret-scan-allow` file at the tree root (`path` or `path:line`, `#` comments).

A finding carries a PATH and a LINE and never the matched secret text, preventing leaks
in CI logs or terminal output.
"""

import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = frozenset({".venv", ".git", "__pycache__", "node_modules", ".mypy_cache", "dist", "build"})

SKIP_PATH_PREFIXES = ("archive/", "archive")


def _is_skipped(path: Path, tree: Path) -> bool:
    try:
        rel = path.relative_to(tree).as_posix()
    except ValueError:
        return False
    return rel in SKIP_PATH_PREFIXES or rel.startswith(
        tuple(p if p.endswith("/") else p + "/" for p in SKIP_PATH_PREFIXES)
    )


@dataclass(frozen=True)
class SecretFormat:
    name: str
    pattern: re.Pattern[str]


PRIVATE_KEY = SecretFormat(
    "private_key",
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
AWS_ACCESS_KEY_ID = SecretFormat("aws_access_key_id", re.compile(r"AKIA[0-9A-Z]{16}"))
ANTHROPIC_API_KEY = SecretFormat("anthropic_api_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"))
OPENAI_API_KEY = SecretFormat("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"))
GITHUB_PAT = SecretFormat("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{36}\b"))
SLACK_TOKEN = SecretFormat("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z]{10,}\b"))

DEFAULT_SECRET_ENV_KEYS = (
    "API_KEY",
    "SECRET_KEY",
    "PRIVATE_KEY",
    "AUTH_TOKEN",
    "ACCESS_TOKEN",
    "ENCRYPTION_KEY",
)

SECRET_ENV_ASSIGNMENT = SecretFormat(
    "secret_env_assignment",
    re.compile(
        r"^\s*(?:export\s+)?(?:"
        + "|".join(DEFAULT_SECRET_ENV_KEYS)
        + r")\s*=\s*(?![\"']?\s*(?:\$|#|$))[\"']?[^\s\"'#${\[]"
    ),
)

SECRET_ENV_YAML_ASSIGNMENT = SecretFormat(
    "secret_env_yaml_assignment",
    re.compile(
        r"\b(?:"
        + "|".join(DEFAULT_SECRET_ENV_KEYS)
        + r")\s*:(?!-)\s+(?![\"']?\s*(?:\$|#|$))[\"']?[^\s\"'#${\[]"
    ),
)

DEFAULT_FORMATS = (
    PRIVATE_KEY,
    AWS_ACCESS_KEY_ID,
    ANTHROPIC_API_KEY,
    OPENAI_API_KEY,
    GITHUB_PAT,
    SLACK_TOKEN,
    SECRET_ENV_ASSIGNMENT,
    SECRET_ENV_YAML_ASSIGNMENT,
)


class SecretMaterialFound(RuntimeError):
    """A match anywhere in the scanned trees fails the check."""


@dataclass(frozen=True)
class Finding:
    format: str
    path: str
    line: int


@dataclass(frozen=True)
class ScanReport:
    trees_scanned: tuple[str, ...]
    formats_matched: tuple[str, ...]
    findings_detail: tuple[Finding, ...]

    @property
    def findings(self) -> int:
        return len(self.findings_detail)


SELF_PATH = Path(__file__).resolve()
ALLOW_MARKER = "secret-scan:allow"
ALLOW_FILE = ".secret-scan-allow"


def _git_files(tree: Path) -> list[Path] | None:
    """Tracked + untracked non-ignored files, or None outside a git worktree.

    Git-ignored files (.env and other local-only secrets) are never scanned:
    they cannot be committed, so flagging them only blocks legitimate pushes.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(tree), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    paths = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        paths.append(tree / os.fsdecode(raw))
    return paths


def _allowlist(tree: Path) -> set[tuple[str, int | None]]:
    """Entries from <tree>/.secret-scan-allow: `path` or `path:line`, # comments."""
    entries: set[tuple[str, int | None]] = set()
    try:
        lines = (tree / ALLOW_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return entries
    for line in lines:
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        path, sep, lineno = entry.rpartition(":")
        if sep and lineno.isdigit() and path:
            entries.add((path, int(lineno)))
        else:
            entries.add((entry, None))
    return entries


def _scannable(tree: Path) -> list[Path]:
    candidates = _git_files(tree)
    if candidates is None:
        candidates = list(tree.rglob("*"))
    return [
        path
        for path in candidates
        if path.is_file()
        and path.resolve() != SELF_PATH
        and not SKIP_DIRS.intersection(path.relative_to(tree).parts)
        and not _is_skipped(path, tree)
    ]


class SecretScan:
    @staticmethod
    def run(
        trees: tuple[Path, ...], formats: tuple[SecretFormat, ...] = DEFAULT_FORMATS
    ) -> ScanReport:
        findings: list[Finding] = []
        for tree in trees:
            allowed = _allowlist(tree)
            for path in _scannable(tree):
                try:
                    rel = path.relative_to(tree).as_posix()
                except ValueError:
                    rel = str(path)
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for number, line in enumerate(text.splitlines(), start=1):
                    if ALLOW_MARKER in line:
                        continue
                    if (rel, None) in allowed or (rel, number) in allowed:
                        continue
                    findings.extend(
                        Finding(fmt.name, str(path), number)
                        for fmt in formats
                        if fmt.pattern.search(line)
                    )
        return ScanReport(
            trees_scanned=tuple(str(tree) for tree in trees),
            formats_matched=tuple(fmt.name for fmt in formats),
            findings_detail=tuple(findings),
        )

    @staticmethod
    def enforce(
        trees: tuple[Path, ...], formats: tuple[SecretFormat, ...] = DEFAULT_FORMATS
    ) -> ScanReport:
        """Run the scan and fail on any match."""
        report = SecretScan.run(trees=trees, formats=formats)
        if report.findings:
            locations = ", ".join(
                f"{finding.path}:{finding.line} ({finding.format})"
                for finding in report.findings_detail
            )
            raise SecretMaterialFound(f"Secret material found: {locations}")
        return report


def main(argv: Sequence[str] | None = None) -> int:
    """Pre-flight entrypoint: `python3 scripts/secret_scan.py <tree>...`."""
    raw_args = sys.argv[1:] if argv is None else list(argv)
    if not raw_args:
        trees = (Path("."),)
    else:
        trees = tuple(Path(arg) for arg in raw_args)
    try:
        report = SecretScan.enforce(trees=trees)
    except SecretMaterialFound as found:
        print(str(found), file=sys.stderr)
        return 1
    print(
        f"secret scan: 0 findings over {', '.join(report.trees_scanned)} "
        f"with formats {', '.join(report.formats_matched)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
