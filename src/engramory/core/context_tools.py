"""Built-In SDD Context Tools Engine (`aidoc-context-tools` migration, PLAN-016).

Consolidates the complete `aidoc-context-tools` capability set into `engramory`
as a first-class built-in module:
- `ArtifactNode`, `CANONICAL_LAYERS`, and `normalize_layer`
- `ScopedContractResolver` (cross-project and local `@project:layer:tag` pointers,
  with path-confinement and multi-project boundary enforcement)
- `AidocDependencyGraph` (local/offline SDD-GRAPH v2.0, YAML, and Markdown scanner,
  DFS cycle detection, `GRAPH-CARD-001..003` semantic cardinality invariants, and
  offline blast-radius `descendants`)
- `ContextSlicer`, `estimate_tokens`, and `compact_node_content` (Tier 0/1/2 token
  compaction and prompt digest assembly)
- `InvariantChecker` (pre-merge contract erosion guard & blast-radius advisories,
  hardened against git ref option injection)
- `StackPolicyValidator` (`.aidoc/stack-policy.yaml` banned patterns, PEP 503
  dependency allowlist across `pyproject.toml`/`uv.lock`/`requirements.txt`, and
  legacy `poetry.lock` rejection)
"""

from __future__ import annotations

import ast
import contextlib
import math
import os
import re
import stat
import subprocess  # nosec B404 # reason: controlled git diff invocations with validated refs
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from engramory.core.docs_tools import _GIT_REF_RE, _is_within_root
from engramory.core.kb_graph import TAG_RE
from engramory.core.models import ValidationError

MAX_SCAN_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB guard against oversized file / YAML DoS

CANONICAL_LAYERS: tuple[str, ...] = (
    "BRD",
    "PRD",
    "EARS",
    "BDD",
    "ADR",
    "SPEC",
    "TDD",
    "TASK",
    "IPLAN",
    "CHG",
    "EVAL",
)

DEFAULT_ALLOWED_LAYERS: set[str] = {
    "BRD",
    "PRD",
    "EARS",
    "BDD",
    "ADR",
    "SPEC",
    "TDD",
    "TASK",
    "IPLAN",
    "CHG",
}

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_POINTER_RE = re.compile(
    r"(?<![A-Za-z0-9_])(@(?:[A-Za-z0-9_\-]+:[A-Za-z0-9_\-:.]*[A-Za-z0-9_\-]"
    r"|(?:BRD|PRD|EARS|BDD|ADR|SPEC|TDD|TASK|IPLAN|CHG|EVAL)[-._][A-Za-z0-9_.\-]*[A-Za-z0-9_\-]))",
    re.IGNORECASE,
)
_SDD_ID_RE = re.compile(
    r"^(BRD|PRD|EARS|BDD|ADR|SPEC|TDD|TASK|IPLAN|CHG|EVAL)[-._]([A-Za-z0-9_.\-]+)$",
    re.IGNORECASE,
)

_EXCLUDED_SCAN_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        ".pytest_cache",
        ".tox",
        ".ci",
        ".ci-out",
        ".agents",
        ".unified-test-suite",
        "node_modules",
        "target",
        "build",
        "dist",
        ".ruff_cache",
        ".cache",
        "archive",
        "history",
        ".worktrees",
        ".mimocode",
        ".muse",
    }
)


def normalize_layer(raw_layer: str | int | None) -> str:
    """Normalize a layer string or number (e.g. '01_BRD' -> 'BRD', '06_SPEC' -> 'SPEC')."""
    if raw_layer is None or raw_layer == "":
        return "UNKNOWN"
    if isinstance(raw_layer, int):
        int_map = {
            1: "BRD",
            2: "PRD",
            3: "EARS",
            4: "BDD",
            5: "ADR",
            6: "SPEC",
            7: "TDD",
            8: "IPLAN",
            9: "CHG",
            10: "EVAL",
        }
        return int_map.get(raw_layer, "UNKNOWN")
    clean = re.sub(r"^\d+[-_]", "", str(raw_layer).strip().upper())
    for layer in CANONICAL_LAYERS:
        if clean == layer or clean.startswith(layer):
            return layer
    return clean or "UNKNOWN"


def _normalize_sdd_key_lossless(raw_token: str) -> str:
    """Normalize an SDD identifier without truncating multi-segment dotted/underscore IDs."""
    token = raw_token.strip().lstrip("@")
    m = _SDD_ID_RE.match(token)
    if m:
        prefix = m.group(1).upper()
        rest = re.sub(r"[-._]+", "-", m.group(2).strip("-._")).upper()
        return f"{prefix}-{rest}" if rest else prefix
    return token.upper()


def _canonical_node_key(raw_id: str) -> str:
    """Return a lossless canonical comparison key for SDD node IDs and scoped pointers."""
    stripped = raw_id.strip().lstrip("@")
    if ":" in stripped:
        parts = [p.strip() for p in stripped.split(":")]
        if len(parts) == 2 and (
            normalize_layer(parts[0]) in CANONICAL_LAYERS or parts[0].upper() == "DEPENDS"
        ):
            # @spec:SPEC-01 -> canonicalize target if it starts with an SDD prefix
            tail = parts[1]
            if _SDD_ID_RE.match(tail):
                return _normalize_sdd_key_lossless(tail)
            return f"{normalize_layer(parts[0])}:{tail.upper()}"
        if len(parts) == 3:
            # Preserve project namespace on 3-part pointers: @proj-a:SPEC:SPEC-01
            proj, raw_l, tag = parts[0].upper(), normalize_layer(parts[1]), parts[2]
            norm_tag = _normalize_sdd_key_lossless(tag)
            return f"{proj}:{raw_l}:{norm_tag}"
        return stripped.upper()
    return _normalize_sdd_key_lossless(stripped)


def _is_safe_token(token: str) -> bool:
    """Ensure a pointer segment cannot traverse paths (no '..', '/', or '\\')."""
    return (
        bool(token)
        and token not in (".", "..")
        and ".." not in token
        and bool(_SAFE_TOKEN_RE.match(token))
    )


def _read_bounded_text(file_path: Path, *, max_bytes: int = MAX_SCAN_FILE_BYTES) -> str | None:
    """Read UTF-8 text from `file_path` only if it is a regular file within `max_bytes`."""
    try:
        st = file_path.stat()
        if not stat.S_ISREG(st.st_mode) or st.st_size > max_bytes:
            return None
        return file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _validate_git_ref(ref: str) -> str:
    """Validate a git revision string to block leading `-` option injection."""
    cleaned = ref.strip()
    if not cleaned or cleaned.startswith("-") or not _GIT_REF_RE.match(cleaned):
        raise ValidationError(f"Invalid or unsafe git ref: {ref!r}")
    return cleaned


@dataclass
class ArtifactNode:
    """Single SDD artifact node in the local/cross-project dependency DAG."""

    node_id: str
    layer: str  # BRD, PRD, EARS, BDD, ADR, SPEC, TDD, TASK, IPLAN, CHG, EVAL
    content: str
    dependencies: list[str] = field(default_factory=list)
    title: str = ""
    file_path: str | None = None
    is_external: bool = False

    def to_surface_dict(self) -> dict[str, Any]:
        """Serialize in a shape compatible with both `AccessSurface` and `aidoc-ctx`."""
        first_line = ""
        for ln in self.content.splitlines():
            s = ln.strip()
            if s and s != "---" and not s.startswith("#"):
                first_line = s[:240]
                break
        return {
            "id": self.node_id,
            "layer": self.layer,
            "title": self.title or self.node_id,
            "file_path": self.file_path or "",
            "labels": [self.layer],
            "properties": {
                "name": self.title or self.node_id,
                "title": self.title or self.node_id,
                "layer": self.layer,
                "path": self.file_path or "",
                "summary": first_line or (self.title or self.node_id),
            },
        }


class ScopedContractResolver:
    """Resolves cross-project and local contract pointers (`@project:layer:tag`)."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        allow_sibling_lookup: bool = True,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.allow_sibling_lookup = allow_sibling_lookup
        self.cache_dir = self.workspace_root / ".aidoc" / "contracts" / "cache"
        self.submodule_dir = self.workspace_root / ".aidoc" / "contracts"

    def _extract_contract_dependencies(self, content: str, file_ext: str) -> list[str]:
        """Extract `import` / `depends_on` references from contract files for multi-hop tracing."""
        deps: list[str] = []
        if file_ext == ".proto":
            for match in re.finditer(r'import\s+["\']([^"\']+)["\'];', content):
                import_path = match.group(1)
                base_tag = os.path.splitext(os.path.basename(import_path))[0]
                if _is_safe_token(base_tag):
                    deps.append(f"@proto:{base_tag}")
        elif file_ext in (".yaml", ".yml", ".sw.yaml"):
            with contextlib.suppress(Exception):
                doc = yaml.safe_load(content)
                if isinstance(doc, dict):
                    raw_deps = doc.get("depends_on") or doc.get("imports") or []
                    if isinstance(raw_deps, list):
                        deps.extend(str(x) for x in raw_deps if x)
                    elif isinstance(raw_deps, (str, int)):
                        deps.append(str(raw_deps))
        return list(dict.fromkeys(deps))

    def _get_search_paths(self, project_id: str) -> list[Path]:
        """Collect candidate external search paths when `allow_sibling_lookup` is enabled."""
        if not self.allow_sibling_lookup or not _is_safe_token(project_id):
            return []

        env_allowed_root = os.environ.get("ENGRAMORY_ALLOWED_DOCS_ROOT", "").strip()
        allowed_bound = Path(env_allowed_root).resolve() if env_allowed_root else None

        paths: list[Path] = []
        env_paths = os.environ.get("AIDOC_CONTRACT_PATH", "")
        if env_paths:
            for p in env_paths.split(os.pathsep):
                cand = Path(p.strip())
                if p.strip() and cand.is_dir():
                    resolved_cand = cand.resolve()
                    if allowed_bound is None or _is_within_root(resolved_cand, allowed_bound):
                        paths.append(resolved_cand)

        parent_dir = self.workspace_root.parent
        if parent_dir.is_dir():
            for name in (
                project_id,
                f"platform-{project_id}",
                f"aidoc-{project_id}",
                f"platform-{project_id}-contracts",
            ):
                cand = parent_dir / name
                if cand.is_dir() and _is_within_root(cand, parent_dir):
                    resolved_cand = cand.resolve()
                    if allowed_bound is None or _is_within_root(resolved_cand, allowed_bound):
                        paths.append(resolved_cand)

        return paths

    def resolve(self, pointer: str) -> ArtifactNode | None:
        """Resolve a pointer string to an `ArtifactNode` with confined path checks."""
        clean_ptr = pointer.strip().lstrip("@")
        parts = [p.strip() for p in clean_ptr.split(":")]

        if len(parts) == 3:
            project_id, raw_layer, tag_id = parts[0], parts[1], parts[2]
            layer = normalize_layer(raw_layer)
        elif len(parts) == 2:
            project_id = "local"
            raw_layer = parts[0]
            norm_l = normalize_layer(raw_layer)
            layer = norm_l if norm_l in CANONICAL_LAYERS else "SPEC"
            tag_id = parts[1]
        else:
            project_id = "local"
            tag_id = parts[0]
            prefix = tag_id.split("-")[0].split(".")[0].upper()
            layer = prefix if prefix in CANONICAL_LAYERS else "UNKNOWN"

        if (
            not _is_safe_token(tag_id)
            or not _is_safe_token(project_id)
            or not _is_safe_token(layer)
        ):
            return None

        # 1. Check in local workspace proto directory
        local_proto_dir = self.workspace_root / "proto"
        if local_proto_dir.is_dir() and _is_within_root(local_proto_dir, self.workspace_root):
            for ext in (".proto", ".yaml", ".json", ".sw.yaml"):
                candidate = local_proto_dir / f"{tag_id}{ext}"
                if candidate.is_file() and _is_within_root(candidate, self.workspace_root):
                    content = _read_bounded_text(candidate)
                    if content is not None:
                        deps = self._extract_contract_dependencies(content, ext)
                        rel = candidate.resolve().relative_to(self.workspace_root).as_posix()
                        return ArtifactNode(
                            node_id=pointer,
                            layer=layer if layer != "UNKNOWN" else "SPEC",
                            content=content,
                            dependencies=deps,
                            title=f"Local Contract: {tag_id}",
                            file_path=rel,
                            is_external=True,
                        )

        # 2. Check in local .aidoc/contracts/cache directory
        if self.cache_dir.is_dir() and _is_within_root(self.cache_dir, self.workspace_root):
            target_file = self.cache_dir / project_id / layer / f"{tag_id}.yaml"
            if target_file.is_file() and _is_within_root(target_file, self.workspace_root):
                content = _read_bounded_text(target_file)
                if content is not None:
                    deps = self._extract_contract_dependencies(content, ".yaml")
                    rel = target_file.resolve().relative_to(self.workspace_root).as_posix()
                    return ArtifactNode(
                        node_id=pointer,
                        layer=layer,
                        content=content,
                        dependencies=deps,
                        title=f"{project_id} - {tag_id}",
                        file_path=rel,
                        is_external=True,
                    )

        # 3. Check in .aidoc/contracts/proto submodule schemas
        if self.submodule_dir.is_dir() and _is_within_root(self.submodule_dir, self.workspace_root):
            for ext in (".proto", ".yaml", ".json", ".sw.yaml"):
                candidate = self.submodule_dir / "proto" / project_id / f"{tag_id}{ext}"
                if candidate.is_file() and _is_within_root(candidate, self.workspace_root):
                    content = _read_bounded_text(candidate)
                    if content is not None:
                        deps = self._extract_contract_dependencies(content, ext)
                        rel = candidate.resolve().relative_to(self.workspace_root).as_posix()
                        return ArtifactNode(
                            node_id=pointer,
                            layer=layer if layer != "UNKNOWN" else "SPEC",
                            content=content,
                            dependencies=deps,
                            title=f"Submodule Contract: {tag_id}",
                            file_path=rel,
                            is_external=True,
                        )

        # 4. Check in sibling repositories / search paths (only when allow_sibling_lookup=True)
        for search_root in self._get_search_paths(project_id):
            candidate_dirs = (
                search_root,
                search_root / "proto",
                search_root / "proto" / project_id,
                search_root / "docs" / "sdd" / "06_SPEC",
                search_root / "sdd" / "06_SPEC",
            )
            for c_dir in candidate_dirs:
                if not c_dir.is_dir() or not _is_within_root(c_dir, search_root):
                    continue
                for ext in (".proto", ".yaml", ".json", ".sw.yaml"):
                    candidate = c_dir / f"{tag_id}{ext}"
                    if candidate.is_file() and _is_within_root(candidate, search_root):
                        content = _read_bounded_text(candidate)
                        if content is not None:
                            deps = self._extract_contract_dependencies(content, ext)
                            rel_in_sibling = candidate.resolve().relative_to(search_root).as_posix()
                            return ArtifactNode(
                                node_id=pointer,
                                layer=layer if layer != "UNKNOWN" else "SPEC",
                                content=content,
                                dependencies=deps,
                                title=f"Sibling Contract: {project_id}/{tag_id}",
                                file_path=f"{project_id}/{rel_in_sibling}",
                                is_external=True,
                            )

        # 5. Fallback representation for unresolved pointer format
        return ArtifactNode(
            node_id=pointer,
            layer=layer,
            content=(
                f"# Imported Contract Pointer: {pointer}\n# Target: {project_id}:{layer}:{tag_id}\n"
            ),
            title=f"External: {pointer}",
            is_external=True,
        )


def _extract_yaml_deps(doc: dict[str, Any]) -> list[str]:
    """Extract dependency IDs from `depends_on`, `trace`, `traceability`, and ADR dependencies."""
    deps: list[str] = []
    for key in ("depends_on", "dependencies", "upstream"):
        raw = doc.get(key)
        if isinstance(raw, (str, int)):
            deps.append(str(raw))
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, (str, int)):
                    deps.append(str(item))
                elif isinstance(item, dict):
                    target = item.get("adr") or item.get("id") or item.get("target")
                    if target:
                        deps.append(str(target))

    rel_dec = doc.get("related_decisions")
    if isinstance(rel_dec, dict):
        for dep in rel_dec.get("dependencies", []) or []:
            if isinstance(dep, dict) and dep.get("adr"):
                deps.append(str(dep["adr"]))

    for trace_key in ("trace", "traceability"):
        trace = doc.get(trace_key)
        if isinstance(trace, dict):
            for v in trace.values():
                if isinstance(v, list):
                    deps.extend(str(x) for x in v if isinstance(x, (str, int)))
                elif isinstance(v, (str, int)):
                    deps.append(str(v))
        elif isinstance(trace, list):
            deps.extend(str(x) for x in trace if isinstance(x, (str, int)))

    return list(dict.fromkeys(d.strip() for d in deps if str(d).strip()))


def _extract_yaml_node_id(doc: dict[str, Any]) -> str | None:
    """Extract canonical node ID from top-level fields or `document_control`."""
    for key in ("id", "task_id", "doc_id", "iplan_id", "chg_id", "spec_id", "adr_id"):
        val = doc.get(key)
        if isinstance(val, (str, int)) and str(val).strip():
            return str(val).strip()
    doc_ctrl = doc.get("document_control")
    if isinstance(doc_ctrl, dict):
        for key in ("document_id", "id", "iplan_id", "chg_id", "spec_id", "adr_id"):
            val = doc_ctrl.get(key)
            if isinstance(val, (str, int)) and str(val).strip():
                return str(val).strip()
    return None


class AidocDependencyGraph:
    """Directed Acyclic Graph of SDD artifact nodes across local and external projects."""

    def __init__(
        self,
        external_resolver: Callable[[str], ArtifactNode | None] | None = None,
    ) -> None:
        self.nodes: dict[str, ArtifactNode] = {}
        self._key_index: dict[str, str] = {}
        self.external_resolver = external_resolver

    def add_node(self, node: ArtifactNode) -> None:
        self.nodes[node.node_id] = node
        self._key_index[_canonical_node_key(node.node_id)] = node.node_id
        clean_id = node.node_id.lstrip("@")
        self._key_index[clean_id.upper()] = node.node_id

    def get_node(self, node_id: str) -> ArtifactNode | None:
        if not node_id:
            return None
        if node_id in self.nodes:
            return self.nodes[node_id]

        clean_id = node_id.lstrip("@")
        if clean_id in self.nodes:
            return self.nodes[clean_id]
        at_id = f"@{clean_id}"
        if at_id in self.nodes:
            return self.nodes[at_id]

        canon_key = _canonical_node_key(node_id)
        mapped_id = self._key_index.get(canon_key) or self._key_index.get(clean_id.upper())
        if mapped_id and mapped_id in self.nodes:
            return self.nodes[mapped_id]

        if self.external_resolver and (node_id.startswith("@") or ":" in node_id):
            external_node = self.external_resolver(node_id)
            if external_node is not None:
                self.add_node(external_node)
                return external_node
        return None

    def detect_cycles(self) -> list[list[str]]:
        """Detect circular dependency cycles using DFS cycle detection."""
        visited: set[str] = set()
        rec_stack: set[str] = set()
        cycles: list[list[str]] = []

        def dfs(current_key: str, path: list[str]) -> None:
            visited.add(current_key)
            rec_stack.add(current_key)
            path.append(current_key)

            node = self.get_node(current_key)
            if node is not None:
                for parent_raw in node.dependencies:
                    parent_node = self.get_node(parent_raw)
                    parent_key = parent_node.node_id if parent_node else parent_raw
                    if parent_key not in visited:
                        dfs(parent_key, path.copy())
                    elif parent_key in rec_stack:
                        cycle_start = path.index(parent_key)
                        cycle = path[cycle_start:] + [parent_key]
                        if cycle not in cycles:
                            cycles.append(cycle)

            rec_stack.remove(current_key)

        for node_id in list(self.nodes.keys()):
            if node_id not in visited:
                dfs(node_id, [])

        return cycles

    def validate_invariants(self) -> tuple[bool, list[str]]:
        """Validate SDD-GRAPH v2.0 semantic cardinality and oracle trace rules.

        - GRAPH-CARD-001: Seed intent (BRD/PRD) decomposes to >= 1 capability/requirement.
        - GRAPH-CARD-002: Requirements (EARS) trace to authorized intent (PRD/BRD/ADR).
        - GRAPH-CARD-003: Oracle Invariant: every EARS requirement has >= 1 verifying BDD/TDD node.
        """
        violations: list[str] = []

        children_map: dict[str, set[str]] = {}
        for nid, node in self.nodes.items():
            for dep in node.dependencies:
                dep_node = self.get_node(dep)
                target_keys = {dep, dep.lstrip("@"), _canonical_node_key(dep)}
                if dep_node is not None:
                    target_keys.add(dep_node.node_id)
                    target_keys.add(dep_node.node_id.lstrip("@"))
                    target_keys.add(_canonical_node_key(dep_node.node_id))
                for k in target_keys:
                    children_map.setdefault(k, set()).add(nid)

        local_nodes = [
            n
            for n in self.nodes.values()
            if not n.is_external and not n.content.startswith("# Imported Contract Pointer:")
        ]
        ears_nodes = [n for n in local_nodes if n.layer == "EARS"]
        seed_nodes = [n for n in local_nodes if n.layer in ("BRD", "PRD")]

        # GRAPH-CARD-003: Oracle Invariant (EARS -> BDD/TDD)
        for ears in ears_nodes:
            lookup_keys = {
                ears.node_id,
                ears.node_id.lstrip("@"),
                _canonical_node_key(ears.node_id),
            }
            children: set[str] = set()
            for k in lookup_keys:
                children |= children_map.get(k, set())

            verifying_nodes = [
                c_node
                for c in children
                if (c_node := self.get_node(c)) is not None and c_node.layer in ("BDD", "TDD")
            ]
            direct_verifiers = [
                d_node
                for d in ears.dependencies
                if (d_node := self.get_node(d)) is not None and d_node.layer in ("BDD", "TDD")
            ]
            if not verifying_nodes and not direct_verifiers:
                violations.append(
                    f"GRAPH-CARD-003 (Oracle Invariant): EARS requirement '{ears.node_id}' "
                    "has 0 verifying BDD acceptance scenarios or TDD test cases."
                )

        # GRAPH-CARD-001: Seed intent decomposition (BRD/PRD -> EARS/SPEC/BDD/TASK/IPLAN)
        for seed in seed_nodes:
            lookup_keys = {
                seed.node_id,
                seed.node_id.lstrip("@"),
                _canonical_node_key(seed.node_id),
            }
            children = set()
            for k in lookup_keys:
                children |= children_map.get(k, set())

            downstream = [
                c_node
                for c in children
                if (c_node := self.get_node(c)) is not None
                and c_node.layer in ("EARS", "SPEC", "BDD", "TASK", "IPLAN")
            ]
            if not downstream and not seed.dependencies:
                violations.append(
                    f"GRAPH-CARD-001: Seed intent '{seed.node_id}' ({seed.layer}) "
                    "does not decompose into any downstream capability or requirement."
                )

        # GRAPH-CARD-002: Requirement realization (EARS -> PRD/BRD/ADR)
        for ears in ears_nodes:
            traces_to_intent = any(
                (d_node := self.get_node(d)) is not None and d_node.layer in ("BRD", "PRD", "ADR")
                for d in ears.dependencies
            )
            if seed_nodes and not traces_to_intent:
                violations.append(
                    f"GRAPH-CARD-002: EARS requirement '{ears.node_id}' "
                    "does not trace to any upstream seed intent (PRD/BRD)."
                )

        return len(violations) == 0, violations

    def descendants(self, doc_id: str, *, max_depth: int = 6) -> list[ArtifactNode]:
        """Compute downstream dependent nodes (blast radius) in the local DAG."""
        depth_limit = max(1, min(int(max_depth), 20))
        start_key = _canonical_node_key(doc_id)
        visited_keys: set[str] = {start_key}
        impacted: list[ArtifactNode] = []
        queue: list[tuple[str, int]] = [(doc_id, 0)]

        while queue:
            curr_id, d = queue.pop(0)
            if d >= depth_limit:
                continue
            curr_keys = {
                curr_id,
                curr_id.lstrip("@"),
                _canonical_node_key(curr_id),
            }
            curr_node = self.get_node(curr_id)
            if curr_node is not None:
                curr_keys.add(curr_node.node_id)
                curr_keys.add(curr_node.node_id.lstrip("@"))
                curr_keys.add(_canonical_node_key(curr_node.node_id))

            for nid, node in self.nodes.items():
                node_key = _canonical_node_key(nid)
                if node_key in visited_keys:
                    continue
                dep_keys = {
                    dep_key
                    for dep in node.dependencies
                    for dep_key in (dep, dep.lstrip("@"), _canonical_node_key(dep))
                }
                if curr_keys & dep_keys:
                    visited_keys.add(node_key)
                    impacted.append(node)
                    queue.append((nid, d + 1))

        return impacted

    def scan_workspace(self, root_dir: str | Path) -> int:
        """Scan workspace SDD directories and extract `ArtifactNode`s and `@`-pointers."""
        resolved_root = Path(root_dir).resolve()
        candidate_roots = [
            resolved_root / "sdd",
            resolved_root / "docs" / "sdd",
            resolved_root / "framework" / "layers",
        ]
        scan_dirs = [d for d in candidate_roots if d.is_dir() and _is_within_root(d, resolved_root)]
        if not scan_dirs:
            scan_dirs = [resolved_root]

        loaded_count = 0
        seen_files: set[Path] = set()

        for sdd_dir in scan_dirs:
            for dirpath, dirs, filenames in os.walk(sdd_dir, followlinks=False):
                dirs[:] = sorted(
                    d
                    for d in dirs
                    if not d.startswith(".")
                    and d not in _EXCLUDED_SCAN_DIRS
                    and not d.endswith("_cache")
                )

                dir_layer = "UNKNOWN"
                rel_dir = os.path.relpath(dirpath, sdd_dir)
                dir_parts = [p.upper() for p in re.split(r"[\\/_\-\s]+", rel_dir)]
                for layer_name in CANONICAL_LAYERS:
                    if layer_name in dir_parts or any(p.startswith(layer_name) for p in dir_parts):
                        dir_layer = layer_name
                        break

                for filename in sorted(filenames):
                    file_path = Path(dirpath) / filename
                    if not _is_within_root(file_path, resolved_root):
                        continue
                    resolved_file = file_path.resolve()
                    if resolved_file in seen_files:
                        continue
                    seen_files.add(resolved_file)
                    rel_path = resolved_file.relative_to(resolved_root).as_posix()

                    if filename.endswith((".yaml", ".yml")):
                        loaded_count += self._scan_yaml_file(resolved_file, rel_path, dir_layer)
                    elif filename.endswith(".md"):
                        loaded_count += self._scan_markdown_file(resolved_file, rel_path, dir_layer)

        return loaded_count

    def _scan_yaml_file(self, file_path: Path, rel_path: str, dir_layer: str) -> int:
        raw_text = _read_bounded_text(file_path)
        if raw_text is None:
            return 0
        count = 0
        try:
            docs = list(yaml.safe_load_all(raw_text))
        except Exception:  # noqa: BLE001 # reason: malformed YAML files should be skipped during workspace scan
            return 0

        for doc in docs:
            if not isinstance(doc, dict):
                continue

            is_sdd_graph = (
                doc.get("schema_version") in ("2.0-graph", "2.0")
                or doc.get("graph_type") == "SDD-GRAPH"
                or ("nodes" in doc and "edges" in doc and isinstance(doc["nodes"], list))
            )

            if is_sdd_graph:
                metadata = doc.get("metadata", {})
                meta_layer = metadata.get("layer") if isinstance(metadata, dict) else None
                doc_layer = normalize_layer(meta_layer or doc.get("layer") or dir_layer)

                node_types: dict[str, str] = {}
                for n in doc.get("nodes", []):
                    if isinstance(n, dict) and "id" in n:
                        n_id = str(n["id"]).strip()
                        n_type = str(n.get("type") or n.get("layer") or "").lower()
                        node_types[n_id] = n_type

                edge_map: dict[str, list[str]] = {}
                for edge in doc.get("edges", []):
                    if isinstance(edge, dict):
                        src = edge.get("from") or edge.get("source")
                        tgt = edge.get("to") or edge.get("target")
                        rel = str(edge.get("relation") or "").lower()
                        if src and tgt:
                            src_s, tgt_s = str(src).strip(), str(tgt).strip()
                            if rel == "compensated_by":
                                # Rollback edge is not an upstream dependency
                                continue
                            src_type = node_types.get(src_s, "")
                            if rel == "authorized_by" and (
                                src_s.upper().startswith("CHG") or src_type == "change_request"
                            ):
                                edge_map.setdefault(tgt_s, []).append(src_s)
                            else:
                                edge_map.setdefault(src_s, []).append(tgt_s)

                for n in doc.get("nodes", []):
                    if isinstance(n, dict) and "id" in n:
                        n_id = str(n["id"]).strip()
                        id_prefix = n_id.split(".")[0].split("-")[0].upper()
                        n_layer = normalize_layer(
                            n.get("layer")
                            or (id_prefix if id_prefix in CANONICAL_LAYERS else None)
                            or doc_layer
                        )
                        props = n.get("properties")
                        prop_title = props.get("title") if isinstance(props, dict) else None
                        n_title = n.get("label") or n.get("title") or prop_title or n_id
                        deps = list(edge_map.get(n_id, []))
                        deps.extend(_extract_yaml_deps(n))
                        n_content = yaml.safe_dump(n, sort_keys=False)
                        self.add_node(
                            ArtifactNode(
                                node_id=n_id,
                                layer=n_layer,
                                content=n_content,
                                dependencies=list(dict.fromkeys(deps)),
                                title=str(n_title),
                                file_path=rel_path,
                            )
                        )
                        count += 1
                continue

            node_id = _extract_yaml_node_id(doc)
            if node_id:
                deps = _extract_yaml_deps(doc)
                metadata = doc.get("metadata", {})
                meta_layer = metadata.get("layer") if isinstance(metadata, dict) else None
                id_prefix = node_id.split(".")[0].split("-")[0].upper()
                doc_layer = normalize_layer(
                    doc.get("layer")
                    or doc.get("artifact_type")
                    or meta_layer
                    or (id_prefix if id_prefix in CANONICAL_LAYERS else None)
                    or dir_layer
                )
                doc_ctrl = doc.get("document_control")
                ctrl_title = doc_ctrl.get("title") if isinstance(doc_ctrl, dict) else None
                title = str(doc.get("title") or ctrl_title or node_id)
                content = yaml.safe_dump(doc, sort_keys=False)
                self.add_node(
                    ArtifactNode(
                        node_id=node_id,
                        layer=doc_layer,
                        content=content,
                        dependencies=deps,
                        title=title,
                        file_path=rel_path,
                    )
                )
                count += 1
        return count

    def _scan_markdown_file(self, file_path: Path, rel_path: str, dir_layer: str) -> int:
        content = _read_bounded_text(file_path)
        if content is None:
            return 0

        fm_deps: list[str] = []
        fm_id: str | None = None
        fm_title: str | None = None
        fm_layer: str | None = None

        lines = content.splitlines()
        if lines and lines[0].strip() == "---":
            end_idx = next(
                (i for i in range(1, len(lines)) if lines[i].strip() == "---"),
                None,
            )
            if end_idx is not None:
                with contextlib.suppress(Exception):
                    fm = yaml.safe_load("\n".join(lines[1:end_idx]))
                    if isinstance(fm, dict):
                        fm_id = _extract_yaml_node_id(fm)
                        fm_title = str(fm["title"]) if fm.get("title") else None
                        fm_layer = str(fm["layer"]) if fm.get("layer") else None
                        fm_deps.extend(_extract_yaml_deps(fm))

        tag_deps = [m.group("target") for m in TAG_RE.finditer(content)]
        pointer_deps = _POINTER_RE.findall(content)
        all_deps = list(dict.fromkeys([*fm_deps, *tag_deps, *pointer_deps]))

        base_name = file_path.stem
        node_id = fm_id or base_name
        id_prefix = node_id.split(".")[0].split("-")[0].upper()
        md_layer = normalize_layer(
            fm_layer or (id_prefix if id_prefix in CANONICAL_LAYERS else None) or dir_layer
        )
        title = fm_title or base_name

        self.add_node(
            ArtifactNode(
                node_id=node_id,
                layer=md_layer,
                content=content[:4000],
                dependencies=all_deps,
                title=title,
                file_path=rel_path,
            )
        )
        return 1


def estimate_tokens(text: str) -> int:
    """Estimate token count using character heuristics (~3.8 chars/token for Markdown & code)."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 3.8))


def compact_node_content(content: str, tier: int = 1) -> str:
    """Compact node content based on priority tier.

    - Tier 0: Full content untouched
    - Tier 1 (formal): Frontmatter + formal requirements/signatures/scenarios (`SHALL`/`MUST`/GWT)
    - Tier 2 (summary): Frontmatter + first heading summary
    """
    if tier <= 0:
        return content.strip()

    lines = content.strip().splitlines()
    if tier == 1:
        in_fm = False
        kept: list[str] = []
        for line in lines:
            if line.strip() == "---":
                in_fm = not in_fm
                kept.append(line)
                continue
            if in_fm:
                kept.append(line)
                continue
            s = line.strip()
            if (
                s.startswith("#")
                or s.startswith("- ")
                or any(
                    s.startswith(k)
                    for k in (
                        "Given",
                        "When",
                        "Then",
                        "And",
                        "message",
                        "service",
                        "rpc",
                        "enum",
                    )
                )
                or "SHALL" in s
                or "MUST" in s
            ):
                kept.append(line)
        if len(kept) < len(lines):
            kept.append("<!-- [Tier 1 Formal compaction applied] -->")
        return "\n".join(kept)

    # Tier 2: Summary only
    in_fm = False
    kept_t2: list[str] = []
    for line in lines:
        if line.strip() == "---":
            in_fm = not in_fm
            kept_t2.append(line)
            continue
        if in_fm:
            kept_t2.append(line)
            continue
        if line.strip().startswith("#"):
            kept_t2.append(line)
            break
    kept_t2.append("<!-- [Tier 2 Distant summary: details condensed to satisfy token budget] -->")
    return "\n".join(kept_t2)


class ContextSlicer:
    """Extracts transitive ancestor closure and tiered token-compacted digests for target tasks."""

    def __init__(
        self,
        graph: AidocDependencyGraph,
        *,
        engine: str = "dfs",
        offline: bool = True,
    ) -> None:
        self.graph = graph
        self.engine_mode = engine
        self.offline = offline

    def slice_subgraph(
        self,
        target_task_id: str,
        allowed_layers: set[str] | None = None,
        max_depth: int = 6,
    ) -> list[ArtifactNode]:
        """Extract the minimal transitive ancestor closure in topological post-order."""
        if allowed_layers is None:
            allowed_layers = set(DEFAULT_ALLOWED_LAYERS)
        else:
            allowed_layers = {layer.strip().upper() for layer in allowed_layers if layer.strip()}

        depth_limit = max(1, min(int(max_depth), 20))
        target_key = _canonical_node_key(target_task_id)
        visited_keys: set[str] = set()
        ordered_closure: list[ArtifactNode] = []

        def dfs(current_id: str, depth: int) -> None:
            node = self.graph.get_node(current_id)
            curr_key = _canonical_node_key(node.node_id if node else current_id)
            if curr_key in visited_keys or depth > depth_limit:
                return
            visited_keys.add(curr_key)

            if node is None:
                return

            for parent_id in node.dependencies:
                dfs(parent_id, depth + 1)

            if node.layer in allowed_layers and curr_key != target_key:
                ordered_closure.append(node)

        dfs(target_task_id, 0)
        return ordered_closure

    def render_context_digest(
        self,
        target_task_id: str,
        *,
        max_depth: int = 6,
        max_tokens: int | None = None,
        allowed_layers: set[str] | None = None,
    ) -> str:
        """Compile a high-density, token-efficient prompt digest with Tier 0/1/2 compaction."""
        task_node = self.graph.get_node(target_task_id)
        ancestors = self.slice_subgraph(
            target_task_id,
            allowed_layers=allowed_layers,
            max_depth=max_depth,
        )

        header_lines = [
            f"=== AIDOC SLICED CONTEXT: TASK [{target_task_id}] ===",
            f"TITLE: {task_node.title if task_node else 'N/A'}",
            f"FILE: {task_node.file_path if task_node and task_node.file_path else 'N/A'}",
            "",
            "=== TASK DEFINITION ===",
            (
                task_node.content.strip()
                if task_node
                else "Task definition not found in local workspace."
            ),
            "",
            "=== STRICT CONTRACT BOUNDARIES (ANCESTOR CLOSURE) ===",
        ]

        if not ancestors:
            header_lines.append("No upstream contract constraints found for this task.")
            header_lines.append("")
            header_lines.append("=== END OF DIGEST: CODE MUST SATISFY THESE BOUNDARIES ONLY ===")
            return "\n".join(header_lines)

        footer = ["", "=== END OF DIGEST: CODE MUST SATISFY THESE BOUNDARIES ONLY ==="]
        ancestor_blocks: list[tuple[ArtifactNode, str]] = [
            (anc, anc.content.strip()) for anc in ancestors
        ]

        def assemble_digest(blocks: list[tuple[ArtifactNode, str]], notice: str = "") -> str:
            res = list(header_lines)
            for anc, body in blocks:
                res.append("")
                res.append(f"--- [{anc.layer}] {anc.node_id} ({anc.title}) ---")
                res.append(body)
            if notice:
                res.append("")
                res.append(notice)
            res.extend(footer)
            return "\n".join(res)

        full_digest = assemble_digest(ancestor_blocks)
        if max_tokens is None or estimate_tokens(full_digest) <= max_tokens:
            return full_digest

        distant_layers = {"BRD", "PRD", "ADR"}
        condensed_count = 0

        # Pass 1: Condense distant ancestors (BRD, PRD, ADR) to Tier 2
        new_blocks: list[tuple[ArtifactNode, str]] = []
        for anc, body in ancestor_blocks:
            if anc.layer in distant_layers:
                compacted = compact_node_content(anc.content, tier=2)
                new_blocks.append((anc, compacted))
                condensed_count += 1
            else:
                new_blocks.append((anc, body))

        notice = f"<!-- Compaction: budget {max_tokens}, {condensed_count} ancestors condensed -->"
        digest_step1 = assemble_digest(new_blocks, notice)
        if estimate_tokens(digest_step1) <= max_tokens:
            est = estimate_tokens(digest_step1)
            final_notice = (
                f"<!-- Compaction: budget {max_tokens}, estimated {est} tokens, "
                f"{condensed_count} ancestors condensed -->"
            )
            return assemble_digest(new_blocks, final_notice)

        # Pass 2: Condense immediate ancestors (EARS, BDD, SPEC, TDD, TASK, IPLAN) to Tier 1
        new_blocks_step2: list[tuple[ArtifactNode, str]] = []
        for anc, body in new_blocks:
            if anc.layer not in distant_layers:
                compacted = compact_node_content(anc.content, tier=1)
                new_blocks_step2.append((anc, compacted))
                condensed_count += 1
            else:
                new_blocks_step2.append((anc, body))

        est2 = estimate_tokens(assemble_digest(new_blocks_step2))
        final_notice2 = (
            f"<!-- Compaction: budget {max_tokens}, estimated {est2} tokens, "
            f"{condensed_count} ancestors condensed -->"
        )
        return assemble_digest(new_blocks_step2, final_notice2)


class InvariantChecker:
    """Pre-merge AST diff and anti-contract erosion gate."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()

    def _run_git(self, args: list[str]) -> subprocess.CompletedProcess[str] | None:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            return subprocess.run(  # nosec B603 B607 # reason: fixed git binary with validated refs
                ["git", *args],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        return None

    def get_changed_files(self, base_ref: str = "origin/dev") -> list[str]:
        """Run git diff against `base_ref` including uncommitted working tree changes."""
        safe_base = _validate_git_ref(base_ref)
        changed: set[str] = set()

        candidates = [
            safe_base,
            "origin/main",
            "origin/dev",
            "origin/master",
            "main",
            "dev",
            "master",
        ]
        discovered_ref: str | None = None
        for cand in candidates:
            check = self._run_git(["rev-parse", "--verify", cand])
            if check is not None and check.returncode == 0:
                discovered_ref = cand
                break

        diff_found = False
        if discovered_ref:
            mb = self._run_git(["merge-base", "HEAD", discovered_ref])
            if mb is not None and mb.returncode == 0 and mb.stdout.strip():
                merge_base = _validate_git_ref(mb.stdout.strip())
                res = self._run_git(["diff", "--name-only", f"{merge_base}...HEAD"])
                if res is not None and res.returncode == 0:
                    diff_found = True
                    for line in res.stdout.splitlines():
                        if line.strip():
                            changed.add(line.strip())

        if not diff_found:
            res2 = self._run_git(["diff", "--name-only", "HEAD~1...HEAD"])
            if res2 is not None and res2.returncode == 0:
                for line in res2.stdout.splitlines():
                    if line.strip():
                        changed.add(line.strip())
            else:
                res3 = self._run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"])
                if res3 is not None and res3.returncode == 0:
                    for line in res3.stdout.splitlines():
                        if line.strip():
                            changed.add(line.strip())

        res_working = self._run_git(["diff", "--name-only", "HEAD"])
        if res_working is not None and res_working.returncode == 0:
            for line in res_working.stdout.splitlines():
                if line.strip():
                    changed.add(line.strip())

        res_untracked = self._run_git(["ls-files", "--others", "--exclude-standard"])
        if res_untracked is not None and res_untracked.returncode == 0:
            for line in res_untracked.stdout.splitlines():
                if line.strip():
                    changed.add(line.strip())

        return sorted(changed)

    def check_contract_erosion(
        self,
        changed_files: Sequence[str],
        chg_type: str = "CODE2C",
    ) -> tuple[bool, list[str]]:
        """Verify if fast-path changes (`CODE2C`/`DIR2C`/`HOTFIX`) mutate protected contracts."""
        violations: list[str] = []
        protected_prefixes = (
            "proto/",
            "schemas/",
            "docs/sdd/06_SPEC/contracts/",
            "sdd/06_SPEC/contracts/",
            "framework/layers/06_SPEC/",
            "framework/layers/04_BDD/",
        )

        if chg_type.upper() in ("CODE2C", "DIR2C", "HOTFIX"):
            for f in changed_files:
                norm_f = f.replace("\\", "/").lstrip("./")
                is_violation = (
                    norm_f.startswith(protected_prefixes)
                    or norm_f.endswith(".proto")
                    or norm_f.endswith(".sw.yaml")
                )
                if is_violation:
                    violations.append(
                        f"Contract Erosion Violation: Fast-path change '{chg_type}' altered "
                        f"protected file '{f}'. Modifying public interface contracts or "
                        "behavioral state graphs requires formal promotion to 'SEED2C' or 'SDD2C'."
                    )

        return len(violations) == 0, violations

    def check_blast_radius_warnings(
        self,
        changed_files: Sequence[str],
        *,
        graph: AidocDependencyGraph | None = None,
        allow_sibling_lookup: bool = True,
    ) -> list[str]:
        """Emit blast-radius advisories when modified contracts have unchanged downstream tasks."""
        warnings: list[str] = []
        if not changed_files:
            return warnings

        norm_changed_files = {f.replace("\\", "/").lstrip("./") for f in changed_files}

        # Extract document IDs from changed paths (stopping before `_slug` or file extensions)
        changed_ids: set[str] = set()
        id_in_path_re = re.compile(
            r"\b(ADR|SPEC|PRD|BRD|EARS|BDD|TDD|IPLAN|TASK)[-_.]"
            r"([0-9]+(?:[.-][A-Za-z0-9]+)*)",
            re.IGNORECASE,
        )
        for f in norm_changed_files:
            stem = os.path.splitext(os.path.basename(f))[0]
            for m in id_in_path_re.finditer(stem):
                prefix, suffix = m.groups()
                clean_suffix = re.sub(r"\.(?:yaml|yml|md|json)$", "", suffix, flags=re.IGNORECASE)
                if prefix.upper() in ("ADR", "SPEC", "PRD", "BRD"):
                    changed_ids.add(f"{prefix.upper()}-{clean_suffix}")

        local_graph = graph
        if local_graph is None:
            resolver = ScopedContractResolver(
                self.workspace_root,
                allow_sibling_lookup=allow_sibling_lookup,
            )
            local_graph = AidocDependencyGraph(external_resolver=resolver.resolve)
            local_graph.scan_workspace(self.workspace_root)

        # Also include any ADR/SPEC/PRD/BRD node whose backing file_path is in changed_files
        for node in local_graph.nodes.values():
            if (
                node.file_path
                and node.file_path.replace("\\", "/").lstrip("./") in norm_changed_files
            ):
                if node.layer in ("ADR", "SPEC", "PRD", "BRD"):
                    changed_ids.add(node.node_id)

        if not changed_ids:
            return warnings

        # Collect canonical keys of all modified nodes/files
        modified_node_keys: set[str] = set()
        for f in norm_changed_files:
            stem = os.path.splitext(os.path.basename(f))[0]
            for m in id_in_path_re.finditer(stem):
                modified_node_keys.add(_canonical_node_key(f"{m.group(1)}-{m.group(2)}"))
        for node in local_graph.nodes.values():
            if (
                node.file_path
                and node.file_path.replace("\\", "/").lstrip("./") in norm_changed_files
            ):
                modified_node_keys.add(_canonical_node_key(node.node_id))

        seen_canon: set[str] = set()
        for doc_id in sorted(changed_ids):
            doc_key = _canonical_node_key(doc_id)
            if doc_key in seen_canon:
                continue
            seen_canon.add(doc_key)

            descendants = local_graph.descendants(doc_id, max_depth=6)
            downstream_nodes = [d for d in descendants if d.layer in ("IPLAN", "TASK", "TDD")]
            unmodified_downstream: list[str] = []
            for d in downstream_nodes:
                d_key = _canonical_node_key(d.node_id)
                d_path = (d.file_path or "").replace("\\", "/").lstrip("./")
                is_modified = (
                    d_key in modified_node_keys
                    or (bool(d_path) and d_path in norm_changed_files)
                    or any(d.node_id in f or d.node_id.lstrip("@") in f for f in norm_changed_files)
                )
                if not is_modified:
                    unmodified_downstream.append(d.node_id)

            if unmodified_downstream:
                preview = ", ".join(unmodified_downstream[:3])
                warnings.append(
                    f"Blast Radius Advisory: Modified contract '{doc_id}' affects downstream "
                    f"contracts [{preview}] which are unchanged in this diff."
                )

        return warnings


def _normalize_pkg_name(name: str) -> str:
    """Normalize Python package name per PEP 503 (`[-_.]+` -> `-`, lowercased)."""
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def _python_docstring_lines(source: str) -> set[int]:
    """Return 1-based line numbers belonging to module/class/function docstrings in Python code."""
    doc_lines: set[int] = set()
    with contextlib.suppress(SyntaxError, ValueError):
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    expr = node.body[0]
                    start = getattr(expr, "lineno", 0)
                    end = getattr(expr, "end_lineno", start)
                    for ln in range(start, end + 1):
                        doc_lines.add(ln)
    return doc_lines


class StackPolicyValidator:
    """Validates codebase and dependency manifests against `.aidoc/stack-policy.yaml`."""

    def __init__(self, root_dir: str | Path = ".") -> None:
        self.root_dir = Path(root_dir).resolve()

    def load_policy(self, policy_path: str | Path | None = None) -> dict[str, Any] | None:
        """Load stack policy contract definition from YAML (confined to `self.root_dir`)."""
        if not policy_path:
            target = self.root_dir / ".aidoc" / "stack-policy.yaml"
        else:
            raw = Path(policy_path)
            if raw.is_absolute():
                target = raw.resolve()
            else:
                cand_in_root = (self.root_dir / raw).resolve()
                cand_in_cwd = (Path.cwd().resolve() / raw).resolve()
                if cand_in_root.is_file():
                    target = cand_in_root
                elif cand_in_cwd.is_file() and _is_within_root(cand_in_cwd, self.root_dir):
                    target = cand_in_cwd
                else:
                    target = cand_in_root
            if not _is_within_root(target, self.root_dir):
                raise ValidationError(
                    f"Stack policy path '{policy_path}' is outside allowed boundary"
                )

        if not target.is_file() or not _is_within_root(target, self.root_dir):
            return None

        text = _read_bounded_text(target)
        if text is None:
            return None
        try:
            data = yaml.safe_load(text)
            return data if isinstance(data, dict) else None
        except yaml.YAMLError:
            return None

    def validate(self, policy_path: str | Path | None = None) -> tuple[bool, list[str]]:
        """Validate source code and dependency lockfiles against the stack policy."""
        policy = self.load_policy(policy_path)
        if not policy:
            return False, ["Missing .aidoc/stack-policy.yaml configuration."]

        violations: list[str] = []

        boundaries = policy.get("boundaries") or {}
        banned_patterns = boundaries.get("banned_patterns") or []
        if isinstance(banned_patterns, list) and banned_patterns:
            violations.extend(self._scan_banned_patterns([str(p) for p in banned_patterns if p]))

        dependencies = policy.get("dependencies") or {}
        strict_allowlist = bool(boundaries.get("strict_allowlist", False))
        allowed_pkgs = {
            _normalize_pkg_name(str(p.get("name")))
            for p in (dependencies.get("allowed_packages") or [])
            if isinstance(p, dict) and p.get("name")
        }

        # Check legacy poetry.lock whenever strict_allowlist is active or poetry.lock exists
        poetry_lock_file = self.root_dir / "poetry.lock"
        if poetry_lock_file.is_file() and _is_within_root(poetry_lock_file, self.root_dir):
            violations.append(
                "Legacy package manager lockfile 'poetry.lock' detected; platform technology "
                "stack mandates uv with uv.lock (stack 09 §3.13)."
            )

        if strict_allowlist and allowed_pkgs:
            # 1. Check requirements.txt
            req_file = self.root_dir / "requirements.txt"
            if req_file.is_file() and _is_within_root(req_file, self.root_dir):
                req_text = _read_bounded_text(req_file) or ""
                for line in req_text.splitlines():
                    s = line.strip()
                    if not s or s.startswith("#") or s.startswith("-"):
                        continue
                    pkg_raw = re.split(r"[><=~;\[@\s]", s)[0].strip()
                    if pkg_raw and _normalize_pkg_name(pkg_raw) not in allowed_pkgs:
                        violations.append(
                            f"Disallowed dependency '{pkg_raw.lower()}' found in "
                            "requirements.txt (strict allowlist active)."
                        )

            # 2. Check pyproject.toml [project.dependencies]
            pyproject_file = self.root_dir / "pyproject.toml"
            if pyproject_file.is_file() and _is_within_root(pyproject_file, self.root_dir):
                pyproj_text = _read_bounded_text(pyproject_file)
                if pyproj_text:
                    self._check_pyproject_dependencies(pyproj_text, allowed_pkgs, violations)

            # 3. Check uv.lock direct dependencies
            uv_lock_file = self.root_dir / "uv.lock"
            if uv_lock_file.is_file() and _is_within_root(uv_lock_file, self.root_dir):
                uv_text = _read_bounded_text(uv_lock_file)
                if uv_text:
                    self._check_uv_lock_dependencies(uv_text, allowed_pkgs, violations)

        return len(violations) == 0, violations

    def _check_pyproject_dependencies(
        self,
        content: str,
        allowed_pkgs: set[str],
        violations: list[str],
    ) -> None:
        parsed_deps: list[str] = []
        toml_ok = False
        with contextlib.suppress(Exception):
            data = tomllib.loads(content)
            toml_ok = True
            proj = data.get("project")
            if isinstance(proj, dict) and isinstance(proj.get("dependencies"), list):
                parsed_deps = [str(d) for d in proj["dependencies"] if isinstance(d, str)]

        if not toml_ok:
            # Fallback line scanner only when partial/non-standard TOML snippet fails tomllib
            in_deps = False
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("dependencies = ["):
                    in_deps = True
                    continue
                if in_deps:
                    if stripped.startswith("]"):
                        break
                    m = re.search(r'"([^"]+)"|\'([^\']+)\'', stripped)
                    if m:
                        parsed_deps.append(m.group(1) or m.group(2))

        for dep_spec in parsed_deps:
            dep_name = re.split(r"[><=~;\[@\s!]", dep_spec.strip())[0].strip()
            if dep_name and _normalize_pkg_name(dep_name) not in allowed_pkgs:
                violations.append(
                    f"Disallowed dependency '{dep_name.lower()}' found in pyproject.toml "
                    "(strict allowlist active)."
                )

    def _check_uv_lock_dependencies(
        self,
        content: str,
        allowed_pkgs: set[str],
        violations: list[str],
    ) -> None:
        toml_ok = False
        with contextlib.suppress(Exception):
            data = tomllib.loads(content)
            toml_ok = True
            for pkg in data.get("package", []) or []:
                if not isinstance(pkg, dict):
                    continue
                src = pkg.get("source")
                if isinstance(src, dict) and (
                    src.get("editable") == "." or src.get("virtual") == "."
                ):
                    for dep in pkg.get("dependencies", []) or []:
                        if isinstance(dep, dict) and dep.get("name"):
                            dep_name = str(dep["name"]).strip()
                            if dep_name and _normalize_pkg_name(dep_name) not in allowed_pkgs:
                                violations.append(
                                    f"Disallowed dependency '{dep_name.lower()}' found in uv.lock "
                                    "(strict allowlist active)."
                                )

        if toml_ok:
            return

        in_root_pkg = False
        in_deps = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("[[package]]"):
                in_root_pkg = False
                in_deps = False
                continue
            if stripped.startswith('source = { editable = "."') or stripped.startswith(
                'source = { virtual = "."'
            ):
                in_root_pkg = True
                continue
            if in_root_pkg:
                if stripped.startswith("dependencies = ["):
                    in_deps = True
                    continue
                if in_deps:
                    if stripped.startswith("]"):
                        in_deps = False
                        in_root_pkg = False
                        continue
                    match = re.search(r'name\s*=\s*"([^"]+)"', stripped)
                    if match:
                        dep_name = match.group(1).strip()
                        if dep_name and _normalize_pkg_name(dep_name) not in allowed_pkgs:
                            violations.append(
                                f"Disallowed dependency '{dep_name.lower()}' found in uv.lock "
                                "(strict allowlist active)."
                            )

    def _scan_banned_patterns(self, banned_patterns: list[str]) -> list[str]:
        """Scan source files for prohibited AST / string patterns (skipping Python docstrings)."""
        violations: list[str] = []
        excluded_dirs = _EXCLUDED_SCAN_DIRS | {"tests", "test"}

        for root, dirs, files in os.walk(self.root_dir, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in excluded_dirs)
            for file in sorted(files):
                if not file.endswith((".py", ".ts", ".js", ".rs", ".go")):
                    continue
                file_path = Path(root) / file
                if not _is_within_root(file_path, self.root_dir):
                    continue
                text = _read_bounded_text(file_path)
                if text is None:
                    continue
                doc_lines = _python_docstring_lines(text) if file.endswith(".py") else set()
                rel_path = file_path.resolve().relative_to(self.root_dir).as_posix()
                for line_no, line in enumerate(text.splitlines(), 1):
                    if line_no in doc_lines:
                        continue
                    code_part = line.split("#")[0].split("//")[0].strip()
                    for pattern in banned_patterns:
                        if pattern in code_part:
                            violations.append(
                                f"Banned pattern '{pattern}' detected in {rel_path}:{line_no}"
                            )
        return violations


# ── Top-Level Convenience Functions for CLI & MCP ───────────────────────────


def slice_local_context(
    root_dir: str | Path,
    *,
    task_id: str,
    depth: int = 6,
    layers: Sequence[str] | None = None,
    max_tokens: int = 2000,
    output_format: str = "json",
    allow_sibling_lookup: bool = True,
) -> dict[str, Any]:
    """Slice local SDD workspace DAG for `task_id` with optional Tier 0/1/2 digest rendering."""
    resolved_root = Path(root_dir).resolve()
    resolver = ScopedContractResolver(resolved_root, allow_sibling_lookup=allow_sibling_lookup)
    graph = AidocDependencyGraph(external_resolver=resolver.resolve)
    graph.scan_workspace(resolved_root)
    slicer = ContextSlicer(graph, engine="dfs", offline=True)

    allowed_layers = {x.strip().upper() for x in layers if x.strip()} if layers else None
    ancestors = slicer.slice_subgraph(
        task_id,
        allowed_layers=allowed_layers,
        max_depth=depth,
    )
    task_node = graph.get_node(task_id)
    serialized = [a.to_surface_dict() for a in ancestors]

    # Budget tokens on JSON list when max_tokens is set
    budget = max(1, int(max_tokens))
    kept: list[dict[str, Any]] = []
    for item in serialized:
        approx = estimate_tokens(f"{item.get('id', '')} {item.get('properties', {})}")
        if approx > budget and kept:
            break
        budget -= approx
        kept.append(item)

    digest = slicer.render_context_digest(
        task_id,
        max_depth=depth,
        max_tokens=max_tokens,
        allowed_layers=allowed_layers,
    )
    return {
        "task_id": task_id,
        "title": task_node.title if task_node else "",
        "depth": depth,
        "engine": "local_dag",
        "ancestor_count": len(kept),
        "ancestors": kept,
        "digest": digest,
        "format": output_format,
    }


def local_blast_radius(
    root_dir: str | Path,
    *,
    doc_id: str,
    depth: int = 6,
    allow_sibling_lookup: bool = True,
) -> dict[str, Any]:
    """Compute downstream blast radius for `doc_id` from the local SDD workspace DAG."""
    resolved_root = Path(root_dir).resolve()
    resolver = ScopedContractResolver(resolved_root, allow_sibling_lookup=allow_sibling_lookup)
    graph = AidocDependencyGraph(external_resolver=resolver.resolve)
    graph.scan_workspace(resolved_root)
    impacted = graph.descendants(doc_id, max_depth=depth)
    descendants = [n.to_surface_dict() for n in impacted]
    return {
        "doc_id": doc_id,
        "depth": depth,
        "engine": "local_dag",
        "impact_count": len(descendants),
        "descendants": descendants,
    }


def validate_sdd_graph(
    root_dir: str | Path,
    *,
    check_invariants: bool = False,
    allow_sibling_lookup: bool = True,
) -> dict[str, Any]:
    """Validate SDD dependency graph acyclicity and optional SDD-GRAPH v2.0 invariants."""
    resolved_root = Path(root_dir).resolve()
    resolver = ScopedContractResolver(resolved_root, allow_sibling_lookup=allow_sibling_lookup)
    graph = AidocDependencyGraph(external_resolver=resolver.resolve)
    node_count = graph.scan_workspace(resolved_root)
    cycles = graph.detect_cycles()

    invariant_violations: list[str] = []
    invariants_passed = True
    if check_invariants:
        invariants_passed, invariant_violations = graph.validate_invariants()

    passed = (len(cycles) == 0) and invariants_passed
    return {
        "status": "ok" if passed else "error",
        "passed": passed,
        "nodes_scanned": node_count,
        "cycles": cycles,
        "invariants_checked": check_invariants,
        "violations": invariant_violations,
    }


def check_sdd_invariants(
    root_dir: str | Path,
    *,
    base_ref: str = "origin/dev",
    chg_type: str = "CODE2C",
    include_blast_warnings: bool = True,
    allow_sibling_lookup: bool = True,
) -> dict[str, Any]:
    """Run pre-merge contract erosion check and optional downstream blast-radius advisories."""
    resolved_root = Path(root_dir).resolve()
    checker = InvariantChecker(resolved_root)
    changed_files = checker.get_changed_files(base_ref=base_ref)
    passed, violations = checker.check_contract_erosion(changed_files, chg_type=chg_type)
    warnings: list[str] = []
    if include_blast_warnings:
        warnings = checker.check_blast_radius_warnings(
            changed_files,
            allow_sibling_lookup=allow_sibling_lookup,
        )
    return {
        "status": "ok" if passed else "error",
        "passed": passed,
        "base_ref": base_ref,
        "change_type": chg_type,
        "changed_files_count": len(changed_files),
        "changed_files": changed_files,
        "violations": violations,
        "warnings": warnings,
    }


def lint_stack_policy(
    root_dir: str | Path,
    *,
    policy_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate repository source tree and lockfiles against `.aidoc/stack-policy.yaml`."""
    resolved_root = Path(root_dir).resolve()
    validator = StackPolicyValidator(resolved_root)
    passed, violations = validator.validate(policy_path=policy_path)
    return {
        "status": "ok" if passed else "error",
        "passed": passed,
        "violations": violations,
    }
