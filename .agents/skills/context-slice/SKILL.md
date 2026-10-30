---
name: context-slice
description: >-
  Use when beginning any implementation, testing, or review task within the SDD lifecycle. Computes the deterministic ancestor closure via aidoc-ctx slice to extract exact, high-density context without context bloat or specification amnesia.
---

# Context Slicing & Invariant Verification

Autonomous coding agents must never ingest unbounded document dumps or perform blind full-repository grep searches when implementing a scoped task. Context bloat triggers attention degradation ("context amnesia") and wastes LLM token budgets.

This skill governs the use of **`aidoc-context-tools` (`aidoc-ctx`)** to extract minimal, deterministic context closures and enforce pre-merge anti-erosion invariant gates.

---

## 1. Principles of Context Slicing

1. **Deterministic Ancestor Closure**: For any task node $T$ in Layer 08 (IPLAN), the required context slice is strictly defined as:
   $$\text{Slice}(T) = \text{Ancestors}(T) \cap \{\text{SPEC}, \text{BDD}, \text{EARS}, \text{ADR}, \text{TDD}\}$$
2. **Zero-Drift Resolution**: Cross-repository dependencies referenced as `@project-id:layer:tag-id` (e.g. `@auth-core:06_SPEC:SPEC-TOKEN-V1`) are resolved against the local contract cache or Git submodule (`.aidoc/contracts/cache/`).
3. **Fail-Closed Invariant Gate**: If a change attempts to alter public API signatures or state graphs while classified under a non-architectural flow (`CODE2C` or `DIR2C`), the commit is rejected.

---

## 2. Invocation Workflow

### Step 1: Generate Targeted Context Slice
Before authoring or editing code for a specific IPLAN task or feature:

```bash
aidoc-ctx slice --task <TASK_OR_SPEC_ID> --sdd-dir docs/sdd
```

Flags:
- `--task <ID>`: Target task identifier (e.g. `IPLAN-AUTH-012` or `SPEC-BILLING-001`).
- `--sdd-dir <PATH>`: Directory containing SDD layers (default: `docs/sdd`).
- `--layers <LIST>`: Comma-separated allowed layers (default: `EARS,BDD,ADR,SPEC,TDD`).
- `-o, --output <FILE>`: Optional path to write context digest.

### Step 2: Validate Graph Acyclicity
Ensure the local specification graph has no circular dependencies:

```bash
aidoc-ctx validate-graph --sdd-dir docs/sdd
```

### Step 3: Run Pre-Commit Anti-Contract Erosion Gate
Before committing changes to git, verify that git diffs do not violate ratified change flow invariants:

```bash
aidoc-ctx lint-invariants --diff-target HEAD --flow-type <FLOW_ID>
```

If `lint-invariants` detects breaking schema or signature mutations under `CODE2C` (bugfix) or `DIR2C` (direct tooling), promote the change request immediately to `SEED2C` (design modification) or `SDD2C` (full specification cascade).
