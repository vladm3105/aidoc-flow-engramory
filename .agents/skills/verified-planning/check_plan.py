#!/usr/bin/env python3
"""Gate a PLAN-*.md: Claim-ledger citations must resolve; Review log must be structured.

stdlib only. Exit 0 = ok, 1 = failures (reported on stdout), 2 = usage error.

Usage:
  check_plan.py [--v2] [--hash-tasks] [--fix] [--root DIR ...] <PLAN.md> [more...]   # gate the plan(s)
  check_plan.py --init [--v2] <PLAN.md> [more...]                                    # scaffold ledger + review sections

--root adds an extra directory to resolve citations against (for cross-repo
claims); citations are tried against the plan's own repo root first, then each
--root in order.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import ntpath
import re
import shlex
import sys
from pathlib import Path

# Paths may contain spaces ("my dir/f.py"); the inclusive pattern resolves
# them whole instead of matching a suffix. STRICT is the fallback for prose
# prefixes ("see src/a.py:3") — see the retry below. Group numbering is
# identical in both.
CITATION_RE = re.compile(r"([\w./\-][\w./\- ]*):(\d+)(?:-(\d+))?")
CITATION_RE_STRICT = re.compile(r"([\w./\-]+):(\d+)(?:-(\d+))?")
PROXIMITY = 3  # lines; tunable window for the symbol check
PASS_RE = re.compile(r"^###\s+Pass\s+(\d+)\b(.*)$", re.MULTILINE)
# A final pass is "green" if it carries any of these. Deliberately forgiving on
# adjectives ("no <anything> findings") plus an explicit Result/Status marker, so
# a genuinely-clean pass is never rejected over wording.
ZERO_FINDINGS_RE = re.compile(
    # bounded adjective list (not "no <anything> findings", which would false-match
    # "no fix for these findings") + an explicit Result/Status marker
    r"\bno (?:new |further |remaining |outstanding |open |load-bearing |additional )?findings\b"
    r"|\bzero findings\b"
    r"|findings:\s*none"
    # the "nothing ..." phrasing requires its findings-object ("found"): bare
    # "nothing further" also matches "nothing further to add, but 3 remain open"
    r"|nothing (?:new|further) found\b"
    r"|(?:result|status):\s*\**\s*(?:ready|green|clean|done|no findings)",
    re.IGNORECASE,
)

# Standalone plan-gate directive line (outside fenced code blocks & inline backticks).
GATE_DIRECTIVE_RE = re.compile(r"^\s*<!--\s*plan-gate:\s*(v[12])\s*-->\s*$", re.IGNORECASE)
# Delimited optional v2 snippet markers in PLAN-TEMPLATE.snippet.md.
V2_SNIPPET_RE = re.compile(
    r"\n*<!--\s*plan-gate-v2-snippet:begin\s*-->\s*(.*?)\s*<!--\s*plan-gate-v2-snippet:end\s*-->\s*",
    re.DOTALL,
)
# Standalone final-pass disposition declaration in v2 mode.
DISPOSITION_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?\*\*Disposition:\*\*(.*)$", re.MULTILINE | re.IGNORECASE)
SPLIT_TO_CHAIN_RE = re.compile(r"^SPLIT_TO_CHAIN\s*\(\s*(CHG-0*[1-9]\d*)\s*\)$", re.IGNORECASE)
ESCALATE_RE = re.compile(r"^ESCALATE$", re.IGNORECASE)

# Pre-CITATION_RE delimited absolute/drive/UNC rejection patterns.
_PREFIX_DELIM = r"(?:^|[\s`\"'(<\[\{])"
_ABS_DRIVE_RE = re.compile(_PREFIX_DELIM + r"[A-Za-z]:\S")
_ABS_UNC_RE = re.compile(_PREFIX_DELIM + r"(?:\\\\|//)\S")
_ABS_POSIX_RE = re.compile(_PREFIX_DELIM + r"/[^\s\n|`\"][^\n|`\"]*?:\d+")
_ABS_BACKSLASH_ROOT_RE = re.compile(_PREFIX_DELIM + r"\\[^\s\n|`\"][^\n|`\"]*?:\d+")

_VALID_TASK_STATUSES = frozenset({"LOCKED", "FIX", "STALE", "PROBE", "REDESIGN"})
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_ZERO_HEX64 = "0" * 64
_REQUIRED_CONVERGENCE_HEADERS = ("task id", "sha-256", "p1", "p2", "p3", "p4", "total", "status")
_TASK_PASS_CAP_V2 = 3

# Link-0 read-only probe command sandbox constants (mirrors plan_convergence.py:_validate_readonly_probe_command)
READ_ONLY_PROBE_EXECUTABLES: frozenset[str] = frozenset({"pytest", "ruff", "mypy", "true", "echo", "test", "git"})
READ_ONLY_GIT_SUBCOMMANDS: frozenset[str] = frozenset({"status", "diff", "log", "rev-parse", "show", "ls-files"})
_READ_ONLY_RUFF_SUBCOMMANDS: frozenset[str] = frozenset({"check", "version", "rule"})
_FORBIDDEN_SHELL_META: tuple[str, ...] = ("&&", "||", ";", "|", ">", ">>", "<", "`", "$(", "\n", "\r")
_FORBIDDEN_LONG_PROBE_FLAGS: frozenset[str] = frozenset(
    {
        "--fix",
        "--unsafe-fixes",
        "--add-noqa",
        "--clean",
        "--output-file",
        "--config-file",
        "--config",
        "--cache-dir",
        "--rootdir",
        "--override-ini",
        "--basetemp",
        "--junitxml",
        "--junit-xml",
        "--result-log",
        "--log-file",
        "--html",
        "--cov-report",
        "--work-tree",
        "--git-dir",
        "--exec-path",
        "--upload-pack",
        "--receive-pack",
        "--output",
    }
)
_FORBIDDEN_SHORT_CHARS_GLOBAL: frozenset[str] = frozenset({"o", "c", "f"})
_FORBIDDEN_GIT_FLAGS: frozenset[str] = frozenset({"-C", "-c", "--work-tree", "--git-dir", "--exec-path", "--output"})
_FORBIDDEN_PYTEST_FLAGS: frozenset[str] = frozenset(
    {"-c", "-o", "-p", "--rootdir", "--override-ini", "--basetemp", "--junitxml", "--log-file"}
)
_FORBIDDEN_MYPY_FLAGS: frozenset[str] = frozenset({"--config-file", "--cache-dir"})

_PROBE_PREFIX_RE = re.compile(r"^PROBE(?:\[\s*([^\]]*?)\s*\])?\s*($|:)", re.IGNORECASE)
_VOLATILE_COUNTER_SYMBOL_RE = re.compile(r"\b(?:total_changes|total_plans)\s*:\s*\d+\b", re.IGNORECASE)
_PROSE_CLAIM_REF_RE = re.compile(r"\bClaims?\s+(\d+[a-zA-Z]?)(?:\s*(?:[–—-]|\.\.|to)\s*(\d+[a-zA-Z]?))?\b")
_NEW_AT_PASS_RE = re.compile(r"\bNEW@pass(\d+)\b", re.IGNORECASE)
_PROVENANCE_TOKEN_RE = re.compile(
    r"(?:[0-9a-f]{8}-[0-9a-f]{4}|verified-planning-reviewer|subagent|reviewer|lenses?:|google|anthropic|aws|microsoft)",
    re.IGNORECASE,
)
_SCORECARD_EXPECTED_WEIGHTS: tuple[float, ...] = (25.0, 25.0, 20.0, 15.0, 15.0)

SNIPPET = Path(__file__).resolve().parent / "PLAN-TEMPLATE.snippet.md"


def _strip_fenced_blocks(text: str) -> str:
    """Replace lines inside ``` or ~~~ fenced code blocks with empty lines."""
    out: list[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    for line in text.splitlines():
        stripped = line.lstrip()
        m_fence = re.match(r"^(?: {0,3})(`{3,}|~{3,})(.*)$", line)
        if not in_fence:
            if m_fence:
                fence_tok = m_fence.group(1)
                fence_char = fence_tok[0]
                fence_len = len(fence_tok)
                # CommonMark forbids backticks in info strings of backtick fences
                if fence_char == "`" and "`" in m_fence.group(2):
                    out.append(line)
                    continue
                in_fence = True
                out.append("")
                continue
            out.append(line)
        else:
            if (
                stripped
                and stripped[0] == fence_char
                and len(stripped.rstrip()) >= fence_len
                and set(stripped.rstrip()) <= {fence_char}
            ):
                in_fence = False
                fence_char = ""
                fence_len = 0
            out.append("")
    return "\n".join(out)


def _strip_inline_code(text: str) -> str:
    """Strip inline backtick code spans (`...`) from single lines to avoid matching quoted literals."""
    return re.sub(r"`[^`\n]+`", "", text)


def _extract_leading_float(cell: str) -> float | None:
    """Extract the leading finite float from a Markdown-formatted table cell (e.g. '**24** *(Rationale)*')."""
    cleaned = re.sub(r"[*_`~]", "", cell).strip()
    m = re.search(r"[-+]?\d+(?:\.\d+)?", cleaned)
    if not m:
        return None
    try:
        val = float(m.group(0))
    except ValueError:
        return None
    return val if math.isfinite(val) else None


def _check_probe_path_token(tok: str) -> str | None:
    """Return an error reason if a probe argument token is absolute, drive-rooted, UNC, or traverses '..'."""
    cleaned = str(tok or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith(("/", "\\", "~")) or re.match(r"^[A-Za-z]:", cleaned):
        return f"non-relative path argument {cleaned!r}"
    norm = cleaned.replace("\\", "/")
    parts = [p for p in norm.split("/") if p]
    if ".." in parts:
        return f"parent directory traversal '..' in {cleaned!r}"
    return None


def classify_probe_command(command: str) -> tuple[str, str | None]:
    """Classify a PROBE command as ('link0', None) if it satisfies the Link-0 read-only sandbox,
    or ('experiment', reason) if it requires an external/throwaway environment.
    """
    cmd = str(command or "").strip()
    if not cmd:
        return "experiment", "empty probe command"
    if any(tok in cmd for tok in _FORBIDDEN_SHELL_META):
        return "experiment", f"contains shell metacharacters ({cmd!r})"
    try:
        tokens = shlex.split(cmd)
    except ValueError as exc:
        return "experiment", f"unparseable command ({exc})"
    if not tokens:
        return "experiment", "empty command tokens"
    exe_tok = tokens[0]
    if any(sep in exe_tok for sep in ("/", "\\", ":")) or exe_tok not in READ_ONLY_PROBE_EXECUTABLES:
        return (
            "experiment",
            f"executable {exe_tok!r} is not in Link-0 read-only allowlist ({', '.join(sorted(READ_ONLY_PROBE_EXECUTABLES))})",
        )
    args = tokens[1:]
    sep_idx = args.index("--") if "--" in args else len(args)
    flag_args = args[:sep_idx]

    for tok in flag_args:
        flag = tok.split("=", 1)[0]
        if "=" in tok:
            path_err = _check_probe_path_token(tok.split("=", 1)[1])
            if path_err:
                return "experiment", path_err
        if flag in _FORBIDDEN_LONG_PROBE_FLAGS:
            return "experiment", f"forbids flag {tok!r}"
        if tok.startswith("-") and not tok.startswith("--"):
            short_body = tok[1:].split("=", 1)[0]
            if any(ch in _FORBIDDEN_SHORT_CHARS_GLOBAL for ch in short_body):
                return "experiment", f"forbids short flag {tok!r}"
            if exe_tok == "git" and "C" in short_body:
                return "experiment", f"forbids git flag {tok!r}"
            if exe_tok == "pytest" and "p" in short_body:
                return "experiment", f"forbids pytest plugin flag {tok!r}"
        if exe_tok == "git" and flag in _FORBIDDEN_GIT_FLAGS:
            return "experiment", f"forbids git flag {tok!r}"
        if exe_tok == "pytest" and flag in _FORBIDDEN_PYTEST_FLAGS:
            return "experiment", f"forbids pytest flag {tok!r}"
        if exe_tok == "mypy" and flag in _FORBIDDEN_MYPY_FLAGS:
            return "experiment", f"forbids mypy flag {tok!r}"
        if not tok.startswith("-"):
            path_err = _check_probe_path_token(tok)
            if path_err:
                return "experiment", path_err

    for tok in args[sep_idx + 1 :]:
        path_err = _check_probe_path_token(tok)
        if path_err:
            return "experiment", path_err

    if exe_tok == "git":
        sub = next((tok for tok in flag_args if not tok.startswith("-")), "")
        if sub not in READ_ONLY_GIT_SUBCOMMANDS:
            return "experiment", f"git subcommand {sub!r} is not read-only"
    elif exe_tok == "ruff":
        sub = next((tok for tok in flag_args if not tok.startswith("-")), "")
        is_format_check = sub == "format" and ("--check" in flag_args or "--diff" in flag_args)
        if sub not in _READ_ONLY_RUFF_SUBCOMMANDS and not is_format_check:
            return "experiment", f"ruff subcommand {sub!r} is not read-only"

    return "link0", None


def repo_root(start: Path) -> Path:
    p = start.resolve()
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return d
    # No checkout (tarball, stripped CI): anchor on the plan's own directory,
    # never the invocation directory — the same command must gate identically
    # from anywhere.
    return p.parent if p.is_file() else Path.cwd()


def sections(text: str) -> dict[str, str]:
    """Map level-2 header (lowercased, trimmed) -> body up to the next level-2 header.

    Fenced code blocks (``` / ~~~) are ignored when detecting headers so `## ` lines inside
    fenced snippets never split or overwrite sections.
    """
    out: dict[str, str] = {}
    cur = None
    buf: list[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    for line in text.splitlines():
        m_fence = re.match(r"^(?: {0,3})(`{3,}|~{3,})(.*)$", line)
        if not in_fence and m_fence:
            tok = m_fence.group(1)
            if not (tok[0] == "`" and "`" in m_fence.group(2)):
                in_fence = True
                fence_char = tok[0]
                fence_len = len(tok)
        elif in_fence:
            stripped = line.lstrip().rstrip()
            if stripped and stripped[0] == fence_char and len(stripped) >= fence_len and set(stripped) <= {fence_char}:
                in_fence = False
                fence_char = ""
                fence_len = 0
        else:
            m = re.match(r"##\s+(.*\S)\s*$", line)
            if m and not line.startswith("###"):
                if cur is not None:
                    out[cur] = "\n".join(buf)
                cur = m.group(1).strip().lower()
                buf = []
                continue
        if cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf)
    return out


ROW_LABEL_RE = re.compile(r"^\**(\d+[a-zA-Z]?)\**$")


def _is_separator(cells: list[str]) -> bool:
    # A separator is dashes/colons, not emptiness: an all-empty pipe row is
    # malformed data and must gate as a row, not vanish silently.
    joined = "".join(cells)
    return bool(joined) and set(joined) <= set("-: ") and any(c in "-:" for c in joined)


def table_rows(body: str) -> list[tuple[str, list[str], bool]]:
    """Every pipe-table data row in ``body``: (label, cells, numbered)."""
    rows: list[tuple[str, list[str], bool]] = []
    numbered = False  # does the table currently being read declare a `#` column?
    in_table = False  # have we seen any pipe-row of the current table yet?
    position = 0
    lines = body.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if not s.startswith("|"):
            numbered = False
            in_table = False
            position = 0
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if _is_separator(cells):
            in_table = True
            continue
        if cells and cells[0].lower() in ("#", "no", "no."):  # header row
            numbered = True
            in_table = True
            continue
        if not in_table and not numbered:
            for nxt in lines[i + 1 :]:
                t = nxt.strip()
                if not t:
                    continue
                if t.startswith("|") and _is_separator(
                    [c.strip() for c in t.strip("|").split("|")]
                ):
                    in_table = True
                    break
                in_table = True
                position += 1
                rows.append((str(position), cells, False))
                break
            else:
                in_table = True
                position += 1
                rows.append((str(position), cells, False))
            continue
        position += 1
        label = str(position)
        if numbered:
            m = ROW_LABEL_RE.match(cells[0])
            if m:
                label = m.group(1)
        rows.append((label, cells, numbered))
    return rows


def _is_abs_token(rel: str) -> bool:
    """True when a path token is absolute, drive-rooted, drive-relative, root-relative, or UNC."""
    if not rel:
        return False
    if ntpath.splitdrive(rel)[0]:
        return True
    if rel.startswith(("/", "\\")):
        return True
    return Path(rel).is_absolute()


def _has_absolute_citation(citation: str) -> bool:
    """Reject Windows drive, UNC, POSIX absolute, and backslash-root citations before regex slicing."""
    return bool(
        _ABS_DRIVE_RE.search(citation)
        or _ABS_UNC_RE.search(citation)
        or _ABS_POSIX_RE.search(citation)
        or _ABS_BACKSLASH_ROOT_RE.search(citation)
    )


def _resolve(rel: str, roots: list[Path]) -> Path | None:
    if _is_abs_token(rel):
        return None  # absolute/drive/UNC citations are rejected by the caller, not resolved
    for base in roots:
        target = (base / rel).resolve()
        try:
            target.relative_to(base.resolve())
        except ValueError:
            continue
        if target.exists():
            return target
    return None


def check_ledger(
    body: str, roots: list[Path]
) -> tuple[
    list[str],
    list[str],
    list[tuple[str, str, int, int]],
    list[tuple[int | str, str]],
    list[dict[str, str]],
]:
    """Return (errors, warnings, drifts, probes, probe_details)."""
    errs: list[str] = []
    warns: list[str] = []
    drifts: list[tuple[str, str, int, int]] = []
    probes: list[tuple[int | str, str]] = []
    probe_details: list[dict[str, str]] = []
    rows = table_rows(body)
    if not rows:
        return ["Claim ledger has no rows"], warns, drifts, probes, probe_details
    for i, cells, numbered in rows:
        citation = cells[-1]
        symbol = cells[-2].strip("`").strip() if len(cells) >= 2 else ""
        claim = cells[1] if (numbered and len(cells) >= 2) else cells[0]
        if symbol and _VOLATILE_COUNTER_SYMBOL_RE.search(symbol):
            warns.append(
                f"ledger row {i}: symbol '{symbol}' embeds a volatile registry counter — "
                "prefer stable YAML key 'total_changes:' or 'total_plans:'"
            )
        if not citation:
            errs.append(f"ledger row {i}: UNVERIFIED or missing citation")
            continue
        m_probe = _PROBE_PREFIX_RE.match(citation)
        if m_probe:
            if len(cells) < 2:
                errs.append(
                    f"ledger row {i}: PROBE row needs claim + citation cells — "
                    f"write '| <claim> — blocks Phase <N> | `n/a` | PROBE: <command> |'"
                )
                continue
            declared_subtype = (m_probe.group(1) or "").strip().lower()
            if declared_subtype and declared_subtype not in ("link0", "experiment"):
                errs.append(
                    f"ledger row {i}: invalid PROBE subtype 'PROBE[{m_probe.group(1)}]' — "
                    "expected 'PROBE[link0]: <readonly_cmd>', 'PROBE[experiment]: <cmd>', or 'PROBE: <cmd>'"
                )
            cmd = citation.split(":", 1)[1].strip() if ":" in citation else ""
            if not cmd:
                errs.append(
                    f"ledger row {i}: PROBE without a command — write "
                    f"'PROBE: <the command that settles it>'"
                )
            auto_kind, sandbox_err = classify_probe_command(cmd)
            if declared_subtype == "link0" and cmd and auto_kind != "link0":
                errs.append(
                    f"ledger row {i}: PROBE[link0] command violates Link-0 read-only sandbox ({sandbox_err})"
                )
            resolved_kind = declared_subtype if declared_subtype in ("link0", "experiment") else auto_kind
            if "block" not in claim.lower():
                errs.append(
                    f"ledger row {i}: PROBE claim does not say what it BLOCKS — "
                    f"an unmeasured claim must name the phase it gates, or it "
                    f"reads as merely unfinished"
                )
            m_blk = re.search(
                r"blocks?\s+\**\s*(Phase\s+[A-Z]\b|Phase\s+\d+\b|[A-Z]\d+[a-z]?\b)",
                claim,
                re.I,
            )
            if not m_blk and "block" in claim.lower():
                errs.append(
                    f"ledger row {i}: PROBE claim does not name a resolvable phase — "
                    f"write 'blocks Phase <N>' (or a B-ID like B1)"
                )
            blocked_target = m_blk.group(1).strip() if m_blk else ""
            probes.append((i, blocked_target))
            probe_details.append(
                {
                    "row": str(i),
                    "blocked": blocked_target,
                    "declared_subtype": declared_subtype,
                    "resolved_kind": resolved_kind,
                    "sandbox_err": sandbox_err or "",
                    "cmd": cmd,
                    "claim": claim,
                }
            )
            continue
        if "UNVERIFIED" in citation.upper():
            errs.append(f"ledger row {i}: UNVERIFIED or missing citation")
            continue
        if _has_absolute_citation(citation):
            errs.append(
                f"ledger row {i}: citation '{citation}' is absolute — cite paths relative to a root"
            )
            continue
        # Normalize relative Windows backslashes only after rejecting absolute/drive/UNC forms.
        citation_norm = citation.replace("\\", "/")
        m = CITATION_RE.search(citation_norm)
        if m and _resolve(m.group(1), roots) is None:
            m = CITATION_RE_STRICT.search(citation_norm)
        if not m:
            errs.append(f"ledger row {i}: citation '{citation}' is not path:line")
            continue
        rel, line = m.group(1), int(m.group(2))
        if _is_abs_token(rel):
            errs.append(
                f"ledger row {i}: citation '{rel}' is absolute — cite paths relative to a root"
            )
            continue
        target = _resolve(rel, roots)
        if target is None:
            where = ", ".join(str(r) for r in roots)
            errs.append(f"ledger row {i}: path '{rel}' does not exist under any root ({where})")
            continue
        if not target.is_file():
            errs.append(f"ledger row {i}: path '{rel}' is not a file")
            continue
        lines = target.read_text(errors="replace").splitlines()
        in_range = 1 <= line <= len(lines)
        if not symbol:
            if not in_range:
                errs.append(
                    f"ledger row {i}: line {line} out of range for {rel} ({len(lines)} lines) and no symbol to anchor"
                )
            continue
        window = (
            (chr(10)).join(lines[max(0, line - 1 - PROXIMITY) : line - 1 + PROXIMITY + 1])
            if in_range
            else ""
        )
        if symbol in window:
            continue
        hits = [n for n, ln in enumerate(lines, 1) if symbol in ln]
        if not hits:
            errs.append(f"ledger row {i}: symbol '{symbol}' not found in {rel}")
            continue
        nearest = min(hits, key=lambda n: abs(n - line))
        warns.append(
            f"ledger row {i}: symbol '{symbol}' is at {rel}:{nearest} (cited :{line}) — line drifted; citation passes"
        )
        if len(hits) == 1:
            drifts.append((symbol, rel, line, hits[0]))
    return errs, warns, drifts, probes, probe_details


def _detect_gate_mode(text: str, cli_v2: bool = False) -> tuple[bool, list[str]]:
    """Detect whether v2 convergence gating is active via CLI flag or standalone directive."""
    errs: list[str] = []
    stripped = _strip_fenced_blocks(text)
    versions: set[str] = set()
    for line in stripped.splitlines():
        m = GATE_DIRECTIVE_RE.fullmatch(line)
        if m:
            versions.add(m.group(1).lower())
    if len(versions) > 1:
        errs.append("conflicting standalone '<!-- plan-gate: ... -->' directives (both v1 and v2 present)")
    is_v2 = bool(cli_v2 or ("v2" in versions))
    return is_v2, errs


def _check_review_detail(body: str, *, is_v2: bool = False) -> tuple[list[str], dict[str, object]]:
    """Validate Review log structure and return (errors, metadata)."""
    errs: list[str] = []
    warns: list[str] = []
    stripped_body = _strip_fenced_blocks(body)
    passes = list(PASS_RE.finditer(stripped_body))
    meta: dict[str, object] = {
        "passes": len(passes),
        "has_independent": False,
        "has_zero_findings": False,
        "split_chg_id": None,
        "has_escalate": False,
        "warns": warns,
    }
    if len(passes) < 2:
        errs.append(f"Review log has {len(passes)} Pass entr(ies); need at least two")
        return errs, meta

    if is_v2 and len(passes) > _TASK_PASS_CAP_V2:
        errs.append(
            f"Review log has {len(passes)} Passes; v2 hard circuit breaker caps reviews at {_TASK_PASS_CAP_V2} passes max"
        )

    marked = [
        m
        for m in passes
        if re.search(r"(?<![\w-])(independent|fallback)\b", m.group(2), re.IGNORECASE)
    ]
    meta["has_independent"] = bool(marked)
    if not marked:
        errs.append("Review log has no pass marked 'independent'")
    elif is_v2:
        for idx, m in enumerate(passes):
            if m not in marked:
                continue
            start_pos = m.end()
            end_pos = passes[idx + 1].start() if idx + 1 < len(passes) else len(stripped_body)
            pass_chunk = stripped_body[start_pos:end_pos]
            if not _PROVENANCE_TOKEN_RE.search(pass_chunk):
                warns.append(
                    f"Review log Pass {m.group(1)} (independent): omits reviewer/subagent ID or selected lenses provenance"
                )

    final_body = stripped_body[passes[-1].end() :]
    has_zero = bool(ZERO_FINDINGS_RE.search(final_body))
    meta["has_zero_findings"] = has_zero

    if not is_v2:
        if not has_zero:
            errs.append(
                "final Pass does not state a zero-findings result (e.g. 'no new findings' or '**Result:** ready')"
            )
        return errs, meta

    # v2 disposition handling: fail closed on malformed, multiple, or ESCALATE declarations.
    disp_matches = list(DISPOSITION_LINE_RE.finditer(final_body))
    split_ids: list[str] = []
    escalates = 0
    invalid_disps: list[str] = []
    for dm in disp_matches:
        rhs = dm.group(1).strip()
        m_split = SPLIT_TO_CHAIN_RE.fullmatch(rhs)
        if m_split:
            split_ids.append(m_split.group(1).upper())
        elif ESCALATE_RE.fullmatch(rhs):
            escalates += 1
        else:
            invalid_disps.append(rhs)

    if invalid_disps:
        errs.append(
            f"final Pass has invalid '**Disposition:**' value {invalid_disps[0]!r} — "
            "expected 'SPLIT_TO_CHAIN (CHG-<N>)' or 'ESCALATE'"
        )
    if len(disp_matches) > 1:
        errs.append("final Pass declares multiple '**Disposition:**' lines — only one is permitted")
    if escalates:
        meta["has_escalate"] = True
        errs.append("Review convergence halted with '**Disposition:** ESCALATE' — requires human intervention")
    if len(split_ids) == 1 and not invalid_disps and len(disp_matches) == 1:
        meta["split_chg_id"] = split_ids[0]

    if not meta["split_chg_id"] and not has_zero and not escalates and not invalid_disps:
        errs.append(
            "final Pass does not state a zero-findings result (e.g. 'no new findings' or '**Result:** ready') "
            "or a valid '**Disposition:** SPLIT_TO_CHAIN (CHG-<N>)'"
        )
    return errs, meta


def check_review(body: str, *, is_v2: bool = False) -> list[str]:
    errs, _ = _check_review_detail(body, is_v2=is_v2)
    return errs


def _strip_excluded_hash_sections(text: str) -> str:
    """Remove ## Task convergence, ## Task scores, ## Claim ledger, and ## Review log sections from text."""
    excluded = {"task convergence", "task scores", "claim ledger", "review log"}
    out: list[str] = []
    in_excluded = False
    in_fence = False
    fence_char = ""
    fence_len = 0
    for line in text.splitlines():
        m_fence = re.match(r"^(?: {0,3})(`{3,}|~{3,})(.*)$", line)
        if not in_fence and m_fence:
            tok = m_fence.group(1)
            if not (tok[0] == "`" and "`" in m_fence.group(2)):
                in_fence = True
                fence_char = tok[0]
                fence_len = len(tok)
        elif in_fence:
            stripped = line.lstrip().rstrip()
            if stripped and stripped[0] == fence_char and len(stripped) >= fence_len and set(stripped) <= {fence_char}:
                in_fence = False
                fence_char = ""
                fence_len = 0
        else:
            m = re.match(r"^##\s+(.*\S)\s*$", line)
            if m and not line.startswith("###"):
                sec_name = m.group(1).strip().lower()
                in_excluded = sec_name in excluded
        if not in_excluded:
            out.append(line)
    return "\n".join(out)


def compute_markdown_task_sha256(task_id: str, plan_text: str) -> str:
    """Compute a deterministic Stage-1 SHA-256 hash over the task's body definition outside excluded sections.

    Returns '' when task_id has no body definition outside ## Task convergence (preserving compatibility
    with table-only unit test fixtures).
    """
    tid = str(task_id or "").strip()
    if not tid:
        return ""
    body_clean = _strip_excluded_hash_sections(plan_text)
    phases = re.split(r"(?=^###\s+Phase\s+|^##\s+)", body_clean, flags=re.M)
    tid_pat = re.compile(rf"(?:`{re.escape(tid)}`|\b{re.escape(tid)}\b)")
    for blk in phases:
        lines = blk.splitlines()
        if lines and lines[0].startswith("###") and tid_pat.search(lines[0]):
            canonical_blk = re.sub(r"(?:\n+\s*---\s*)+\Z", "", blk.strip()).strip()
            return hashlib.sha256(f"{tid}\n{canonical_blk}".encode()).hexdigest()

    # Fallback: non-table body lines outside excluded sections referencing task_id
    non_table_lines = [
        ln.strip()
        for ln in _strip_fenced_blocks(body_clean).splitlines()
        if not ln.strip().startswith("|") and tid_pat.search(ln)
    ]
    if non_table_lines:
        joined = "\n".join(non_table_lines)
        return hashlib.sha256(f"{tid}\n{joined}".encode()).hexdigest()
    return ""


def _parse_task_convergence_table(body: str) -> tuple[list[str], list[dict[str, str]]]:
    """Parse raw Markdown pipe-table headers and data rows for Task convergence."""
    lines = _strip_fenced_blocks(body).splitlines()
    i = 0
    n = len(lines)
    while i < n and not lines[i].strip().startswith("|"):
        i += 1
    if i >= n:
        return ["'## Task convergence' contains no pipe table"], []

    header_cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
    norm_headers = [re.sub(r"[`*_]", "", c).strip().lower() for c in header_cells]
    if i + 1 >= n or not lines[i + 1].strip().startswith("|"):
        return ["Task convergence table is missing header separator row"], []
    sep_cells = [c.strip() for c in lines[i + 1].strip().strip("|").split("|")]
    if not _is_separator(sep_cells):
        return ["Task convergence table second row must be a Markdown separator (`|---|`)"], []

    missing_cols = [col for col in _REQUIRED_CONVERGENCE_HEADERS if col not in norm_headers]
    if missing_cols:
        return [
            f"Task convergence table missing required column header(s): {', '.join(missing_cols)}"
        ], []
    if len(set(norm_headers)) != len(norm_headers):
        return ["Task convergence table contains duplicate column headers"], []

    col_idx = {h: idx for idx, h in enumerate(norm_headers)}
    parsed_rows: list[dict[str, str]] = []
    errs: list[str] = []
    j = i + 2
    while j < n:
        raw = lines[j].strip()
        if not raw.startswith("|"):
            break
        cells = [c.strip() for c in raw.strip("|").split("|")]
        if _is_separator(cells):
            j += 1
            continue
        if len(cells) < len(norm_headers):
            errs.append(f"Task convergence row {len(parsed_rows) + 1}: fewer cells than headers")
            j += 1
            continue
        row = {col: cells[idx].strip().strip("`").strip() for col, idx in col_idx.items()}
        parsed_rows.append(row)
        j += 1

    if not parsed_rows and not errs:
        errs.append("Task convergence table has no rows")
    return errs, parsed_rows


def check_task_convergence(
    text: str,
    secs: dict[str, str],
    *,
    has_independent: bool,
    has_zero_findings: bool,
    split_chg_id: str | None,
    probe_details: list[dict[str, str]] | None = None,
) -> tuple[list[str], float | None]:
    """Validate Task convergence table and enforce v2 lock/split readiness invariants."""
    errs: list[str] = []
    stripped_text = _strip_fenced_blocks(text)
    conv_headings = re.findall(r"^##\s+(?:task convergence|task scores)\s*$", stripped_text, re.M | re.I)
    if len(conv_headings) > 1:
        errs.append(
            "duplicate '## Task convergence' / '## Task scores' section — only one may exist"
        )
        return errs, None
    sec_body = secs.get("task convergence") if "task convergence" in secs else secs.get("task scores")
    if sec_body is None:
        errs.append("v2 gate requires a '## Task convergence' section")
        return errs, None

    parse_errs, rows = _parse_task_convergence_table(sec_body)
    if parse_errs:
        return parse_errs, None

    seen_ids: set[str] = set()
    statuses: list[tuple[str, str]] = []
    health_credits: list[float] = []

    for idx, row in enumerate(rows, 1):
        tid = row["task id"]
        if not tid:
            errs.append(f"Task convergence row {idx}: missing Task ID")
            continue
        if tid in seen_ids:
            errs.append(f"Task convergence row {idx}: duplicate Task ID '{tid}'")
            continue
        seen_ids.add(tid)

        sha = row["sha-256"]
        if sha == _ZERO_HEX64:
            errs.append(f"Task convergence '{tid}': all-zero dummy SHA-256 hash is forbidden")

        pillar_vals: list[float] = []
        bad_num = False
        for pkey in ("p1", "p2", "p3", "p4"):
            raw_p = row[pkey]
            try:
                val = float(raw_p)
            except ValueError:
                errs.append(f"Task convergence '{tid}': invalid {pkey.upper()} score {raw_p!r}")
                bad_num = True
                continue
            if not math.isfinite(val) or val < 0.0 or val > 5.0:
                errs.append(
                    f"Task convergence '{tid}': {pkey.upper()} score {val} must be finite in [0.0, 5.0]"
                )
                bad_num = True
            pillar_vals.append(val)

        raw_total = row["total"]
        total_val = 0.0
        try:
            total_val = float(raw_total)
            if not math.isfinite(total_val) or total_val < 0.0 or total_val > 20.0:
                errs.append(f"Task convergence '{tid}': Total {total_val} must be finite in [0.0, 20.0]")
                bad_num = True
        except ValueError:
            errs.append(f"Task convergence '{tid}': invalid Total score {raw_total!r}")
            bad_num = True

        if not bad_num:
            expected_sum = sum(pillar_vals)
            if abs(total_val - expected_sum) > 0.05:
                errs.append(
                    f"Task convergence '{tid}': Total {total_val} does not equal P1..P4 sum {expected_sum:.2f}"
                )

        status = row["status"].upper()
        if status not in _VALID_TASK_STATUSES:
            errs.append(
                f"Task convergence '{tid}': invalid Status {row['status']!r} "
                f"(expected one of {', '.join(sorted(_VALID_TASK_STATUSES))})"
            )
        statuses.append((tid, status))

        expected_body_sha = compute_markdown_task_sha256(tid, text)
        if status == "LOCKED":
            if not _HEX64_RE.fullmatch(sha) or sha == _ZERO_HEX64:
                errs.append(
                    f"Task convergence '{tid}': LOCKED task requires valid non-zero 64-hex lowercase SHA-256"
                )
            elif expected_body_sha and sha != expected_body_sha:
                errs.append(
                    f"Task convergence '{tid}': SHA-256 '{sha}' does not match body task digest '{expected_body_sha}' "
                    "(run 'check_plan.py --hash-tasks --fix' to update)"
                )
            if not bad_num:
                if total_val < 16.0:
                    errs.append(f"Task convergence '{tid}': LOCKED task requires Total >= 16.0 (got {total_val})")
                if any(v < 2.5 for v in pillar_vals):
                    errs.append(f"Task convergence '{tid}': LOCKED task requires every pillar P1..P4 >= 2.5")
            if not has_independent:
                errs.append(
                    f"Task convergence '{tid}': LOCKED task requires an independent/fallback pass in Review log"
                )
        elif sha.lower() not in ("", "n/a", "-", "pending") and not _HEX64_RE.fullmatch(sha):
            errs.append(f"Task convergence '{tid}': malformed SHA-256 hash {sha!r}")

        if status == "PROBE" and probe_details:
            tid_pat = re.compile(rf"\b{re.escape(tid)}\b", re.IGNORECASE)
            for pdet in probe_details:
                if tid_pat.search(pdet["claim"]) and pdet["resolved_kind"] != "link0":
                    why = pdet["sandbox_err"] or "declared as PROBE[experiment]"
                    errs.append(
                        f"Task convergence '{tid}': Status PROBE cannot be backed by non-sandboxed "
                        f"Claim-ledger probe row {pdet['row']} ({why})"
                    )

        if status == "STALE":
            health_credits.append(0.0)
        else:
            health_credits.append(0.0 if bad_num else (total_val / 20.0) * 100.0)

    plan_health_pct = round(sum(health_credits) / len(health_credits), 1) if health_credits else 0.0

    # Terminal gate check: unless explicit valid SPLIT_TO_CHAIN (CHG-N) is active,
    # v2 requires zero-findings AND 100% LOCKED tasks (never bypassable via "green"/"no new findings").
    non_locked = [
        tid for tid, st in statuses if st != "LOCKED"
    ]
    if split_chg_id is None and non_locked:
        errs.append(
            f"v2 convergence incomplete: non-LOCKED task(s) {non_locked} require LOCKED status "
            "or explicit '**Disposition:** SPLIT_TO_CHAIN (CHG-<N>)'"
        )
    if has_zero_findings and non_locked and split_chg_id is None:
        pass  # Already reported above unconditionally

    return errs, plan_health_pct


def check_internal_references(
    stripped_text: str,
    ledger_body: str,
    *,
    num_passes: int,
    is_v2: bool,
) -> tuple[list[str], list[str]]:
    """Check duplicate numbered Claim IDs, dangling prose Claim pointers, and NEW@passN bounds (D3)."""
    errs: list[str] = []
    warns: list[str] = []
    target_bucket = errs if is_v2 else warns

    rows = table_rows(ledger_body)
    valid_ids: set[str] = set()
    seen_numbered_ids: set[str] = set()
    has_numbered_rows = False

    for label, cells, numbered in rows:
        norm_label = label.lower()
        valid_ids.add(norm_label)
        if numbered and cells and ROW_LABEL_RE.match(cells[0]):
            has_numbered_rows = True
            if norm_label in seen_numbered_ids:
                target_bucket.append(f"Claim ledger has duplicate row ID '{label}'")
            seen_numbered_ids.add(norm_label)

        claim_text = cells[1] if (numbered and len(cells) >= 2) else (cells[0] if cells else "")
        if num_passes > 0 and claim_text:
            for m_tag in _NEW_AT_PASS_RE.finditer(claim_text):
                pnum = int(m_tag.group(1))
                if pnum < 1 or pnum > num_passes:
                    target_bucket.append(
                        f"ledger row {label}: NEW@pass{pnum} exceeds Review log pass count ({num_passes})"
                    )

    if has_numbered_rows:
        stripped_ledger = _strip_fenced_blocks(ledger_body)
        prose_wo_ledger = _strip_inline_code(stripped_text.replace(stripped_ledger, ""))
        reported_missing: set[str] = set()
        for m_ref in _PROSE_CLAIM_REF_RE.finditer(prose_wo_ledger):
            first_id = m_ref.group(1)
            second_id = m_ref.group(2)
            candidates: list[str] = [first_id]
            if second_id:
                if first_id.isdigit() and second_id.isdigit():
                    start_n, end_n = int(first_id), int(second_id)
                    if start_n <= end_n and (end_n - start_n) <= 200:
                        candidates = [str(k) for k in range(start_n, end_n + 1)]
                    else:
                        candidates.append(second_id)
                else:
                    candidates.append(second_id)
            for cid in candidates:
                if cid.lower() not in valid_ids and cid.lower() not in reported_missing:
                    reported_missing.add(cid.lower())
                    target_bucket.append(f"prose references 'Claim {cid}', which does not exist in Claim ledger")

    return errs, warns


def _extract_markdown_tables(body: str) -> list[tuple[list[str], list[list[str]], str]]:
    """Extract all pipe tables from body as [(headers, data_rows, trailing_line), ...]."""
    lines = _strip_fenced_blocks(body).splitlines()
    tables: list[tuple[list[str], list[list[str]], str]] = []
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        if s.startswith("|") and i + 1 < n and lines[i + 1].strip().startswith("|"):
            sep_cells = [c.strip() for c in lines[i + 1].strip().strip("|").split("|")]
            if _is_separator(sep_cells):
                headers = [c.strip() for c in s.strip("|").split("|")]
                data_rows: list[list[str]] = []
                j = i + 2
                while j < n and lines[j].strip().startswith("|"):
                    rcells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                    if not _is_separator(rcells):
                        data_rows.append(rcells)
                    j += 1
                trailing = ""
                for k in range(j, min(n, j + 4)):
                    if lines[k].strip():
                        trailing = lines[k].strip()
                        break
                tables.append((headers, data_rows, trailing))
                i = j
                continue
        i += 1
    return tables


def check_scorecard(secs: dict[str, str]) -> list[str]:
    """Validate Tier-2 100-Point 5-Pillar Well-Architected Scorecard tables in v2 mode (D1)."""
    errs: list[str] = []
    scorecard_bodies = [body for name, body in secs.items() if "scorecard" in name]
    if not scorecard_bodies:
        return errs

    matched_scorecards = 0
    for body in scorecard_bodies:
        tables = _extract_markdown_tables(body)
        for headers, data_rows, _ in tables:
            norm_headers = [re.sub(r"[*_`]", "", h).strip().lower() for h in headers]
            if len(norm_headers) < 3 or not any("pillar" in h for h in norm_headers[:2]):
                continue
            weight_col = 1
            opt_indices = [
                idx
                for idx in range(2, len(headers))
                if not any(
                    skip in norm_headers[idx]
                    for skip in ("min bar", "minimum", "evaluation criteria", "core focus")
                )
            ]
            if not opt_indices:
                continue
            matched_scorecards += 1

            total_row: list[str] | None = None
            pillar_rows: list[list[str]] = []
            for row in data_rows:
                if len(row) < len(headers):
                    continue
                first_cell = re.sub(r"[*_`]", "", row[0]).strip().lower()
                if "total" in first_cell:
                    total_row = row
                else:
                    pillar_rows.append(row)

            if len(pillar_rows) != 5 or total_row is None:
                errs.append(
                    f"Scorecard table requires 5 pillar rows and a TOTAL SCORE row (found {len(pillar_rows)} pillar rows)"
                )
                continue

            selected_col = None
            for idx in opt_indices:
                if re.search(r"\b(?:selected|recommended)\b", headers[idx], re.IGNORECASE):
                    selected_col = idx
                    break
            if selected_col is None:
                for idx in opt_indices:
                    if re.search(r"\b(?:approved|ready|pass|selected)\b", total_row[idx], re.IGNORECASE):
                        selected_col = idx
                        break
            if selected_col is None:
                selected_col = opt_indices[0]

            pillar_scores: list[float] = []
            for p_idx, prow in enumerate(pillar_rows):
                expected_w = _SCORECARD_EXPECTED_WEIGHTS[p_idx]
                w_val = _extract_leading_float(prow[weight_col])
                max_w = w_val if (w_val is not None and w_val > 0) else expected_w
                score_val = _extract_leading_float(prow[selected_col])
                if score_val is None:
                    errs.append(f"Scorecard Pillar {p_idx + 1}: missing numeric score in selected option column")
                    continue
                if score_val < 0.0 or score_val > max_w:
                    errs.append(
                        f"Scorecard Pillar {p_idx + 1}: score {score_val} out of range [0.0, {max_w}]"
                    )
                if score_val < 0.5 * max_w:
                    errs.append(
                        f"Scorecard Pillar {p_idx + 1}: score {score_val} falls below 50% minimum floor ({0.5 * max_w})"
                    )
                pillar_scores.append(score_val)

            total_val = _extract_leading_float(total_row[selected_col])
            if total_val is None:
                errs.append("Scorecard TOTAL SCORE row: missing numeric score in selected option column")
                continue
            if len(pillar_scores) == 5 and abs(total_val - sum(pillar_scores)) > 0.05:
                errs.append(
                    f"Scorecard TOTAL SCORE {total_val} does not equal 5-pillar sum {sum(pillar_scores):.2f}"
                )
            if total_val < 80.0:
                errs.append(f"Scorecard selected option TOTAL SCORE {total_val} is below passing threshold 80.0")

    if matched_scorecards == 0:
        errs.append("Scorecard section contains no valid 5-pillar scorecard table")
    return errs


def check_open_questions(secs: dict[str, str]) -> list[str]:
    """Validate Tier-1 4-Pillar Open-Question tables in v2 mode (D2)."""
    errs: list[str] = []
    oq_bodies = [body for name, body in secs.items() if "open question" in name]
    if not oq_bodies:
        return errs

    matched_oq_tables = 0
    for body in oq_bodies:
        tables = _extract_markdown_tables(body)
        for t_idx, (headers, data_rows, trailing) in enumerate(tables, 1):
            norm_headers = [re.sub(r"[*_`]", "", h).strip().lower() for h in headers]
            if not any("composite" in h for h in norm_headers):
                continue
            comp_col = next(i for i, h in enumerate(norm_headers) if "composite" in h)
            if comp_col < 5:
                continue
            matched_oq_tables += 1
            pillar_cols = [comp_col - 4, comp_col - 3, comp_col - 2, comp_col - 1]
            opt_col = comp_col - 5

            if len(data_rows) < 3:
                errs.append(f"Open-question table {t_idx}: requires at least 3 options (found {len(data_rows)})")
                continue

            composites: list[float] = []
            selected_row_idx: int | None = None

            for r_idx, row in enumerate(data_rows):
                if len(row) <= comp_col:
                    errs.append(f"Open-question table {t_idx} row {r_idx + 1}: fewer cells than headers")
                    continue
                p_vals: list[float] = []
                for p_pos, c_idx in enumerate(pillar_cols, 1):
                    pval = _extract_leading_float(row[c_idx])
                    if pval is None or pval < 1.0 or pval > 5.0:
                        errs.append(
                            f"Open-question table {t_idx} row {r_idx + 1}: Pillar {p_pos} score must be in [1.0, 5.0]"
                        )
                    else:
                        p_vals.append(pval)
                cval = _extract_leading_float(row[comp_col])
                if cval is None:
                    errs.append(f"Open-question table {t_idx} row {r_idx + 1}: missing numeric composite score")
                    continue
                if len(p_vals) == 4:
                    expected_comp = sum(p_vals) / 4.0
                    if abs(cval - expected_comp) > 0.05:
                        errs.append(
                            f"Open-question table {t_idx} row {r_idx + 1}: Composite {cval:.2f} does not equal "
                            f"4-pillar average {expected_comp:.2f}"
                        )
                composites.append(cval)
                row_text = " ".join(row[: opt_col + 1] + [row[comp_col]])
                if re.search(r"\b(?:selected|recommended)\b", row_text, re.IGNORECASE):
                    selected_row_idx = r_idx

            if selected_row_idx is None and trailing:
                m_auto = re.search(r"Option\s+([1-9]\d*|[A-Z])\b", re.sub(r"[*_`]", "", trailing), re.IGNORECASE)
                if m_auto:
                    tok = m_auto.group(1).upper()
                    idx_guess = (int(tok) - 1) if tok.isdigit() else (ord(tok) - ord("A"))
                    if 0 <= idx_guess < len(composites):
                        selected_row_idx = idx_guess

            if selected_row_idx is None:
                errs.append(f"Open-question table {t_idx}: no option marked '(Selected)' or '*Auto-selected*'")
            elif len(composites) == len(data_rows) and composites:
                max_comp = max(composites)
                if composites[selected_row_idx] + 0.01 < max_comp:
                    errs.append(
                        f"Open-question table {t_idx}: selected option composite {composites[selected_row_idx]:.2f} "
                        f"is less than highest composite {max_comp:.2f}"
                    )

    if matched_oq_tables == 0:
        errs.append("Open-questions section contains no valid 4-pillar option table")
    return errs


def check_plan(
    path: Path, extra_roots: list[Path], *, cli_v2: bool = False
) -> tuple[list[str], list[str], list[tuple[str, str, int, int]], dict[str, int | float | bool | None]]:
    """Return (errors, warnings, drifts, info)."""
    text = path.read_text(errors="replace")
    secs = sections(text)
    is_v2, mode_errs = _detect_gate_mode(text, cli_v2=cli_v2)
    info: dict[str, int | float | bool | None] = {
        "gated": False,
        "citations": 0,
        "passes": 0,
        "dup_gated": False,
        "v2": is_v2,
        "plan_health_pct": None,
    }
    warns: list[str] = []
    drifts: list[tuple[str, str, int, int]] = []
    if not is_v2 and "claim ledger" not in secs and "review log" not in secs and not mode_errs:
        return [], warns, drifts, info  # not a gated v1 plan; skip
    info["gated"] = True
    roots = [repo_root(path), *extra_roots]
    errs: list[str] = list(mode_errs)

    stripped_text = _strip_fenced_blocks(text)
    for gated in ("claim ledger", "review log"):
        if len(re.findall(rf"^##\s+{gated}\s*$", stripped_text, re.M | re.I)) > 1:
            errs.append(
                f"duplicate '## {gated}' section — only one may exist; "
                f"rows in the other copy are ignored"
            )
            info["dup_gated"] = True

    probe_details: list[dict[str, str]] = []
    if "claim ledger" not in secs:
        errs.append("missing '## Claim ledger' section")
    else:
        info["citations"] = len(table_rows(secs["claim ledger"]))
        e, w, d, probes, probe_details = check_ledger(secs["claim ledger"], roots)
        errs += e
        warns += w
        drifts += d
        info["probes"] = len(probes)
        body_wo_ledger = stripped_text.replace(_strip_fenced_blocks(secs["claim ledger"]), "")
        defs = {
            m.group(1).lower()
            for m in re.finditer(r"^###\s+Phase\s+([A-Z]\b|\d+\b)", body_wo_ledger, re.M | re.I)
        } | {
            m.group(1).lower() for m in re.finditer(r"\*\*([A-Z]\d+[a-z]?)\b", body_wo_ledger, re.I)
        }
        for row, blocked in probes:
            if not blocked:
                continue
            pm = re.match(r"Phase\s+([A-Z]|\d+)\b", blocked, re.I)
            key = pm.group(1).lower() if pm else blocked.lower()
            if key not in defs:
                errs.append(
                    f"ledger row {row}: PROBE says it blocks '{blocked}', but no "
                    f"such phase exists in the plan — a probe attached to a "
                    f"deleted phase blocks nothing"
                )

    review_meta: dict[str, object] = {
        "passes": 0,
        "has_independent": False,
        "has_zero_findings": False,
        "split_chg_id": None,
        "has_escalate": False,
        "warns": [],
    }
    if "review log" not in secs:
        errs.append("missing '## Review log' section")
    else:
        rev_errs, review_meta = _check_review_detail(secs["review log"], is_v2=is_v2)
        info["passes"] = int(review_meta["passes"])
        errs += rev_errs
        rev_warns = review_meta.get("warns")
        if isinstance(rev_warns, list):
            warns += rev_warns

    if "claim ledger" in secs:
        ref_errs, ref_warns = check_internal_references(
            stripped_text,
            secs["claim ledger"],
            num_passes=int(info["passes"] or 0),
            is_v2=is_v2,
        )
        errs += ref_errs
        warns += ref_warns

    if is_v2:
        conv_errs, health_pct = check_task_convergence(
            text,
            secs,
            has_independent=bool(review_meta["has_independent"]),
            has_zero_findings=bool(review_meta["has_zero_findings"]),
            split_chg_id=review_meta["split_chg_id"] if isinstance(review_meta["split_chg_id"], str) else None,
            probe_details=probe_details,
        )
        errs += conv_errs
        errs += check_scorecard(secs)
        errs += check_open_questions(secs)
        info["plan_health_pct"] = health_pct

    return errs, warns, drifts, info


def apply_task_hashes(path: Path, *, update: bool = False) -> tuple[list[tuple[str, str]], int]:
    """Compute Stage-1 Markdown task SHA-256 digests for tasks in ## Task convergence (and optionally rewrite)."""
    text = path.read_text(errors="replace")
    secs = sections(text)
    sec_body = secs.get("task convergence") if "task convergence" in secs else secs.get("task scores")
    computed: list[tuple[str, str]] = []
    if sec_body is None:
        return computed, 0
    _, rows = _parse_task_convergence_table(sec_body)
    for row in rows:
        tid = row.get("task id", "").strip()
        if not tid:
            continue
        digest = compute_markdown_task_sha256(tid, text)
        if not digest:
            digest = hashlib.sha256(f"{tid}\n".encode()).hexdigest()
        computed.append((tid, digest))

    updated = 0
    if update and computed:
        lines = text.splitlines(keepends=True)
        in_conv_sec = False
        in_fence = False
        fence_char = ""
        fence_len = 0
        for idx, ln in enumerate(lines):
            raw_ln = ln.rstrip("\r\n")
            m_fence = re.match(r"^(?: {0,3})(`{3,}|~{3,})(.*)$", raw_ln)
            if not in_fence and m_fence:
                tok = m_fence.group(1)
                if not (tok[0] == "`" and "`" in m_fence.group(2)):
                    in_fence = True
                    fence_char = tok[0]
                    fence_len = len(tok)
                    continue
            elif in_fence:
                stripped = raw_ln.strip()
                if stripped and stripped[0] == fence_char and len(stripped) >= fence_len and set(stripped) <= {fence_char}:
                    in_fence = False
                    fence_char = ""
                    fence_len = 0
                continue
            m_hdr = re.match(r"^##\s+(.*\S)\s*$", raw_ln)
            if m_hdr and not ln.startswith("###"):
                in_conv_sec = m_hdr.group(1).strip().lower() in ("task convergence", "task scores")
                continue
            if not in_conv_sec:
                continue
            for tid, digest in computed:
                row_pat = re.compile(rf"^(\s*\|\s*`?{re.escape(tid)}`?\s*\|\s*)(`?[^|]+?`?)(\s*\|.*)$")
                m = row_pat.match(raw_ln)
                if m:
                    old_cell = m.group(2).strip().strip("`").strip()
                    if old_cell != digest:
                        ending = "\n" if ln.endswith("\n") else ""
                        lines[idx] = f"{m.group(1)}`{digest}`{m.group(3)}{ending}"
                        updated += 1
                    break
        if updated:
            path.write_text("".join(lines))
    return computed, updated


def apply_fixes(path: Path, drifts: list[tuple[str, str, int, int]]) -> int:
    """Re-point drifted citations **per row** (PLAN-051)."""
    lines = path.read_text(errors="replace").splitlines(keepends=True)
    fixed = 0
    for symbol, rel, old, new in drifts:
        pat = re.compile(re.escape(f"{rel}:{old}") + r"(?!\d)")
        for idx, ln in enumerate(lines):
            if symbol in ln and pat.search(ln):
                lines[idx] = pat.sub(f"{rel}:{new}", ln, count=1)
                fixed += 1
                break
    if fixed:
        path.write_text("".join(lines))
    return fixed


def _snippet_parts_with_v2() -> tuple[str, str, str]:
    """Split PLAN-TEMPLATE.snippet.md into (ledger, v1_review, v2_convergence_block)."""
    raw = SNIPPET.read_text()
    v2_block = ""
    m_v2 = V2_SNIPPET_RE.search(raw)
    if m_v2:
        v2_block = m_v2.group(1).strip()
        raw = V2_SNIPPET_RE.sub("", raw).rstrip() + "\n"
    idx = raw.find("## Review log")
    if idx == -1:
        return raw.rstrip(), "", v2_block
    return raw[:idx].rstrip(), raw[idx:].rstrip(), v2_block


def _snippet_parts() -> tuple[str, str]:
    """Split the bundled snippet into its v1 Claim-ledger and Review-log halves."""
    ledger, review, _ = _snippet_parts_with_v2()
    return ledger, review


def _default_v2_convergence_snippet() -> str:
    return (
        "## Task convergence\n\n"
        "> Opt-in for `<!-- plan-gate: v2 -->` / `check_plan.py --v2`: every task carries a canonical 64-hex `SHA-256`, 4-pillar scores (`0.0`–`5.0`), and status in `LOCKED | FIX | STALE | PROBE | REDESIGN`. `LOCKED` requires `Total >= 16.0`, every pillar `>= 2.5`, non-zero 64-hex hash, and an independent pass.\n\n"
        "| Task ID | SHA-256 | P1 | P2 | P3 | P4 | Total | Status |\n"
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |\n"
        "| TASK-1 | `<64-hex-sha256>` | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | PROBE |"
    )


def do_init(path: Path, *, v2: bool = False) -> int:
    """Append whichever of Claim-ledger / (v2 Task-convergence) / Review-log sections the plan lacks."""
    text = path.read_text(errors="replace") if path.exists() else ""
    try:
        ledger, review, v2_block = _snippet_parts_with_v2()
    except FileNotFoundError:
        print(
            f"error: snippet not found beside the gate ({SNIPPET}) — reinstall the gate",
            file=sys.stderr,
        )
        return 2

    added_labels: list[str] = []
    if v2:
        stripped = _strip_fenced_blocks(text)
        has_v2_dir = any(
            (m := GATE_DIRECTIVE_RE.fullmatch(ln)) and m.group(1).lower() == "v2"
            for ln in stripped.splitlines()
        )
        if not has_v2_dir:
            v1_replaced = False
            new_lines: list[str] = []
            fence_char = ""
            fence_len = 0
            for ln in text.splitlines(keepends=True):
                raw_ln = ln.rstrip("\r\n")
                fm = re.match(r"^\s{0,3}(`{3,}|~{3,})(.*)$", raw_ln)
                if not fence_char and fm and not (fm.group(1)[0] == "`" and "`" in fm.group(2)):
                    fence_char = fm.group(1)[0]
                    fence_len = len(fm.group(1))
                    new_lines.append(ln)
                    continue
                if fence_char:
                    cm = re.match(rf"^\s{{0,3}}{re.escape(fence_char)}{{{fence_len},}}\s*$", raw_ln)
                    if cm:
                        fence_char = ""
                        fence_len = 0
                    new_lines.append(ln)
                    continue
                m_dir = GATE_DIRECTIVE_RE.fullmatch(raw_ln)
                if m_dir and m_dir.group(1).lower() == "v1" and not v1_replaced:
                    ending = "\n" if ln.endswith("\n") else ""
                    new_lines.append(f"<!-- plan-gate: v2 -->{ending}")
                    v1_replaced = True
                else:
                    new_lines.append(ln)
            text = (
                "".join(new_lines)
                if v1_replaced
                else ("<!-- plan-gate: v2 -->\n\n" + text.lstrip("\n") if text else "<!-- plan-gate: v2 -->\n")
            )
            added_labels.append("plan-gate: v2")

    add: list[str] = []
    if "## Claim ledger" not in text:
        add.append(ledger)
    if v2 and "## Task convergence" not in text and "## Task scores" not in text:
        add.append(v2_block or _default_v2_convergence_snippet())
    if "## Review log" not in text and review:
        add.append(review)

    if not add and not added_labels:
        print(f"ok   {path} (already has Claim ledger + Review log; unchanged)")
        return 0

    if add:
        sep = "" if (text.endswith("\n\n") or not text) else ("\n" if text.endswith("\n") else "\n\n")
        text = text + sep + "\n\n".join(add) + "\n"
    elif not text.endswith("\n"):
        text += "\n"
    path.write_text(text)
    which = " + ".join(added_labels + [s.splitlines()[0].lstrip("# ") for s in add])
    print(f"init {path} (scaffolded: {which})")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="check_plan.py", add_help=True)
    ap.add_argument("plans", nargs="*")
    ap.add_argument(
        "--root",
        action="append",
        default=[],
        metavar="DIR",
        help="extra directory to resolve citations against (repeatable)",
    )
    ap.add_argument(
        "--init",
        action="store_true",
        help="scaffold Claim-ledger + Review-log sections into the plan(s)",
    )
    ap.add_argument(
        "--v2",
        action="store_true",
        help="enable v2 per-task convergence gating (or scaffold ## Task convergence with --init)",
    )
    ap.add_argument(
        "--hash-tasks",
        action="store_true",
        help="compute deterministic Stage-1 Markdown task SHA-256 digests (combine with --fix to rewrite table)",
    )
    ap.add_argument(
        "--fix",
        action="store_true",
        help="re-point unambiguous drifted citation line numbers in the plan(s)",
    )
    args = ap.parse_args(argv)

    if not args.plans:
        ap.print_usage(sys.stderr)
        return 2

    if args.init:
        rc = 0
        for a in args.plans:
            rc = do_init(Path(a), v2=args.v2) or rc
        return rc

    if args.hash_tasks and not args.fix:
        for a in args.plans:
            try:
                computed, _ = apply_task_hashes(Path(a), update=False)
            except OSError as exc:
                print(f"error: plan not found or unreadable: {a} ({exc})", file=sys.stderr)
                return 2
            for tid, digest in computed:
                print(f"hash {a} {tid} {digest}")
        return 0

    extra_roots = [Path(r) for r in args.root]
    failed = False
    for a in args.plans:
        try:
            if args.hash_tasks and args.fix:
                _, n_hashed = apply_task_hashes(Path(a), update=True)
                if n_hashed:
                    print(f"hash {a} — updated {n_hashed} task SHA-256 digest(s)")
            errs, warns, drifts, info = check_plan(Path(a), extra_roots, cli_v2=args.v2)
        except OSError as exc:
            print(f"error: plan not found or unreadable: {a} ({exc})", file=sys.stderr)
            return 2
        if args.fix and drifts:
            if info.get("dup_gated"):
                print(f"fix  {a} — skipped: duplicate gated header; de-duplicate first")
            else:
                n = apply_fixes(Path(a), drifts)
                if n:
                    print(f"fix  {a} — re-pointed {n} drifted citation(s)")
        if errs:
            failed = True
            print(f"FAIL {a}")
            for e in errs:
                print(f"  - {e}")
        elif info["gated"]:
            health_suffix = (
                f", health {info['plan_health_pct']:.1f}%"
                if info.get("v2") and isinstance(info.get("plan_health_pct"), (int, float))
                else ""
            )
            print(
                f"ok   {a} — verified {info['citations']} citation(s)"
                + (f", {info['probes']} probe(s)" if info.get("probes") else "")
                + f", {info['passes']} review pass(es)"
                + health_suffix
            )
        else:
            print(f"ok   {a} (not a gated plan; skipped)")
        if warns and not args.fix:
            for w in warns:
                print(f"  warn {a}: {w}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
