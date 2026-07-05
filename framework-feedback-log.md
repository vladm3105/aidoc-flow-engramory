# UCX / aidoc-flow-framework — Feedback Log (aidoc-flow-engramory)

**Source:** Found while authoring Engramory's SDD artifacts against framework v0.23.0, June 2026.
**Repo:** github.com/vladm3105/aidoc-flow-framework
**Purpose:** Inconsistencies/errors + improvement requests hit while using the framework on this project, mirrored as framework TODO tasks. None block authoring.

Severity: 🔴 error · 🟠 inconsistency · 🟡 clarification/improvement

---

## 1. 🟡 Codify "whole-project BRD roadmap + draft BRD sketches" as a project-initiation step  (TODO #58 — extends #35)

**Context.** Starting a project with only `BRD-01` authored in full plus one-line index stubs leaves the *whole-project scope* under-specified — you can't see, validate, or correct the full arc before committing to cycle 1. The practice that worked for Engramory:

1. **Initial BRD (current MVP cycle): full depth** — all 8 layers (BRD-01).
2. **Draft/sketch BRDs for every future cycle: business scope only** — hypothesis + rough feature set + `@depends:` link, **status Draft, NO downstream layers** (PRD/EARS/… are developed in that cycle's MVP, not now).
3. **A project roadmap** (`roadmap/ROADMAP.md`) sequencing all cycles **MVP → PROD → new-feature MVP → new PROD**.

This gives whole-project scope visibility up front, lets scope be corrected early, and keeps the framework's "don't over-author distant specs" rule (BRD = stable business intent; downstream layers depreciate, so they're deferred). It is the planning-first principle applied at the **project** level, not just the cycle level.

This **extends feedback item #35** ("author current cycle's BRD set in full; stub the rest"): #35 stubs future BRDs as index one-liners; this refines that to proper *draft BRD sketches* + a roadmap.

**Fix:**
- Document the project-initiation step in the README and `framework/layers/01_BRD/README.md`: produce (a) `roadmap/ROADMAP.md` enumerating all MVP cycles, (b) draft BRD **sketches** (scope-only) for all future cycles, (c) the current cycle's BRD at full depth.
- Add a lightweight **"BRD sketch" sub-form** (or a `status: Sketch` / `_sketch: true` marker) that requires only scope-level sections (document_control, introduction, business_objectives hypothesis+goals, project_scope) and **explicitly forbids downstream-layer content** — so sketches pass lint/validation without being treated as incomplete full BRDs.
- Clarify in `roadmap/README` how `@depends:` chains the cycles and how a sketch graduates to a full BRD when its MVP cycle begins.

---

## 2. 🟡 Provide a standalone element-ID hashing helper (decouple from `sdd_create`)  (TODO #59)

**Context.** Element IDs are content-derived (`{type}.{doc}.{section}.{sha256[:4]}`). When authoring a document by hand — or with an AI agent that does not invoke `sdd_create` — there is no easy way to compute canonical hashes, so authors produce **format-valid placeholder hashes** that only get canonicalized later by `sdd_create`/`sdd_validate`. This is fine but undocumented, and it leaves a window where two artifacts can look authored yet carry non-canonical IDs.

**Fix:** Ship a tiny standalone CLI/library (`sdd hash` or `framework/tools/hash_id.py`) that computes the canonical element ID from `(doc_id, section_id, title, description)`, and state clearly in `ID_NAMING_STANDARDS.md` that hand-authored hashes are placeholders until canonicalized. Lets non-Hermes authors emit canonical IDs directly.

---

## 3. 🟡 IPLAN-00 registry schema differs from IPLAN docs — trips naive validation globs  (TODO #60)

**Context.** `IPLAN-00_index.yaml` is `document_type: iplan-registry` (a `registry:` structure), while `IPLAN-NN_*.yaml` are `document_type: iplan-document` (with `document_control`, `file_manifest`, …). A generic "validate every `08_IPLAN/IPLAN-*.yaml`" loop fails on the registry because it has no `document_control`. (Hit directly during a batch validation pass.)

**Fix:** Either name the registry so generic globs naturally exclude it (e.g., keep the `-00_index` convention *and* document that index files are validated by a different rule), or note in `08_IPLAN/README.md` that registry vs document are distinct schemas and how each is validated. A one-line note in the layer README prevents the foot-gun.

---

## 4. 🟡 Surface the SPEC/IPLAN element-ID exemptions in the templates, not only in ID_NAMING_STANDARDS  (TODO #61)

**Context.** `ID_NAMING_STANDARDS.md` documents that SPEC §5 / §3 and IPLAN §4 / §2 **may** omit layer-local element IDs (traceability provided by upstream `@`-citations + method/file names). But the SPEC and IPLAN **templates** don't restate this, so an author reading only the template may over-assign element IDs (noise) or worry they're missing required ones.

**Fix:** Add a one-line `_note` in the SPEC §5 / §3 and IPLAN §4 / §2 template guidance pointing to the exemption in `ID_NAMING_STANDARDS.md`. Keep the standard authoritative; just cross-reference it where authors actually look.

---

## 5. 🟡 Clarify platform-BRD ADR "before PRD" means decision *timing*, not authoring *order*  (TODO #62 — relates to #40)

**Context.** The BRD template says platform-BRD ADRs are created **before** PRD. Read literally, that conflicts with the cumulative-tag chain (ADR carries `@ears`/`@bdd`), since EARS/BDD wouldn't exist yet (BeeLocal #40). In practice we authored the SDD pass in strict layer order — BRD → PRD → EARS → BDD → ADR — so the ADRs *did* have upstream EARS/BDD to cite, and #40 never bit. The "before PRD" wording is about *when the architectural decision was effectively made* (the platform's foundations were already chosen), not the order in which the artifacts are authored.

**Fix:** Reword the platform-BRD note: even for platform BRDs, **author ADRs in sequence (after BDD)** so they carry the full cumulative chain; "decided before PRD" refers to decision provenance, which the ADR records in `context`/`originating_topic`. This resolves #40 by clarification rather than a tagging variant.

---

## 6. 🟡 State explicitly that SDD depth is orthogonal to the MVP→PROD cadence and may vary per cycle  (TODO #63)

**Context.** The framework defines the three depth variants (Lite / Standard / Full) and the MVP → PROD → new-MVP lifecycle, but does not explicitly state that the two are **orthogonal**: depth is chosen *per cycle* by risk, and **may change between cycles** (e.g., Lite for an early MVP → Standard/Full as the product hardens). Adopters commonly ask "do we pick one depth for the whole project?" The CHG governance overlay (Full) also lives *inside* a cycle (gates before code), not outside the cadence — easy to misread as a separate flow.

**Fix:** Add a one-paragraph note to the README (depth section) and `roadmap/README`: "SDD depth is chosen per cycle by risk and MAY vary across cycles; the MVP → PROD → new-MVP cadence is identical at every depth; Full's CHG gates run inside the cycle, before code." A small worked example (Lite MVP-1 → Full MVP-3) would remove the ambiguity.

---

## 7. 🟡 Add a full-chain *forward* coverage gate (BRD requirement → IPLAN) + allow multi-upstream tagging  (TODO #64 — extends #54)

**Context.** Comparing BRD vs IPLAN on this project, two **core** requirements — L2 (long-term distilled memory) and L3 (per-agent identity) — traced to **no IPLAN**, even though both are fully implemented (SPEC-02 `Memory`/`AgentProfile` + scope, SPEC-04 distillation, SPEC-01 scoping → IPLAN-02/04/01). Root cause: **no EARS line carried `@brd:` for those BRD FRs** — several EARS lines that serve them cite only one upstream BRD FR each. The cumulative tags flow *downward* and are spot-checked per layer, but nothing verifies, **end-to-end and forward**, that *every BRD requirement resolves to ≥1 implementing IPLAN*. So a requirement can be built yet appear uncovered (false gap), or — worse — be genuinely unbuilt yet pass if downstream tags are sloppy (false pass). This is BeeLocal #54 ("no requirements-coverage check, EARS/BDD→SPEC") extended to the full chain through IPLAN — exactly the check an adopter needs before GATE-CODE.

**Fix:**
- Provide a **forward coverage report/gate** (e.g., `sdd_coverage`, or a GATE-CODE pre-check): resolve the `@`-tag graph and assert every BRD functional requirement reaches ≥1 SPEC and ≥1 IPLAN; emit a full BRD→PRD→EARS→BDD→SPEC→TDD→IPLAN matrix and list any requirement with a broken/empty downstream path.
- **Allow and encourage multi-upstream tagging:** an EARS line frequently serves several BRD FRs; the template/standard should explicitly permit multiple `@brd:` citations per line and lint for any BRD FR with **zero** downstream EARS coverage (the upstream-completeness direction, complementing #54's downstream-coverage direction).

---

## Summary table

| # | Sev | Issue | TODO |
|---|-----|-------|------|
| 1 | 🟡 | Codify whole-project BRD roadmap + draft BRD sketches at project initiation | #58 (extends #35) |
| 2 | 🟡 | Standalone element-ID hashing helper (decouple from sdd_create) | #59 |
| 3 | 🟡 | IPLAN-00 registry schema differs from IPLAN docs — trips naive validation | #60 |
| 4 | 🟡 | Surface SPEC/IPLAN element-ID exemptions in the templates | #61 |
| 5 | 🟡 | Clarify platform-BRD "ADR before PRD" = decision timing, not authoring order | #62 (relates to #40) |
| 6 | 🟡 | SDD depth is orthogonal to MVP→PROD cadence; may vary per cycle | #63 |
| 7 | 🟡 | Full-chain forward coverage gate (BRD→IPLAN) + multi-upstream tagging | #64 (extends #54) |

*Maintained per `docs/HOW_TO_USE_THE_FRAMEWORK.md` §6. Mirror items to framework TODOs.*
