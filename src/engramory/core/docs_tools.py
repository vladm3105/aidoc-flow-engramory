"""AI-First Documentation, Executable Runbook AST, Linting, Drift & llms.txt Engine (PLAN-013).

Implements:
- Path confinement (`resolve_confined_repo_path`)
- Deterministic YAML frontmatter & annotated executable code-block AST parser (`parse_markdown_
- INV-STEP-01 & Google SRE defense-in-depth INV-STEP-02 validation
- AI-first chunk-independence & Google Docs-as-Code referential integrity linter (`lint_markdow
- Hardened `tier="green"` read-only smoke runner (`smoke_run_green_steps`)
- Git `watched_paths` drift & `last_verified` TTL staleness engine (`check_repo_drift`)
- Deterministic, prompt-cache friendly `/llms.txt` & `/llms-full.txt` exporter (`export_repo_llms`)
- Token-budgeted, L0-before-L2 runbook slicer (`slice_local_runbooks`, `format_runbook_slice`)
"""

from __future__ import annotations

import dataclasses
import fnmatch
import json
import os
import re
import shutil
import subprocess  # nosec B404 # reason: controlled CLI invocations for vale, git diff, and green smoke probes
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from engramory.access.poisoning_guard import (
    _CTRL_AND_BIDI_RE,
    _SECRET_PATTERNS,
    detect_hard_secrets,
    has_dangerous_unicode,
)
from engramory.core.citations import build_doc_id, generate_evidence_ref
from engramory.core.models import (
    AUTONOMY_TIERS,
    DOC_STATUSES,
    RUNBOOK_MODES,
    KBDocument,
    KBRunbookStep,
    ValidationError,
)

MAX_SECTION_WORDS = 450
MAX_SMOKE_OUTPUT_BYTES = 8192

TIER_ORDER: Mapping[str, int] = {"green": 0, "yellow": 1, "red": 2}

REQUIRED_FRONTMATTER_KEYS = (
    "id",
    "title",
    "kind",
    "domain",
    "namespace",
    "status",
    "autonomy_tier",
    "watched_paths",
    "last_verified",
)

BANNED_DEICTIC_PHRASES = (
    "the command above",
    "the script above",
    "as shown below",
    "in the previous section",
    "see above",
    "see below",
    "as mentioned earlier",
)

BANNED_RETIRED_TOOLS: Mapping[str, str] = {
    "seaweedfs": "MinIO / RustFS",
    "bytebase": "Atlas CLI",
    "pre-commit": "lefthook",
}

DOCUMENTATION_TOOL_CATALOG: tuple[dict[str, str], ...] = (
    {
        "tool": "engramory docs (platform-engramory)",
        "category": "Core Documentation, Runbook AST, Drift & RAG Engine",
        "tier": "Adopt",
        "gate": "Tier 1 + Tier 2 + Tier 3 + MCP Server",
        "license": "Apache-2.0",
        "role": (
            "Parses DocumentFrontmatter & {id, tier, mode} executable runbook steps, enforces "
            "INV-STEP-01/02, checks git watched_paths drift & TTL staleness, exports /llms.txt, "
            "and fuses L0 runbooks with L2 episodic memories."
        ),
    },
    {
        "tool": "vale (errata-ai/vale)",
        "category": "AI-Chunking & Prose AST Linter",
        "tier": "Adopt",
        "gate": "Tier 1 (Offline <1s)",
        "license": "MIT",
        "role": (
            "Enforces AIOpsAgent chunk-independence rules (.vale.ini & .vale/styles/AIOpsAgent/): "
            "NoCrossChunkDeictics, NoAmbiguousSubjectStart, BannedRetiredTools, "
            "DeterministicPortFormat, paired with pure-Python parity in docs_tools.py."
        ),
    },
    {
        "tool": "@astrojs/starlight",
        "category": "Static Multi-Repo Developer Portal",
        "tier": "Trial",
        "gate": "Tier 2 Build / Stage Portal (https://docs.int.aiops-flow.io)",
        "license": "MIT",
        "role": (
            "Aggregates thin-spoke docs/*.md and SDD projections into a zero-JS searchable "
            "developer portal with native Mermaid v11 and runbook autonomy badges."
        ),
    },
    {
        "tool": "@scalar/api-reference",
        "category": "Interactive API & Contract Playground",
        "tier": "Trial",
        "gate": "Tier 2 Build / Stage Portal (/contracts/<service>)",
        "license": "MIT",
        "role": (
            "Renders interactive OpenAPI 3.1 specs, JSONSchema contracts, MCP tool schemas, "
            "and Protobuf HTTP routes inside the Starlight portal."
        ),
    },
    {
        "tool": "docling + pymupdf + beautifulsoup4",
        "category": "Multi-Format Document Layout & Table Extraction",
        "tier": "Adopt",
        "gate": "Utility / Ingestion Pipeline (SmartExtractionRouter)",
        "license": "MIT / AGPL-3.0 (optional) / MIT",
        "role": (
            "Extracts PDFs, DOCX, and HTML documents into heading-structured Markdown chunks "
            "inside platform-engramory's SmartExtractionRouter."
        ),
    },
    {
        "tool": "mermaid (@mermaid-js/mermaid-cli / mmdc)",
        "category": "Diagrams-as-Code",
        "tier": "Adopt",
        "gate": "Tier 2 CI + Native Portal Rendering",
        "license": "MIT",
        "role": (
            "Authoritative visualization standard for C4 topologies, sequence diagrams, "
            "state machines, and ER schemas."
        ),
    },
    {
        "tool": "markdownlint-cli2",
        "category": "Markdown Syntax & Heading AST Linter",
        "tier": "Adopt",
        "gate": "Tier 1 (Offline <1s)",
        "license": "MIT",
        "role": (
            "Fast glob-aware Markdown structure and code-fence linter in lefthook & task check."
        ),
    },
    {
        "tool": "yamllint",
        "category": "YAML Schema & Frontmatter Linter",
        "tier": "Adopt",
        "gate": "Tier 1 (Offline <1s)",
        "license": "GPL-3.0",
        "role": "Lints SDD YAML layers, ADR/CHG records, and configuration manifests.",
    },
    {
        "tool": "lychee",
        "category": "Cross-Repo & External Link Verifier",
        "tier": "Adopt",
        "gate": "Tier 2 PR CI + Tier 3 Nightly Sweep",
        "license": "Apache-2.0 / MIT",
        "role": "Rust link checker verifying relative anchors and external URLs.",
    },
    {
        "tool": "aidoc-framework (sdd_doc_lint) + Embedded Kùzu",
        "category": "10-Layer SDD Lifecycle & Topological Graph Slicer",
        "tier": "Adopt",
        "gate": "Tier 1 + Tier 2",
        "license": "Proprietary / MIT (Kùzu)",
        "role": (
            "Validates the 10-layer SDD chain (BRD->EVAL), executes Binary Topological Validation "
            "(BTV), and slices sub-millisecond openCypher context closures."
        ),
    },
    {
        "tool": "copybara",
        "category": "Cross-Repo Git Sync (Evaluated & Held)",
        "tier": "Hold",
        "gate": "None",
        "license": "Apache-2.0",
        "role": (
            "Not used (Hold): JVM Bazel dependency and Starlark transform maintenance overhead; "
            "replaced by Engramory GitSyncEngine, engramory docs build-portal, and Split-Forge "
            "Gitea-to-GitHub mirror."
        ),
    },
    {
        "tool": "mintlify / readme / mkdocs-material-insiders",
        "category": "SaaS & Open-Core Doc Portals (Banned)",
        "tier": "Hold",
        "gate": "None",
        "license": "Proprietary / Commercial",
        "role": (
            "Banned per INV-DOC-01: proprietary SaaS lock-in or paywalled features; "
            "use Astro Starlight (MIT) + Scalar (MIT)."
        ),
    },
)


def list_documentation_tools(*, tier: str | None = None) -> dict[str, Any]:
    """Return the governed documentation tool inventory filtered by optional tier."""
    valid_tiers = {"Adopt", "Trial", "Hold"}
    if tier is not None and tier not in valid_tiers:
        raise ValidationError(f"Invalid tool tier '{tier}'; must be one of {sorted(valid_tiers)}")
    tools = [
        dict(item) for item in DOCUMENTATION_TOOL_CATALOG if tier is None or item["tier"] == tier
    ]
    return {
        "status": "ok",
        "count": len(tools),
        "tools": tools,
    }


# Google SRE defense-in-depth INV-STEP-02 patterns prohibited in tier="green" steps
_GREEN_BANNED_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:docker|podman)\b[^\n]*\b(?:restart|rm|stop|kill|down|up|exec|run|cp)\b",
            re.IGNORECASE,
        ),
        (
            "Mutating container lifecycle verb ('docker/podman restart|rm|stop|kill|down|up|exec|"
            "run|cp') is prohibited in tier='green'"
        ),
    ),
    (
        re.compile(
            r"\b(?:tofu|terraform)\s+(?:apply|destroy|import|taint|state\s+rm)\b"
            r"|\bkubectl\b[^\n]*\b(?:apply|delete|patch|edit|replace|scale|rollout\s+restart)\b"
            r"|\bgit\s+(?:push|reset|clean|commit|merge|rebase)\b",
            re.IGNORECASE,
        ),
        "Mutating IaC/orchestrator/git command is prohibited in tier='green'",
    ),
    (
        re.compile(
            r"\b(?:rm|mv|dd|shred|unlink|chmod|chown|mkfs|truncate)\b"
            r"|\bfind\b[^\n]*\s-(?:delete|exec)\b"
        ),
        (
            "Destructive or mutating file operation ('rm|mv|dd|shred|unlink|chmod|chown|truncate') "
            "is prohibited in tier='green'"
        ),
    ),
    (
        re.compile(
            r"\b(?:bao|vault)\s+operator\s+(?:generate-root|rekey|rotate|step-down)\b",
            re.IGNORECASE,
        ),
        "Mutating OpenBao/Vault operator command is prohibited in tier='green'",
    ),
    (
        re.compile(r"\bsystemctl\s+(?:restart|stop|reload|disable|mask|kill)\b", re.IGNORECASE),
        "Mutating systemctl verb is prohibited in tier='green'",
    ),
    (
        re.compile(
            r"\b(?:DROP|DELETE\s+FROM|UPDATE\s+[\w.]+\s+SET|INSERT\s+INTO|ALTER\s+TABLE|TRUNCATE"
            r"|CREATE\s+(?:TABLE|INDEX|DATABASE|ROLE))\b",
            re.IGNORECASE,
        ),
        (
            "Mutating SQL statement ('DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|CREATE') "
            "is prohibited in tier='green'"
        ),
    ),
    (
        re.compile(
            r"\$\(|`[^`]*`|[<>]\(|\b(?:eval|exec|source)\b"
            r"|\b(?:bash|sh|zsh|dash|python3?|perl|ruby|node)\s+-[ce]\b"
        ),
        (
            "Subshell/process substitution or inline interpreter ('$(...)', backticks, '<(...)', "
            "'eval', 'exec', 'bash -c', 'python -c') is prohibited in tier='green'"
        ),
    ),
    (
        re.compile(r"(?<![0-9>&])>>?(?!\s*(?:&1|&2|/dev/null))\s*\S+|\|\s*tee\b"),
        "File output redirection ('>', '>>', '| tee') is prohibited in tier='green'",
    ),
    (
        re.compile(
            r"\bcurl\b[^\n]*(?:(?:-X|--request)\s*=?\s*(?:POST|PUT|DELETE|PATCH)"
            r"|(?:-d|--data(?:-[a-z]+)?|-F|--form|-T|--upload-file|--json|-K|--config|-o|--output|-O)(?:\b|=|\S))"
            r"|\bwget\b[^\n]*(?:--post-(?:data|file)|--method\s*=?\s*(?:POST|PUT|DELETE|PATCH)|--body-(?:data|file))"
            r"|\b(?:xh|http)\s+(?:POST|PUT|DELETE|PATCH)\b",
            re.IGNORECASE,
        ),
        (
            "Mutating or file-writing HTTP client flag ('curl -X POST|PUT|DELETE|PATCH', '--data', "
            "'--form', '--upload-file', '--json', '--config', '-o') is prohibited in tier='green'"
        ),
    ),
)

_GIT_REF_RE = re.compile(r"^[A-Za-z0-9_./~^-]+$")
_AMBIGUOUS_SUBJECT_RE = re.compile(
    r"^(?:It|This|They|These|Those)\s+(?:is|are|was|were|will|should|must|can|triggers|runs|fails)\b"
)
_BARE_LOCALHOST_RE = re.compile(r"\bhttps?://localhost:\d+\b")

_SENSITIVE_ENV_RE = re.compile(
    r"(?:SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE_KEY|API_KEY|DATABASE_URL|DSN|CREDENTIAL|AUTH)",
    re.IGNORECASE,
)

_FENCE_OPEN_RE = re.compile(r"^(`{3,}|~{3,})([^\n`~]*)$")
_ATTR_BLOCK_RE = re.compile(r"\{([^}]+)\}\s*$")
_ATTR_KV_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_-]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s}]+))')
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_INLINE_CODE_RE = re.compile(r"`[^`]+`")
_MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


@dataclass(frozen=True, slots=True)
class LintDiagnostic:
    """Actionable ACI diagnostic with remediation hint for autonomous coding agents."""

    rule_id: str
    severity: str  # "error" | "warning"
    file: str
    line: int
    message: str
    remediation_hint: str

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True, slots=True)
class ParsedMarkdownDocument:
    """Parsed representation of a Markdown document, its frontmatter, and executable steps."""

    file_path: Path
    rel_path: str
    frontmatter: Mapping[str, Any]
    has_frontmatter: bool
    kb_document: KBDocument | None
    steps: tuple[KBRunbookStep, ...]
    diagnostics: tuple[LintDiagnostic, ...] = ()
    body_text: str = ""


def _is_within_root(file_path: Path, root: Path) -> bool:
    """Verify resolved `file_path` stays inside resolved `root` (blocks symlink escape)."""
    try:
        return file_path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def resolve_confined_repo_path(
    path: str | Path,
    *,
    allowed_root: Path | None = None,
    project_id: str | None = None,
    enforce_project_boundary: bool = False,
) -> Path:
    """Resolve `path` and verify it stays within `allowed_root` (or workspace boundary)."""
    if allowed_root is None:
        env_root = os.environ.get("ENGRAMORY_ALLOWED_DOCS_ROOT", "").strip()
        base = Path(env_root).resolve() if env_root else Path.cwd().resolve()
    else:
        base = allowed_root.resolve()

    effective_base = base
    if enforce_project_boundary:
        if (
            not project_id
            or not project_id.strip()
            or "/" in project_id
            or "\\" in project_id
            or project_id.strip() in (".", "..")
        ):
            raise ValidationError("Valid project_id is required when enforce_project_boundary=True")
        clean_proj = project_id.strip()
        effective_base = base if base.name == clean_proj else (base / clean_proj).resolve()
        if not effective_base.is_relative_to(base):
            raise ValidationError(f"Path '{path}' is outside allowed boundary")

    raw = Path(path)
    if raw.is_absolute():
        candidate = raw.resolve()
    elif enforce_project_boundary and effective_base != base:
        if raw.parts and raw.parts[0] == effective_base.name:
            candidate = (base / raw).resolve()
        else:
            candidate = (effective_base / raw).resolve()
    else:
        candidate = (Path.cwd().resolve() / raw).resolve()

    if not candidate.is_relative_to(base) or not candidate.is_relative_to(effective_base):
        raise ValidationError("Requested path is outside allowed boundary")
    return candidate


def check_green_step_safety(command_body: str) -> list[str]:
    """Verify Google SRE defense-in-depth INV-STEP-02 on a tier='green' command body."""
    violations: list[str] = []
    # Collapse backslash line-continuations first so split tokens cannot bypass regexes
    unwrapped = re.sub(r"\\\r?\n\s*", " ", command_body)
    # Strip full-line comments before checking patterns
    active_lines = [
        ln for ln in unwrapped.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    ]
    cleaned = "\n".join(active_lines)
    for pattern, reason in _GREEN_BANNED_PATTERNS:
        if pattern.search(cleaned):
            violations.append(reason)
    return violations


def _extract_frontmatter(content: str) -> tuple[dict[str, Any] | None, str, int, str | None]:
    """Return (frontmatter_dict, body_text, body_start_line, yaml_error)."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, content, 1, None

    end_idx: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break

    if end_idx is None:
        return None, content, 1, "Unclosed YAML frontmatter fence '---'"

    fm_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :])
    try:
        loaded = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        return None, body, end_idx + 2, f"Invalid YAML frontmatter: {exc}"

    if loaded is None:
        return {}, body, end_idx + 2, None
    if not isinstance(loaded, dict):
        return None, body, end_idx + 2, "YAML frontmatter must be a key-value mapping"
    return loaded, body, end_idx + 2, None


def _parse_fence_attributes(info_str: str) -> dict[str, str] | None:
    m = _ATTR_BLOCK_RE.search(info_str)
    if not m:
        return None
    attr_body = m.group(1)
    attrs: dict[str, str] = {}
    for kv in _ATTR_KV_RE.finditer(attr_body):
        key = kv.group(1)
        val = kv.group(2) or kv.group(3) or kv.group(4) or ""
        attrs[key] = val
    return attrs


def parse_markdown_document(
    file_path: Path,
    *,
    repo_root: Path | None = None,
    tenant_id: str = "default",
    project_id: str | None = None,
    domain_id: str | None = None,
    scope: str = "project",
) -> ParsedMarkdownDocument:
    """Parse a Markdown file's YAML frontmatter and annotated executable runbook code blocks."""
    resolved_file = file_path.resolve()
    if repo_root is not None:
        try:
            rel_path = str(resolved_file.relative_to(repo_root.resolve()))
        except ValueError:
            rel_path = resolved_file.name
    else:
        rel_path = resolved_file.name

    content = resolved_file.read_text(encoding="utf-8")
    diagnostics: list[LintDiagnostic] = []

    fm, body, body_start_line, yaml_err = _extract_frontmatter(content)
    if yaml_err is not None:
        diagnostics.append(
            LintDiagnostic(
                rule_id="DOC-FM-001",
                severity="error",
                file=rel_path,
                line=1,
                message=yaml_err,
                remediation_hint=(
                    "Wrap valid YAML frontmatter between opening and closing '---' fences "
                    "at line 1."
                ),
            )
        )
        return ParsedMarkdownDocument(
            file_path=resolved_file,
            rel_path=rel_path,
            frontmatter={},
            has_frontmatter=False,
            kb_document=None,
            steps=(),
            diagnostics=tuple(diagnostics),
            body_text=body,
        )

    has_frontmatter = fm is not None
    fm_dict: dict[str, Any] = fm or {}
    kb_doc: KBDocument | None = None

    if has_frontmatter:
        for req_key in REQUIRED_FRONTMATTER_KEYS:
            if req_key not in fm_dict or fm_dict[req_key] in (None, ""):
                diagnostics.append(
                    LintDiagnostic(
                        rule_id="DOC-FM-002",
                        severity="error",
                        file=rel_path,
                        line=1,
                        message=f"Missing required frontmatter key '{req_key}'",
                        remediation_hint=(
                            f"Add '{req_key}: ...' to the YAML frontmatter block "
                            f"at the top of '{rel_path}'."
                        ),
                    )
                )

        doc_id = str(fm_dict.get("id") or build_doc_id(project_id, rel_path))
        title = str(fm_dict.get("title") or resolved_file.stem)
        kind = str(fm_dict.get("kind") or "doc")
        namespace = str(fm_dict.get("namespace") or "ns:platform")
        status = str(fm_dict.get("status") or "active")
        autonomy_tier = str(fm_dict.get("autonomy_tier") or "green")

        if status not in DOC_STATUSES:
            diagnostics.append(
                LintDiagnostic(
                    rule_id="DOC-FM-003",
                    severity="error",
                    file=rel_path,
                    line=1,
                    message=(
                        f"Invalid frontmatter status '{status}'; "
                        f"must be one of {sorted(DOC_STATUSES)}"
                    ),
                    remediation_hint=(
                        f"Set 'status' in frontmatter to one of: {', '.join(sorted(DOC_STATUSES))}."
                    ),
                )
            )
            status = "active"

        if autonomy_tier not in AUTONOMY_TIERS:
            diagnostics.append(
                LintDiagnostic(
                    rule_id="DOC-FM-004",
                    severity="error",
                    file=rel_path,
                    line=1,
                    message=(
                        f"Invalid autonomy_tier '{autonomy_tier}'; "
                        f"must be one of {sorted(AUTONOMY_TIERS)}"
                    ),
                    remediation_hint=(
                        "Set 'autonomy_tier' in frontmatter to 'green', 'yellow', or 'red'."
                    ),
                )
            )
            autonomy_tier = "green"

        raw_watched = fm_dict.get("watched_paths")
        if not isinstance(raw_watched, list) or not raw_watched:
            if "watched_paths" in fm_dict:
                diagnostics.append(
                    LintDiagnostic(
                        rule_id="DOC-FM-005",
                        severity="error",
                        file=rel_path,
                        line=1,
                        message=(
                            "'watched_paths' must be a non-empty list of "
                            "repository-relative glob strings"
                        ),
                        remediation_hint=(
                            "Provide at least one repository-relative path or glob in "
                            "'watched_paths'."
                        ),
                    )
                )
            watched_paths: tuple[str, ...] = ()
        else:
            watched_paths = tuple(str(x) for x in raw_watched if x)

        triggers_raw = fm_dict.get("triggers")
        trigger_alerts: tuple[str, ...] = ()
        if isinstance(triggers_raw, dict):
            alerts_list = triggers_raw.get("alerts")
            if isinstance(alerts_list, list):
                trigger_alerts = tuple(str(a) for a in alerts_list if a)

        raw_last_verified = fm_dict.get("last_verified", "2026-11-06")
        if isinstance(raw_last_verified, date):
            last_verified_str = raw_last_verified.isoformat()
        else:
            last_verified_str = str(raw_last_verified)
            try:
                date.fromisoformat(last_verified_str)
            except ValueError:
                diagnostics.append(
                    LintDiagnostic(
                        rule_id="DOC-FM-006",
                        severity="error",
                        file=rel_path,
                        line=1,
                        message=f"Invalid ISO date '{last_verified_str}' for 'last_verified'",
                        remediation_hint="Format 'last_verified' as YYYY-MM-DD (e.g. 2026-11-06).",
                    )
                )
                last_verified_str = "2026-11-06"

        raw_ttl = fm_dict.get("review_ttl_days", 90)
        try:
            review_ttl_days = int(raw_ttl if raw_ttl is not None else 90)
        except (ValueError, TypeError):
            diagnostics.append(
                LintDiagnostic(
                    rule_id="DOC-FM-007",
                    severity="error",
                    file=rel_path,
                    line=1,
                    message=f"Invalid integer '{raw_ttl}' for 'review_ttl_days'",
                    remediation_hint="Set 'review_ttl_days' to a positive integer (e.g. 90).",
                )
            )
            review_ttl_days = 90
        git_sha = generate_evidence_ref(resolved_file)
        eff_domain = domain_id or (str(fm_dict["domain"]) if fm_dict.get("domain") else None)

        # Ensure JSON-serializable frontmatter copy
        serializable_fm = json.loads(json.dumps(fm_dict, default=str))
        kb_doc = KBDocument(
            doc_id=doc_id,
            repo_path=rel_path,
            title=title,
            kind=kind,
            namespace=namespace,
            status=status,
            autonomy_tier=autonomy_tier,
            frontmatter=serializable_fm,
            watched_paths=watched_paths,
            trigger_alerts=trigger_alerts,
            last_verified=last_verified_str,
            review_ttl_days=max(1, review_ttl_days),
            git_commit_sha=git_sha,
            project_id=project_id,
            domain_id=eff_domain,
            tenant_id=tenant_id,
            scope=scope,
        )
    else:
        doc_id = build_doc_id(project_id, rel_path)

    # Parse body for heading hierarchy and annotated executable code blocks
    lines = body.splitlines()
    heading_stack: list[tuple[int, str]] = []
    in_fence = False
    fence_marker = ""
    fence_start_line = 0
    fence_attrs: dict[str, str] | None = None
    fence_lines: list[str] = []
    recent_prose_lines: list[str] = []
    steps: list[KBRunbookStep] = []
    step_order = 0
    seen_step_ids: set[str] = set()

    for idx, line in enumerate(lines):
        line_no = body_start_line + idx
        if not in_fence:
            m_open = _FENCE_OPEN_RE.match(line.strip())
            if m_open:
                in_fence = True
                fence_marker = m_open.group(1)
                fence_start_line = line_no
                fence_attrs = _parse_fence_attributes(m_open.group(2))
                fence_lines = []
                continue

            m_head = _HEADING_RE.match(line)
            if m_head:
                level = len(m_head.group(1))
                h_text = m_head.group(2).strip()
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, h_text))
                recent_prose_lines = []
            elif line.strip():
                recent_prose_lines.append(line.strip())
                if len(recent_prose_lines) > 6:
                    recent_prose_lines.pop(0)
        else:
            stripped = line.strip()
            if stripped.startswith(fence_marker) and stripped == fence_marker[0] * len(stripped):
                # Closing fence
                if fence_attrs is not None:
                    step_order += 1
                    step_id = fence_attrs.get("id", "").strip()
                    tier = fence_attrs.get("tier", "").strip()
                    mode = fence_attrs.get("mode", "").strip()
                    expect_exit_str = fence_attrs.get("expect_exit", "0").strip()
                    timeout_str = fence_attrs.get("timeout_sec", "30").strip()
                    rollback_step = fence_attrs.get("rollback_step", "").strip() or None

                    if not step_id:
                        diagnostics.append(
                            LintDiagnostic(
                                rule_id="DOC-STEP-001",
                                severity="error",
                                file=rel_path,
                                line=fence_start_line,
                                message="Executable code block is missing required 'id' attribute",
                                remediation_hint=(
                                    'Add id="step-name" to the code fence attributes: '
                                    '```bash {id="step-name" tier="green" mode="read-only"}.'
                                ),
                            )
                        )
                        step_id = f"step-{step_order}"
                    elif step_id in seen_step_ids:
                        diagnostics.append(
                            LintDiagnostic(
                                rule_id="DOC-STEP-002",
                                severity="error",
                                file=rel_path,
                                line=fence_start_line,
                                message=f"Duplicate executable step id '{step_id}' in '{rel_path}'",
                                remediation_hint=(
                                    f"Give step '{step_id}' at line {fence_start_line} a unique "
                                    f"'id' attribute within this document."
                                ),
                            )
                        )
                    seen_step_ids.add(step_id)

                    if tier not in AUTONOMY_TIERS:
                        diagnostics.append(
                            LintDiagnostic(
                                rule_id="DOC-STEP-003",
                                severity="error",
                                file=rel_path,
                                line=fence_start_line,
                                message=(
                                    f"Step '{step_id}' has invalid or missing tier '{tier}'; "
                                    f"must be green, yellow, or red"
                                ),
                                remediation_hint=(
                                    'Set tier="green", tier="yellow", or tier="red" on the '
                                    "code fence."
                                ),
                            )
                        )
                        tier = "yellow"

                    if mode not in RUNBOOK_MODES:
                        diagnostics.append(
                            LintDiagnostic(
                                rule_id="DOC-STEP-004",
                                severity="error",
                                file=rel_path,
                                line=fence_start_line,
                                message=(
                                    f"Step '{step_id}' has invalid or missing mode '{mode}'; "
                                    f"must be read-only or mutating"
                                ),
                                remediation_hint=(
                                    'Set mode="read-only" or mode="mutating" on the code fence.'
                                ),
                            )
                        )
                        mode = "read-only" if tier == "green" else "mutating"

                    if tier == "green" and mode != "read-only":
                        diagnostics.append(
                            LintDiagnostic(
                                rule_id="INV-STEP-01",
                                severity="error",
                                file=rel_path,
                                line=fence_start_line,
                                message=(
                                    f"INV-STEP-01 violation on step '{step_id}': tier='green' "
                                    f"requires mode='read-only' (got mode='{mode}')"
                                ),
                                remediation_hint=(
                                    f"Change step '{step_id}' to tier='yellow' if it mutates "
                                    f"state, or mode='read-only' if it is a safe diagnostic probe."
                                ),
                            )
                        )

                    cmd_body = "\n".join(fence_lines).strip()
                    if tier == "green":
                        for violation in check_green_step_safety(cmd_body):
                            diagnostics.append(
                                LintDiagnostic(
                                    rule_id="INV-STEP-02",
                                    severity="error",
                                    file=rel_path,
                                    line=fence_start_line,
                                    message=(
                                        f"INV-STEP-02 violation on step '{step_id}': {violation}"
                                    ),
                                    remediation_hint=(
                                        f"Promote step '{step_id}' to tier='yellow' "
                                        f"mode='mutating' or remove the mutating/subshell "
                                        f"construct."
                                    ),
                                )
                            )

                    try:
                        expect_exit = int(expect_exit_str)
                    except ValueError:
                        expect_exit = 0
                    try:
                        timeout_sec = max(1, int(timeout_str))
                    except ValueError:
                        timeout_sec = 30

                    breadcrumb = (
                        " > ".join(h for _, h in heading_stack)
                        if heading_stack
                        else (kb_doc.title if kb_doc else resolved_file.stem)
                    )
                    prose_ctx = " ".join(recent_prose_lines)
                    if cmd_body and not (tier == "green" and mode != "read-only"):
                        steps.append(
                            KBRunbookStep(
                                step_uid=f"{doc_id}#{step_id}",
                                doc_id=doc_id,
                                step_order=step_order,
                                step_id=step_id,
                                heading_path=breadcrumb,
                                tier=tier,
                                mode=mode,
                                command_body=cmd_body,
                                prose_context=prose_ctx,
                                expect_exit=expect_exit,
                                timeout_sec=timeout_sec,
                                rollback_step=rollback_step,
                                tenant_id=tenant_id,
                            )
                        )
                in_fence = False
                fence_marker = ""
                fence_attrs = None
                fence_lines = []
            else:
                fence_lines.append(line)

    # Referential check: MISSING_ROLLBACK_STEP
    for st in steps:
        if st.rollback_step and st.rollback_step not in seen_step_ids:
            diagnostics.append(
                LintDiagnostic(
                    rule_id="MISSING_ROLLBACK_STEP",
                    severity="error",
                    file=rel_path,
                    line=1,
                    message=(
                        f"Step '{st.step_id}' references rollback_step='{st.rollback_step}', "
                        f"which does not exist in '{rel_path}'"
                    ),
                    remediation_hint=(
                        f"Add an executable code block with id='{st.rollback_step}' to "
                        f"'{rel_path}' or update rollback_step to an existing step id."
                    ),
                )
            )

    return ParsedMarkdownDocument(
        file_path=resolved_file,
        rel_path=rel_path,
        frontmatter=fm_dict,
        has_frontmatter=has_frontmatter,
        kb_document=kb_doc,
        steps=tuple(steps),
        diagnostics=tuple(diagnostics),
        body_text=body,
    )


def lint_markdown_file(
    file_path: Path,
    *,
    repo_root: Path | None = None,
    require_frontmatter: bool = True,
    verify_registry: bool = False,
) -> list[LintDiagnostic]:
    """Run AI-first chunk-independence, AST, secret, and referential integrity checks on a file."""
    parsed = parse_markdown_document(file_path, repo_root=repo_root)
    diags: list[LintDiagnostic] = list(parsed.diagnostics)
    rel_path = parsed.rel_path

    if require_frontmatter and not parsed.has_frontmatter:
        diags.append(
            LintDiagnostic(
                rule_id="DOC-FM-000",
                severity="error",
                file=rel_path,
                line=1,
                message=f"Document '{rel_path}' is missing deterministic YAML frontmatter",
                remediation_hint=(
                    "Add a YAML frontmatter block ('---') at line 1 with id, title, kind, "
                    "domain, namespace, status, autonomy_tier, watched_paths, and last_verified."
                ),
            )
        )

    if verify_registry and parsed.has_frontmatter:
        endpoints = parsed.frontmatter.get("endpoints")
        if endpoints is not None:
            if not isinstance(endpoints, dict):
                diags.append(
                    LintDiagnostic(
                        rule_id="DOC-REG-001",
                        severity="error",
                        file=rel_path,
                        line=1,
                        message="Frontmatter 'endpoints' must be a key-value mapping",
                        remediation_hint=(
                            "Define 'endpoints' as a mapping with 'host_loopback' and/or "
                            "'container_gateway' URLs."
                        ),
                    )
                )
            else:
                host_lb = endpoints.get("host_loopback")
                if host_lb is not None and not re.match(
                    r"^https?://127\.0\.0\.1:\d+", str(host_lb)
                ):
                    diags.append(
                        LintDiagnostic(
                            rule_id="DOC-REG-001",
                            severity="error",
                            file=rel_path,
                            line=1,
                            message=(
                                f"Invalid endpoints.host_loopback '{host_lb}'; must bind to "
                                f"explicit host loopback 'http(s)://127.0.0.1:<port>'"
                            ),
                            remediation_hint=(
                                "Set endpoints.host_loopback to 'http://127.0.0.1:<port>'."
                            ),
                        )
                    )
                container_gw = endpoints.get("container_gateway")
                if container_gw is not None and not re.match(
                    r"^https?://10\.250\.0\.1:\d+", str(container_gw)
                ):
                    diags.append(
                        LintDiagnostic(
                            rule_id="DOC-REG-001",
                            severity="error",
                            file=rel_path,
                            line=1,
                            message=(
                                f"Invalid endpoints.container_gateway '{container_gw}'; must bind "
                                f"to explicit container gateway 'http(s)://10.250.0.1:<port>'"
                            ),
                            remediation_hint=(
                                "Set endpoints.container_gateway to 'http://10.250.0.1:<port>'."
                            ),
                        )
                    )

        triggers = parsed.frontmatter.get("triggers")
        if triggers is not None:
            if not isinstance(triggers, dict):
                diags.append(
                    LintDiagnostic(
                        rule_id="DOC-REG-001",
                        severity="error",
                        file=rel_path,
                        line=1,
                        message="Frontmatter 'triggers' must be a key-value mapping",
                        remediation_hint=(
                            "Define 'triggers' as a mapping containing 'alerts', 'cli_commands', "
                            "or 'mcp_tools' string lists."
                        ),
                    )
                )
            else:
                for trig_key in ("alerts", "cli_commands", "mcp_tools"):
                    trig_val = triggers.get(trig_key)
                    if trig_val is not None and (
                        not isinstance(trig_val, list)
                        or not all(isinstance(x, str) and bool(x.strip()) for x in trig_val)
                    ):
                        diags.append(
                            LintDiagnostic(
                                rule_id="DOC-REG-001",
                                severity="error",
                                file=rel_path,
                                line=1,
                                message=(
                                    f"Frontmatter 'triggers.{trig_key}' must be a list of "
                                    f"non-empty strings"
                                ),
                                remediation_hint=(
                                    f"Ensure every entry under 'triggers.{trig_key}' is a "
                                    f"non-empty string."
                                ),
                            )
                        )

    # Check watched_paths glob existence (DEAD_WATCHED_PATH_GLOB)
    if repo_root is not None and parsed.kb_document is not None:
        root_res = repo_root.resolve()
        for glob_pat in parsed.kb_document.watched_paths:
            matched = list(root_res.glob(glob_pat))
            if not matched and "**" in glob_pat:
                base_glob = glob_pat.replace("/**", "")
                matched = list(root_res.glob(base_glob))
            if not matched:
                diags.append(
                    LintDiagnostic(
                        rule_id="DEAD_WATCHED_PATH_GLOB",
                        severity="error",
                        file=rel_path,
                        line=1,
                        message=(
                            f"watched_paths glob '{glob_pat}' matches 0 files in repository "
                            f"'{root_res.name}'"
                        ),
                        remediation_hint=(
                            f"Update 'watched_paths' in '{rel_path}' so '{glob_pat}' matches "
                            f"at least one existing file or directory in the repository."
                        ),
                    )
                )

    raw_text = file_path.read_text(encoding="utf-8")
    for secret_hit in detect_hard_secrets(raw_text):
        diags.append(
            LintDiagnostic(
                rule_id="DOC-SEC-001",
                severity="error",
                file=rel_path,
                line=1,
                message=f"Hard secret pattern detected ({secret_hit})",
                remediation_hint=(
                    "Remove the literal credential/key and replace with an OpenBao reference "
                    "or placeholder variable."
                ),
            )
        )

    # Scan prose lines outside fenced code blocks
    _, body, body_start_line, _ = _extract_frontmatter(raw_text)
    in_fence = False
    fence_marker = ""
    section_words = 0
    section_line = body_start_line
    section_heading = "preamble"
    awaiting_first_prose_after_heading = False

    for idx, line in enumerate(body.splitlines()):
        line_no = body_start_line + idx
        stripped = line.strip()
        if not in_fence:
            m_open = _FENCE_OPEN_RE.match(stripped)
            if m_open:
                in_fence = True
                fence_marker = m_open.group(1)
                awaiting_first_prose_after_heading = False
                continue
        else:
            if stripped.startswith(fence_marker) and stripped == fence_marker[0] * len(stripped):
                in_fence = False
                fence_marker = ""
            continue

        m_head = _HEADING_RE.match(line)
        if m_head:
            if section_words > MAX_SECTION_WORDS:
                diags.append(
                    LintDiagnostic(
                        rule_id="DOC-CHUNK-001",
                        severity="warning",
                        file=rel_path,
                        line=section_line,
                        message=(
                            f"Section '{section_heading}' has {section_words} prose words "
                            f"(exceeds MAX_SECTION_WORDS={MAX_SECTION_WORDS})"
                        ),
                        remediation_hint=(
                            f"Split section '{section_heading}' into smaller H2/H3 subsections "
                            f"(<={MAX_SECTION_WORDS} words) for self-contained RAG chunking."
                        ),
                    )
                )
            section_heading = m_head.group(2).strip()
            section_line = line_no
            section_words = 0
            awaiting_first_prose_after_heading = len(m_head.group(1)) in (2, 3)
            continue

        # Strip inline backticks before prose rule scanning so meta-docs discussing rules don't
        # self-trigger
        prose_without_code = _INLINE_CODE_RE.sub("", line)
        if awaiting_first_prose_after_heading and prose_without_code.strip():
            if _AMBIGUOUS_SUBJECT_RE.match(prose_without_code.strip()):
                diags.append(
                    LintDiagnostic(
                        rule_id="DOC-PROSE-002",
                        severity="warning",
                        file=rel_path,
                        line=line_no,
                        message=(
                            f"Section '{section_heading}' starts with an unanchored pronoun; "
                            f"name the explicit service or component"
                        ),
                        remediation_hint=(
                            f"Replace the leading pronoun at line {line_no} with the explicit "
                            f"component or service name."
                        ),
                    )
                )
            awaiting_first_prose_after_heading = False

        if _BARE_LOCALHOST_RE.search(prose_without_code):
            diags.append(
                LintDiagnostic(
                    rule_id="DOC-PORT-001",
                    severity="warning",
                    file=rel_path,
                    line=line_no,
                    message=(
                        "Bare 'localhost:<port>' detected; specify explicit host loopback "
                        "('127.0.0.1:<port>') or container gateway ('10.250.0.1:<port>')"
                    ),
                    remediation_hint=(
                        f"Replace 'localhost:<port>' at line {line_no} with '127.0.0.1:<port>' "
                        f"or '10.250.0.1:<port>'."
                    ),
                )
            )

        section_words += len(prose_without_code.split())
        low_prose = prose_without_code.lower()

        for phrase in BANNED_DEICTIC_PHRASES:
            if phrase in low_prose:
                diags.append(
                    LintDiagnostic(
                        rule_id="DOC-PROSE-001",
                        severity="error",
                        file=rel_path,
                        line=line_no,
                        message=(
                            f"Cross-chunk deictic phrase '{phrase}' destroys chunk independence "
                            f"when retrieved in isolation"
                        ),
                        remediation_hint=(
                            f"Replace '{phrase}' at line {line_no} with the explicit step ID, "
                            f"section title, or command name."
                        ),
                    )
                )

        for retired, replacement in BANNED_RETIRED_TOOLS.items():
            if re.search(rf"\b{re.escape(retired)}\b", low_prose):
                diags.append(
                    LintDiagnostic(
                        rule_id="DOC-STACK-001",
                        severity="error",
                        file=rel_path,
                        line=line_no,
                        message=(
                            f"Retired tool '{retired}' referenced in prose; "
                            f"use canonical '{replacement}'"
                        ),
                        remediation_hint=(
                            f"Replace '{retired}' at line {line_no} with '{replacement}'."
                        ),
                    )
                )

        # Check relative Markdown links (BROKEN_RELATIVE_LINK)
        for m_link in _MD_LINK_RE.finditer(prose_without_code):
            target = m_link.group(2).strip()
            if not target or target.startswith(
                (
                    "#",
                    "http://",
                    "https://",
                    "mailto:",
                    "conversation://",
                    "file://",
                )
            ):
                continue
            path_part = target.split("#", 1)[0]
            if not path_part:
                continue
            target_path = (file_path.parent / path_part).resolve()
            # Only enforce existence for links resolving inside the repository
            if repo_root is not None and not target_path.is_relative_to(repo_root.resolve()):
                continue
            if not target_path.exists():
                diags.append(
                    LintDiagnostic(
                        rule_id="BROKEN_RELATIVE_LINK",
                        severity="error",
                        file=rel_path,
                        line=line_no,
                        message=(
                            f"Broken relative link '{target}' "
                            f"(resolved target '{path_part}' does not exist)"
                        ),
                        remediation_hint=(
                            f"Fix the relative path '{target}' at line {line_no} to point to "
                            f"an existing file in the repository."
                        ),
                    )
                )

    if section_words > MAX_SECTION_WORDS:
        diags.append(
            LintDiagnostic(
                rule_id="DOC-CHUNK-001",
                severity="warning",
                file=rel_path,
                line=section_line,
                message=(
                    f"Section '{section_heading}' has {section_words} prose words "
                    f"(exceeds MAX_SECTION_WORDS={MAX_SECTION_WORDS})"
                ),
                remediation_hint=(
                    f"Split section '{section_heading}' into smaller H2/H3 subsections "
                    f"(<={MAX_SECTION_WORDS} words) for self-contained RAG chunking."
                ),
            )
        )

    return diags


def lint_repo(
    target_path: Path,
    *,
    strict: bool = False,
    require_frontmatter: bool = False,
    verify_registry: bool = False,
) -> dict[str, Any]:
    """Lint a single Markdown file or all governed docs in a repository."""
    resolved = target_path.resolve()
    repo_root = resolved if resolved.is_dir() else resolved.parent
    for parent in [repo_root, *repo_root.parents]:
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            repo_root = parent
            break

    if resolved.is_file():
        files = [resolved]
    else:
        if (resolved / "docs").is_dir():
            raw_files = sorted((resolved / "docs").rglob("*.md"))
        else:
            raw_files = sorted(resolved.rglob("*.md"))
        files = [f for f in raw_files if _is_within_root(f, resolved)]

    all_diags: list[LintDiagnostic] = []
    for f in files:
        # Require frontmatter when explicitly requested, on docs/doc-tools/*.md, or when the file
        # already begins with a YAML frontmatter block
        req_fm = (
            require_frontmatter
            or "doc-tools" in f.parts
            or f.read_text(encoding="utf-8").startswith("---\n")
        )
        all_diags.extend(
            lint_markdown_file(
                f,
                repo_root=repo_root,
                require_frontmatter=req_fm,
                verify_registry=verify_registry,
            )
        )

    # Optional external Vale integration if installed and .vale.ini exists
    vale_bin = shutil.which("vale")
    vale_ini = repo_root / ".vale.ini"
    if vale_bin and vale_ini.exists() and files:
        try:
            res = subprocess.run(  # nosec B603 # reason: fixed vale binary resolved via shutil.which with confined file args
                [vale_bin, "--config", str(vale_ini), "--output=JSON", *[str(f) for f in files]],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if res.stdout.strip().startswith("{"):
                vale_data = json.loads(res.stdout)
                for fname, alerts in vale_data.items():
                    for al in alerts:
                        sev = "error" if al.get("Severity") == "error" else "warning"
                        all_diags.append(
                            LintDiagnostic(
                                rule_id=str(al.get("Check", "Vale.Rule")),
                                severity=sev,
                                file=str(fname),
                                line=int(al.get("Line", 1)),
                                message=str(al.get("Message", "")),
                                remediation_hint=(
                                    "Address the Vale prose rule violation so the section "
                                    "remains self-contained."
                                ),
                            )
                        )
        except (OSError, subprocess.SubprocessError, ValueError):
            # Optional external linter failure must not abort pure-Python linting
            res = None

    errors = [d.to_dict() for d in all_diags if d.severity == "error"]
    warnings = [d.to_dict() for d in all_diags if d.severity == "warning"]
    failed = bool(errors) or (strict and bool(warnings))
    return {
        "status": "failed" if failed else "ok",
        "files_checked": len(files),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }


def run_governed_lint(
    target_path: Path,
    *,
    strict: bool = False,
    verify_registry: bool = False,
) -> dict[str, Any]:
    """Run governed documentation lint requiring deterministic YAML frontmatter on all files."""
    return lint_repo(
        target_path,
        strict=strict,
        require_frontmatter=True,
        verify_registry=verify_registry,
    )


def _build_scrubbed_env() -> dict[str, str]:
    """Return a copy of os.environ with all secret/token/DB variables stripped."""
    clean: dict[str, str] = {}
    for k, v in os.environ.items():
        if _SENSITIVE_ENV_RE.search(k):
            continue
        clean[k] = v
    return clean


def smoke_run_green_steps(
    repo_path: Path,
    *,
    doc_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Verify and execute (or syntax-check in dry_run) only tier='green' mode='read-only' steps."""
    root = repo_path.resolve()
    bound_root = root if root.is_dir() else root.parent
    raw_files = (
        [root]
        if root.is_file()
        else sorted((root / "docs").rglob("*.md"))
        if (root / "docs").is_dir()
        else sorted(root.rglob("*.md"))
    )
    md_files = [f for f in raw_files if _is_within_root(f, bound_root)]

    results: list[dict[str, Any]] = []
    passed = 0
    failed = 0
    scrubbed_env = _build_scrubbed_env()

    for f in md_files:
        parsed = parse_markdown_document(f, repo_root=root if root.is_dir() else root.parent)
        if (
            doc_id
            and (parsed.kb_document is None or parsed.kb_document.doc_id != doc_id)
            and f.stem != doc_id
        ):
            continue

        for step in parsed.steps:
            if step.tier != "green" or step.mode != "read-only":
                continue

            safety_issues = check_green_step_safety(step.command_body)
            if safety_issues:
                failed += 1
                results.append(
                    {
                        "step_uid": step.step_uid,
                        "step_id": step.step_id,
                        "doc_id": step.doc_id,
                        "status": "blocked_by_ast_guard",
                        "violations": safety_issues,
                    }
                )
                continue

            if dry_run:
                proc = subprocess.run(  # nosec B603 B607 # reason: bash -n syntax-only dry run with scrubbed env and INV-STEP-02 guard
                    ["bash", "-n"],
                    input=step.command_body,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=scrubbed_env,
                    check=False,
                )
                ok = proc.returncode == 0
                if ok:
                    passed += 1
                else:
                    failed += 1
                results.append(
                    {
                        "step_uid": step.step_uid,
                        "step_id": step.step_id,
                        "doc_id": step.doc_id,
                        "mode": "dry_run_syntax_check",
                        "status": "ok" if ok else "failed",
                        "exit_code": proc.returncode,
                        "stderr": proc.stderr[:MAX_SMOKE_OUTPUT_BYTES],
                    }
                )
            else:
                try:
                    proc = subprocess.run(  # nosec B603 B607 # reason: read-only tier=green probe verified by check_green_step_safety with scrubbed env
                        ["bash", "-c", step.command_body],
                        capture_output=True,
                        text=True,
                        timeout=step.timeout_sec,
                        env=scrubbed_env,
                        cwd=str(root if root.is_dir() else root.parent),
                        check=False,
                    )
                    ok = proc.returncode == step.expect_exit
                    if ok:
                        passed += 1
                    else:
                        failed += 1
                    results.append(
                        {
                            "step_uid": step.step_uid,
                            "step_id": step.step_id,
                            "doc_id": step.doc_id,
                            "mode": "live_green_probe",
                            "status": "ok" if ok else "failed",
                            "exit_code": proc.returncode,
                            "expect_exit": step.expect_exit,
                            "stdout": proc.stdout[:MAX_SMOKE_OUTPUT_BYTES],
                            "stderr": proc.stderr[:MAX_SMOKE_OUTPUT_BYTES],
                        }
                    )
                except subprocess.TimeoutExpired:
                    failed += 1
                    results.append(
                        {
                            "step_uid": step.step_uid,
                            "step_id": step.step_id,
                            "doc_id": step.doc_id,
                            "mode": "live_green_probe",
                            "status": "timeout",
                            "timeout_sec": step.timeout_sec,
                        }
                    )

    return {
        "status": "failed" if failed > 0 else "ok",
        "dry_run": dry_run,
        "passed": passed,
        "failed": failed,
        "steps": results,
    }


def _validate_git_ref(ref: str, param_name: str) -> str:
    """Validate git revision ref to prevent option injection (e.g. --output=...)."""
    cleaned = ref.strip()
    if not cleaned or cleaned.startswith("-") or not _GIT_REF_RE.fullmatch(cleaned):
        raise ValidationError(
            f"Invalid {param_name} '{ref}'; git ref must match {_GIT_REF_RE.pattern} and not "
            f"start with '-'"
        )
    return cleaned


def check_repo_drift(
    repo_path: Path,
    *,
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    changed_files: Sequence[str] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Detect code-to-doc drift against `watched_paths` globs and `last_verified` TTL expiry."""
    root = repo_path.resolve()
    ref_date = today or date.today()
    safe_base = _validate_git_ref(base_ref, "base_ref")
    safe_head = _validate_git_ref(head_ref, "head_ref")

    if changed_files is not None:
        modified = set(changed_files)
    else:
        modified = set()
        res = subprocess.run(  # nosec B603 B607 # reason: read-only git diff inside confined repository root with validated refs
            ["git", "diff", "--name-only", f"{safe_base}...{safe_head}", "--"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            modified.update(ln.strip() for ln in res.stdout.splitlines() if ln.strip())
        wt_res = subprocess.run(  # nosec B603 B607 # reason: read-only git status inside confined repository root
            ["git", "status", "--porcelain"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        if wt_res.returncode == 0:
            for ln in wt_res.stdout.splitlines():
                if len(ln) > 3:
                    path_str = ln[3:].strip().split(" -> ")[-1]
                    modified.add(path_str)

    docs_dir = root / "docs"
    raw_files = sorted(docs_dir.rglob("*.md")) if docs_dir.is_dir() else sorted(root.glob("*.md"))
    md_files = [f for f in raw_files if _is_within_root(f, root)]

    findings: list[dict[str, Any]] = []
    docs_scanned = 0

    for f in md_files:
        parsed = parse_markdown_document(f, repo_root=root)
        if not parsed.has_frontmatter or parsed.kb_document is None:
            continue
        doc = parsed.kb_document
        if doc.status in {"deprecated", "archived"}:
            continue
        docs_scanned += 1

        # 0. Check for dead watched_paths globs (DEAD_WATCHED_PATH_GLOB)
        for glob_pat in doc.watched_paths:
            matched_glob = list(root.glob(glob_pat))
            if not matched_glob and "**" in glob_pat:
                matched_glob = list(root.glob(glob_pat.replace("/**", "")))
            if not matched_glob:
                findings.append(
                    {
                        "kind": "DEAD_WATCHED_PATH_GLOB",
                        "doc_id": doc.doc_id,
                        "file": doc.repo_path,
                        "glob": glob_pat,
                        "message": (
                            f"Document '{doc.doc_id}' watched_paths glob '{glob_pat}' matches "
                            f"0 files in repository '{root.name}'"
                        ),
                        "remediation_hint": (
                            f"Update 'watched_paths' in '{doc.repo_path}' so '{glob_pat}' "
                            f"matches at least one existing file or directory."
                        ),
                    }
                )

        # 1. Check watched_paths against modified files (only if the doc itself wasn't updated in
        # the same diff)
        if doc.repo_path not in modified:
            matched_watched: list[str] = []
            for mod_file in sorted(modified):
                for pat in doc.watched_paths:
                    if fnmatch.fnmatch(mod_file, pat) or (
                        pat.endswith("/**") and mod_file.startswith(pat[:-3].rstrip("/") + "/")
                    ):
                        matched_watched.append(mod_file)
                        break
            if matched_watched:
                findings.append(
                    {
                        "kind": "WATCHED_PATH_MODIFIED",
                        "doc_id": doc.doc_id,
                        "file": doc.repo_path,
                        "touched_watched_paths": matched_watched,
                        "message": (
                            f"Document '{doc.doc_id}' watches modified path(s) "
                            f"{matched_watched} but was not updated"
                        ),
                        "remediation_hint": (
                            f"Review '{doc.repo_path}' against changes in "
                            f"{matched_watched} and bump 'last_verified'."
                        ),
                    }
                )

        # 2. Check TTL staleness
        try:
            verified_dt = date.fromisoformat(doc.last_verified)
            age_days = (ref_date - verified_dt).days
            if age_days > doc.review_ttl_days:
                findings.append(
                    {
                        "kind": "TTL_STALE",
                        "doc_id": doc.doc_id,
                        "file": doc.repo_path,
                        "last_verified": doc.last_verified,
                        "age_days": age_days,
                        "review_ttl_days": doc.review_ttl_days,
                        "message": (
                            f"Document '{doc.doc_id}' last_verified ({doc.last_verified}) is "
                            f"{age_days} days old (exceeds TTL {doc.review_ttl_days}d)"
                        ),
                        "remediation_hint": (
                            f"Verify all commands and contracts in '{doc.repo_path}' and "
                            f"update 'last_verified: {ref_date.isoformat()}'."
                        ),
                    }
                )
        except ValueError:
            pass

    return {
        "status": "drift_detected" if findings else "ok",
        "documents_scanned": docs_scanned,
        "modified_files_count": len(modified),
        "finding_count": len(findings),
        "findings": findings,
    }


def _scrub_manifest_text(text: str) -> str:
    """Scrub P14 control/bidi/tag characters and redact all D14 secret classes before export."""
    if has_dangerous_unicode(text):
        text = _CTRL_AND_BIDI_RE.sub("", text)
    if detect_hard_secrets(text):
        for _sid, pattern in _SECRET_PATTERNS:
            text = pattern.sub("[REDACTED-BY-D14]", text)
    return text


def export_repo_llms(
    repo_path: Path,
    *,
    write_files: bool = False,
    include_full: bool = True,
) -> dict[str, Any]:
    """Generate deterministic, prompt-cache friendly `/llms.txt` and `/llms-full.txt` manifests."""
    root = repo_path.resolve()
    repo_name = root.name

    candidates: list[Path] = []
    for root_doc in ("README.md", "AGENTS.md", "portal/README.md"):
        p = root / root_doc
        if p.is_file() and _is_within_root(p, root):
            candidates.append(p)
    if (root / "docs").is_dir():
        candidates.extend(
            f for f in sorted((root / "docs").rglob("*.md")) if _is_within_root(f, root)
        )
    if (root / "sdd").is_dir():
        candidates.extend(
            f for f in sorted((root / "sdd").rglob("*.yaml")) if _is_within_root(f, root)
        )

    entries: list[tuple[str, str, str, str, str, str, str]] = []
    # tuple: (sort_group, kind, doc_id, rel_path, title, summary, body_clean)
    for f in candidates:
        if f.suffix.lower() == ".yaml":
            try:
                rel_path = str(f.resolve().relative_to(root))
            except ValueError:
                rel_path = f.name
            raw_yaml = f.read_text(encoding="utf-8")
            try:
                loaded_yaml = yaml.safe_load(raw_yaml)
            except yaml.YAMLError:
                loaded_yaml = {}
            if not isinstance(loaded_yaml, dict):
                loaded_yaml = {}
            yaml_status = str(loaded_yaml.get("status", "active")).lower()
            if yaml_status not in {"active", "accepted", "approved"}:
                continue
            doc_id = str(loaded_yaml.get("id") or build_doc_id(repo_name, rel_path))
            title = str(loaded_yaml.get("title") or loaded_yaml.get("name") or f.stem)
            kind = str(loaded_yaml.get("kind") or "sdd")
            summary = f"{title} (`id: {doc_id}`, `kind: {kind}`)"
            entries.append(
                (
                    "2_sdd",
                    kind,
                    doc_id,
                    rel_path,
                    title,
                    _scrub_manifest_text(summary),
                    _scrub_manifest_text(raw_yaml.strip()),
                )
            )
            continue

        parsed = parse_markdown_document(f, repo_root=root)
        rel_path = parsed.rel_path
        if parsed.kb_document is not None:
            if parsed.kb_document.status != "active":
                continue
            doc_id = parsed.kb_document.doc_id
            title = parsed.kb_document.title
            kind = parsed.kb_document.kind
            tier = parsed.kb_document.autonomy_tier
            summary = f"{title} (`id: {doc_id}`, `kind: {kind}`, `tier: {tier}`)"
            sort_group = (
                f"0_{kind}" if rel_path not in ("README.md", "AGENTS.md") else f"00_{rel_path}"
            )
        else:
            fm_status = str(parsed.frontmatter.get("status", "active")).lower()
            if fm_status != "active":
                continue
            doc_id = build_doc_id(repo_name, rel_path)
            kind = str(parsed.frontmatter.get("kind", "doc"))
            title = resolved_title = f.stem
            for ln in parsed.body_text.splitlines():
                if ln.startswith("# "):
                    resolved_title = ln[2:].strip()
                    break
            title = resolved_title
            summary = f"{title} (`id: {doc_id}`)"
            sort_group = "00_root" if rel_path in ("README.md", "AGENTS.md") else "1_docs"

        clean_body = _scrub_manifest_text(parsed.body_text.strip())
        entries.append(
            (
                sort_group,
                kind,
                doc_id,
                rel_path,
                title,
                _scrub_manifest_text(summary),
                clean_body,
            )
        )

    # Deterministic sorting by (sort_group, kind, doc_id, rel_path) for KV-cache prefix stability
    entries.sort(key=lambda item: (item[0], item[1], item[2], item[3]))

    llms_lines = [
        f"# {repo_name}",
        "",
        (
            "> Deterministic AI-First Repository Documentation & Executable Runbook Index "
            "(`engramory docs export-llms`)"
        ),
        "",
        "## Core Documents & Runbooks",
    ]
    full_lines = [
        f"# {repo_name} — Full Canonical Context (`/llms-full.txt`)",
        "",
    ]

    for _, _kind, doc_id, rel_path, title, summary, clean_body in entries:
        llms_lines.append(f"- [{rel_path}]({rel_path}): {summary}")
        if include_full:
            full_lines.append("---")
            full_lines.append(f"## [{doc_id}] {title} (`{rel_path}`)")
            full_lines.append("")
            full_lines.append(clean_body)
            full_lines.append("")

    llms_txt = "\n".join(llms_lines) + "\n"
    llms_full_txt = ("\n".join(full_lines) + "\n") if include_full else ""

    if write_files:
        (root / "llms.txt").write_text(llms_txt, encoding="utf-8")
        if include_full:
            (root / "llms-full.txt").write_text(llms_full_txt, encoding="utf-8")

    return {
        "status": "ok",
        "repo": repo_name,
        "document_count": len(entries),
        "wrote_files": write_files,
        "llms_txt": llms_txt,
        "llms_full_txt": llms_full_txt if include_full else None,
    }


def format_runbook_slice(
    steps: Sequence[Mapping[str, Any]],
    *,
    memories: Sequence[Mapping[str, Any]] = (),
    token_budget: int = 2000,
    output_format: str = "json",
) -> dict[str, Any]:
    """Order static L0 runbook steps before dynamic L2 memories and enforce `token_budget`."""
    # Sort static L0 steps deterministically by (doc_id, step_order) for Anthropic prefix KV-cache
    # stability
    ordered_steps = sorted(
        steps,
        key=lambda s: (str(s.get("doc_id", "")), int(s.get("step_order", 0))),
    )

    budget_Steps: list[dict[str, Any]] = []
    used_tokens = 0

    for st in ordered_steps:
        step_dict = dict(st)
        est_tokens = max(
            1,
            int(
                (
                    len(str(step_dict.get("command_body", "")).split())
                    + len(str(step_dict.get("prose_context", "")).split())
                    + len(str(step_dict.get("heading_path", "")).split())
                )
                * 1.3
            ),
        )
        if budget_Steps and used_tokens + est_tokens > token_budget:
            break
        used_tokens += est_tokens
        budget_Steps.append(step_dict)

    # Dynamic L2 memories always come AFTER static L0 canonical steps
    budget_memories: list[dict[str, Any]] = []
    for mem in memories:
        mem_dict = dict(mem)
        est_tokens = max(1, int(len(str(mem_dict.get("content", "")).split()) * 1.3))
        if used_tokens + est_tokens > token_budget:
            break
        used_tokens += est_tokens
        budget_memories.append(mem_dict)

    result: dict[str, Any] = {
        "status": "ok",
        "token_budget": token_budget,
        "estimated_tokens": used_tokens,
        "step_count": len(budget_Steps),
        "l0_steps": budget_Steps,
        "l2_memories": budget_memories,
    }

    if output_format == "markdown":
        md_lines = ["# Runbook Context Slice (L0 Canonical Steps -> L2 Episodic Memory)", ""]
        md_lines.append("## 1. L0 Canonical Runbook Steps")
        for s in budget_Steps:
            md_lines.append(
                f"### [{s.get('doc_id')}] Step {s.get('step_order')}: `{s.get('step_id')}` "
                f"(`tier={s.get('tier')}`, `mode={s.get('mode')}`)"
            )
            if s.get("heading_path"):
                md_lines.append(f"- **Section:** {s.get('heading_path')}")
            if s.get("prose_context"):
                md_lines.append(f"- **Context:** {s.get('prose_context')}")
            md_lines.append("```bash")
            md_lines.append(str(s.get("command_body", "")))
            md_lines.append("```")
            md_lines.append("")
        if budget_memories:
            md_lines.append("## 2. L2 Distilled Operational Memories")
            for m in budget_memories:
                md_lines.append(f"- **{m.get('id', 'mem')}**: {m.get('content', '')}")
        result["markdown"] = "\n".join(md_lines)

    return result


def slice_local_runbooks(
    repo_path: Path,
    *,
    doc_id: str | None = None,
    alert_id: str | None = None,
    max_tier: str | None = None,
    token_budget: int = 2000,
    output_format: str = "json",
) -> dict[str, Any]:
    """Slice executable runbook steps directly from local Markdown files (Tier-1 offline mode)."""
    root = repo_path.resolve()
    bound_root = root if root.is_dir() else root.parent
    raw_files = (
        [root]
        if root.is_file()
        else sorted((root / "docs").rglob("*.md"))
        if (root / "docs").is_dir()
        else sorted(root.rglob("*.md"))
    )
    md_files = [f for f in raw_files if _is_within_root(f, bound_root)]

    if max_tier is not None and max_tier not in TIER_ORDER:
        raise ValidationError(f"Invalid max_tier '{max_tier}'; must be one of {sorted(TIER_ORDER)}")
    max_rank = TIER_ORDER[max_tier] if max_tier else 2
    matched_steps: list[dict[str, Any]] = []

    for f in md_files:
        parsed = parse_markdown_document(f, repo_root=root if root.is_dir() else root.parent)
        if parsed.kb_document is None:
            continue
        doc = parsed.kb_document
        if doc.status in {"deprecated", "archived"}:
            continue
        if doc_id and doc.doc_id != doc_id and f.stem != doc_id:
            continue
        if alert_id and alert_id not in doc.trigger_alerts:
            continue

        for st in parsed.steps:
            if TIER_ORDER.get(st.tier, 2) > max_rank:
                continue
            matched_steps.append(
                {
                    "step_uid": st.step_uid,
                    "doc_id": st.doc_id,
                    "title": doc.title,
                    "repo_path": doc.repo_path,
                    "step_order": st.step_order,
                    "step_id": st.step_id,
                    "heading_path": st.heading_path,
                    "tier": st.tier,
                    "mode": st.mode,
                    "expect_exit": st.expect_exit,
                    "timeout_sec": st.timeout_sec,
                    "command_body": st.command_body,
                    "prose_context": st.prose_context,
                    "rollback_step": st.rollback_step,
                    "trigger_alerts": list(doc.trigger_alerts),
                }
            )

    return format_runbook_slice(
        matched_steps,
        memories=(),
        token_budget=token_budget,
        output_format=output_format,
    )


def stage_portal_bundle(
    repo_root: Path,
    *,
    output_dir: Path | None = None,
    dry_run: bool = False,
    allowed_root: Path | None = None,
) -> dict[str, Any]:
    """Re-export `stage_portal_bundle` from `engramory.core.portal_builder`."""
    from engramory.core.portal_builder import stage_portal_bundle as _stage_portal_bundle

    return _stage_portal_bundle(
        repo_root,
        output_dir=output_dir,
        dry_run=dry_run,
        allowed_root=allowed_root,
    )
