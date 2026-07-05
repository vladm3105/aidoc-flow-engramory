# How to Use the UCX / aidoc-flow Framework

*Practical reference for authoring SDD artifacts the proper way. Distilled from the framework README and the BeeLocal (`b-local-privy`) worked example + its `framework-feedback-log.md`. June 2026.*

---

## 1. What the framework is

UCX (aidoc-flow-framework) is **AI-First Specification-Driven Development**: AI agents are the primary operators, humans work *through* them. Work flows through an 8-layer document lifecycle, planning-first, with governance gates and cumulative traceability.

It sits in a multi-plane ecosystem:

| Plane | Component | Role |
|---|---|---|
| Control | `aidoc-flow-framework` (UCX) | SDD lifecycle BRD→IPLAN; Hermes orchestration; lifecycle gates are source of truth |
| Execution | `iplan-runner` → `iplanic` | Approved IPLAN → ledger → gate → monitor; system-of-record |
| Memory + knowledge | **Engramory** | Shared KB + per-agent distilled memory (replaces `ucx_kb`) |
| Consumers | `aidoc-flow`, `AI Operations Intelligence`, BeeLocal, … | Build on the framework + Engramory |

---

## 2. The 8 layers (SDD v3, recommended)

| # | Layer | Name | C4 level | Carries tags from |
|---|---|---|---|---|
| 1 | BRD | Business Requirements | L1 Context | — |
| 2 | PRD | Product Requirements | L2 Container | `@brd` |
| 3 | EARS | Easy Approach to Requirements Syntax | Decision bridge | `@prd` |
| 4 | BDD | Behavior-Driven Development | Decision bridge | `@ears` |
| 5 | ADR | Architecture Decision Record | Decision bridge | `@bdd` |
| 6 | SPEC | Technical Specification | L3 Component | `@adr` |
| 7 | TDD | Test-Driven Development guide | Impl bridge | `@spec` |
| 8 | IPLAN | Implementation Plan | Impl bridge | `@tdd` |

**Depth variants:** **Lite** (BRD→PRD→IPLAN) · **Standard** (+EARS/BDD/ADR) · **Full** (all 8 + CHG governance overlay). Pick by risk: regulated/fintech → Full; core infra → Standard; prototype → Lite.

> Legacy v2 used a 14-layer chain with SYS/REQ/CTR/TSPEC/TASKS. **Use v3.** v3 collapses SYS/REQ/CTR into **SPEC** and adds **TDD** + **IPLAN**.

---

## 3. Where the templates actually live (critical)

**Canonical templates: `framework/layers/NN_*/{TYPE}-TEMPLATE.yaml`** — e.g. `framework/layers/01_BRD/BRD-TEMPLATE.yaml`. This path is verified working.

Do **not** use these (they fail or are deprecated):
- `ucx_flow_v3/01_BRD/BRD-TEMPLATE.yaml` and `ucx_flow_v3/LAYER_REGISTRY.yaml` — raw fetch returns **empty**.
- `mcp_ucx/templates/` — `mcp_ucx/` is **DEPRECATED** (frozen at v1.22.0); use `ucx_hermes/` for the active runtime.

This is the single most common stumbling block; BeeLocal logged it as feedback #38.

---

## 4. The proper workflow (planning-first)

**No implementation without an approved plan.** The required sequence:

```
analyze inputs → roadmap → planning index → changelog plan
  → gap review/fix → IPLAN → approval → implementation
```

Approval authority: a human reviewer **or** an independent LLM-as-judge (`sdd_review`).

### Per cycle
1. **Define the cycle.** A cycle is anchored by a **BRD set** — one *platform* BRD plus its *feature* BRDs, linked by `@depends:` — **not** a single BRD. Subsequent cycles: BRD-02, BRD-03, … via `@depends: BRD-01`.
2. **Author the current cycle's BRD set in full; stub future cycles/features** in the BRD-00 index. Don't over-author distant specs that depreciate before their cycle.
3. **Treat existing prose docs as input content, not artifacts.** Conversion is *assembly, not redesign*: scaffold via Hermes `sdd_create` from the YAML templates → add cumulative `@`-tags → ID per `ID_NAMING_STANDARDS` → validate with `sdd_validate` / `sdd_consistency` → gate via CHG.
4. **Authoring order:** BRD → PRD → EARS → BDD → ADR (convert existing decisions) → SPEC → TDD → IPLAN → hand approved IPLAN to code agents.

### Operating model
- **Hermes** drives the document lifecycle (BRD→IPLAN) via the `sdd_*` MCP tools.
- **Claude Code / Codex** implement source code **only from an approved IPLAN**.
- UCX MCP validation/review gates run before and after implementation.

### CHG gates (Full depth)
GATE-01 after BRD/PRD (scope) · GATE-02 after EARS/BDD (requirements+behavior) · GATE-03 after ADR (architecture) · GATE-04 after SPEC/TDD (design+test) · **GATE-CODE before code generation**.

### Governance states
`ai:ready → ai:in-progress → ai:review-requested`. Only `ai:ready` issues are eligible for autonomous execution.

### Plan taxonomy
- **Document-layer IPLAN** — lifecycle output (`docs/IPLAN/`, `UCX/08_IPLAN/`). Permanent.
- **Permanent development plan** — `plans/`. Cross-session execution + history. Permanent.
- **Temporary plan** — `tmp/`. Bug fixes, minor one-offs. Disposable.
- *Escalation:* if a temporary plan grows (new capability, cross-cutting dependency, multi-session), promote it to `plans/`.

---

## 5. Traceability rules (where teams trip up)

- **Cumulative tagging:** every layer carries `@`-tags from all upstream layers (see §2). Enforce with `sdd_validate` / `sdd_consistency`.
- **Per-layer numbering is independent.** SPEC-07 has **no** relation to "BRD-07"; numbers are per-layer counters with no cross-layer alignment. One upstream item **fans out** to many downstream docs (1 feature BRD → N SPECs). Trace by `@`-tags + element IDs, never by matching numbers.
- **SPEC/TDD/IPLAN tag chains truncate** — SPEC/TDD carry `@adr,@bdd,@ears`; IPLAN carries `@spec,@tdd`. To reach the BRD you walk the chain transitively. (Known conflict with `GATE-08-E003`, which lists `@brd`+`@prd` — reconcile per project.)
- **Verification/acceptance refs MUST be element-level** (`BDD.01.03.3aa0`), not doc-level (`BDD-01`), or element-level coverage computation silently breaks. Doc-level refs are only for whole-document dependencies.
- **Keep a generated `TRACEABILITY.md` matrix** per project: forward (component → ADR/BDD/EARS → parent BRD) and reverse (BRD requirement → all downstream docs). Add `# TRACEABILITY: SPEC-03, ADR-0006, BDD-01-cap-breach` comments in generated code.
- **Requirements coverage:** track each EARS/BDD element → covering SPEC/TDD, or an explicit `deferred:` reason, so *deferred* is distinguishable from *missed*.

---

## 6. The framework feedback file (`framework-feedback-log.md`)

Maintaining this file is **part of using the framework correctly**. It is the framework's continuous-improvement loop, kept **inside the consumer project**.

**How it works:** while authoring artifacts, whenever you hit a framework inconsistency, error, or ambiguity, log it — and mirror each as a TODO task back to the framework repo. None of it blocks authoring (proceed by working from `framework/layers/` directly); every friction point flows back to harden the framework.

**Format** (from BeeLocal):
- **Header:** source, framework repo, purpose, and the TODO range it mirrors to.
- **Severity codes:** 🔴 error · 🟠 inconsistency · 🟡 clarification/improvement.
- **Per item:** a one-line title with severity + TODO number, the concrete place you hit it, and a proposed **Fix:**.
- **Summary table:** `# | Sev | Issue | TODO`.

**Entry skeleton:**
```markdown
## N. 🟠 Short title  (TODO #NN)
Where/how it was hit (be concrete — which artifact, which rule).
**Fix:** the proposed change to the framework.
```

> This is, in effect, *procedural memory about the framework itself* — the same thing Engramory distills. A project's feedback log is a natural input to Engramory's L2 procedural memory.

---

## 7. Known gotchas (pre-solved by BeeLocal — don't re-hit)

| Gotcha | Do this |
|---|---|
| "Each BRD = one cycle" wording | A cycle = a **BRD set** (platform + feature BRDs) |
| How much to author | Current cycle full; **stub** future cycles in the index |
| Template path confusion | Use `framework/layers/`, not `ucx_flow_v3/` or `mcp_ucx/` |
| Numbers don't line up across layers | Independent per-layer numbering; trace by tags; keep `TRACEABILITY.md` |
| Can't reach BRD from a SPEC's tags | Tag chain truncates at L6 — resolve transitively |
| Coverage looks like a gap | Doc-level refs break coverage — use **element-level** refs |
| Deferred vs missed requirements | Add a coverage section + check (EARS/BDD element → SPEC/TDD or `deferred:`) |
| Readiness score blank | It's **advisory**; the deterministic floor is the real gate |
| Platform-BRD ADRs decided before PRD | Tag them `@brd` only; reconcile later |
| `status` means three things | Scope it (`document_status` / `option_status` / enums) |
| Units mixed (words vs tokens) | Section `_size_target` = words; doc cap = 50,000 tokens |
| Vendor names in ADRs | Allowed in `recommended_selection`; not in titles/business_driver |

---

## 8. Quick-start checklist for a new cycle

1. [ ] Choose **depth** (Lite / Standard / Full) by risk.
2. [ ] Pull templates from **`framework/layers/NN_*/{TYPE}-TEMPLATE.yaml`**.
3. [ ] Define the **BRD set** (platform + feature BRDs); stub future cycles in BRD-00 index.
4. [ ] Inventory existing prose as **input content** → map each to its target layer.
5. [ ] Author BRD → PRD → EARS → BDD → ADR(convert) → SPEC → TDD → IPLAN via Hermes `sdd_create`.
6. [ ] Add cumulative `@`-tags; IDs per `ID_NAMING_STANDARDS`; **element-level** verification refs.
7. [ ] Run `sdd_validate` / `sdd_consistency`; generate `TRACEABILITY.md`; check coverage.
8. [ ] Gate via CHG (GATE-01 … GATE-CODE); approve via human or `sdd_review`.
9. [ ] Hand approved IPLAN to code agents; execute via `iplan-runner` (ledger → gate → monitor).
10. [ ] Keep `framework-feedback-log.md` updated as you go; mirror items to framework TODOs.

---

## 9. References

- Framework repo: `github.com/vladm3105/aidoc-flow-framework` (v3 8-layer chain, C4 mapping, depth variants, CHG overlay, cumulative tagging, `sdd_*` tools).
- Worked example: `b-local-privy/BeeLocal-SDD-v3-Plan.md` (layer mapping, authoring order, gate placement) and `b-local-privy/framework-feedback-log.md` (issues #34–#57 and the feedback-file format).
- Templates: `framework/layers/`. Active runtime: `ucx_hermes/`. (`mcp_ucx/` deprecated.)
