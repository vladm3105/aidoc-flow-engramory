# AI Agent Memory — Conceptual Review of Engramory's Approach

*July 5, 2026 · Review of the memory design in `ARCHITECTURE.md`, `MEMORY_DESIGN.md`,
`research/MEMORY_LANDSCAPE.md`, and its operationalization in `sdd/06_SPEC/SPEC-02`
(store) and `SPEC-04` (distillation).*

> Status: review artifact. Evaluative by purpose. Where it conflicts with an accepted
> ADR, the ADR governs until amended. Findings marked **Fix** are proposals, not decisions.
>
> **Status (2026-07-09, PLAN-001):** the contract-level fixes for D1
> (feedback loop), D2 (confidence dynamics), D4 partially (status +
> usage columns), and the quarantine/idempotency gaps now live in
> `db/migrations/0003_reconcile_contracts.sql` + SPEC-01/03/04 updates.
> Still open as design work: D3 (contradiction detection), D5 (query-type
> routing), D6 partially (reflection triggers specified in SPEC-04, not
> yet built), G1 (injection screen / self-poisoning gate beyond
> source_trust), G2 (cross-agent promotion/corroboration before `space`
> scope), G4 (context assembly beyond token_budget). This review governs
> those open items.

---

## Summary judgment

The design is strong on **memory-as-storage** and under-specified on **memory-as-learning**.
Every decision about *where memory lives and how it survives* is sound and, in places, ahead
of comparable 2026 systems. Most decisions about *how the agent decides what to keep, how it
corrects itself, and how success is measured* are named as important and then deferred to
"adopt LangMem/Cipher and tune." The difficult part of agent memory lives in that deferral.

The source documents are candid — they state that distillation quality "is the whole game,"
that there is "no free lunch on truth," and that "consolidation can entrench a wrong lesson."
These are recorded as **risks to be aware of** rather than **mechanisms to be designed**. The
purpose of this review is to move each item from naming to mechanism.

---

## Strong foundations (retain; refine)

### S1. Own-the-canonical-store

Raw text + provenance + regenerable embeddings in Postgres, with the memory engine treated as a
replaceable processor. This is the correct portability primitive. It correctly identifies that
*semantic* portability (re-embed from retained text) is a stronger guarantee than any vendor
export API.

- **Refine.** The design labels both embeddings and the graph "rebuildable projections"
  (ARCHITECTURE §graph, ADR-05). The two differ in rebuild cost and fidelity. Re-embedding is
  deterministic given text + model. Re-projecting a graph is a fresh, non-deterministic LLM
  extraction that yields different entities/edges per run and discards curated corrections.
- **Fix.** Separate *deterministic projections* (embeddings) from *derived interpretations*
  (entity graph, links). Give the latter their own provenance and version record so a rebuild
  is not a silent regression.

### S2. Three memory types (semantic / episodic / procedural)

A correct taxonomy, correctly tied to "lessons and errors become skills."

- **Refine.** The taxonomy is collapsed into one table, one `type` column, one distillation
  loop, and one scalar `confidence` (SPEC-02, migration 0001). The three types have different
  mechanics: procedural skills are validated by repeated success/failure; semantic facts need
  contradiction handling and temporal validity; episodic records are high-volume substrate that
  should decay or sample rather than persist with equal weight.
- **Fix.** Type-specific consolidation policies and fields (e.g., success/failure counters on
  procedural memory, context/validity on semantic, salience/decay on episodic).

### S3. Distillation as "sleep" (reflection → consolidation)

A sound framing consistent with reflection in Generative Agents and 2025 sleep-time-compute work.

- **Refine.** The claim "indistinguishable in practice from the agent remembers everything"
  (MEMORY_DESIGN §1) sets a total-recall expectation that retrieval-based memory will visibly
  miss.
- **Fix.** State the expectation as *relevant recall, not total recall*, and treat the
  "I don't have that" case as designed behavior.

---

## Defects (conceptually wrong or high-risk)

### D1. No feedback loop — the "whole game" is played blind

Nothing measures whether a retrieved memory helped the task. Consolidation merges, generalizes,
and prunes on a one-shot write-time judgment that is never corrected by outcome. Memory is
modeled as store → retrieve, but "learning what to keep" requires retrieve → use → outcome →
reinforce, and that arc is absent. BRD-01 sets a 95% citation-accuracy gate for the KB; L2
memory has no equivalent.

- **Fix.** Log each retrieval and bind it to task outcome; let utility-from-use — not only
  birth-time confidence — drive confidence updates and pruning.

### D2. `confidence` is a placeholder, not a mechanism

`confidence REAL DEFAULT 0.5` with "quarantine below a floor" (SPEC-02, SPEC-04). Its source is
undefined (an LLM self-estimate, which is poorly calibrated) and it has no update rule — set once
at write, read as a static gate. A lesson that keeps proving true never gains standing; one that
causes errors never loses it.

- **Fix.** Define confidence as evidence-based: a prior from distillation, updated by
  retrieval-utility and contradiction signals.

### D3. Conflict handling is linear; real conflicts are not

`supersedes` + `valid_to` handle "B replaces A." There is no detection of contradiction (the
supersession link is assumed to already exist) and no context-dependent validity (true in
project X, false in project Y — where both should survive, scoped, rather than one superseding
the other). Zep/Graphiti is cited for temporal contradiction resolution but its mechanism is not
adopted.

- **Fix.** Detect contradictions at reflection time (retrieve neighbors, test for conflict) and
  support context-scoped validity so conflicting-but-both-true memories coexist.

### D4. "Endless because bounded" conflates two claims, one false

Bounded *working set* (what enters context) is real and correct. Bounded *total store* is false:
genuinely new facts across projects grow L2 monotonically regardless of consolidation quality.
The endlessness derives from retrieval bounding, not from consolidation bounding the store. There
is also no forgetting policy — "prune low-value noise" has no value function, whereas functional
forgetting reduces interference.

- **Fix.** Separate the two claims; add a retention/forgetting policy (recency × frequency ×
  utility decay); plan archival tiers for a growing canonical store; treat forgetting as a
  first-class, evaluated operation.

### D5. Retrieval is top-K similarity + scope filter — too thin

The stated goal ("the right lesson at the right time") is a relevance/timing problem; similarity
to query text is a different problem. It is especially wrong for procedural memory, which should
fire on situation match (the agent is in state X), not text similarity. There is no re-ranking by
recency/confidence/utility, no query-intent routing to memory type, and no associative/graph
retrieval for the multi-hop cases the graph engine exists to serve.

- **Fix.** Multi-signal ranking (similarity × recency × confidence × utility × scope-priority),
  query-intent routing, and situation-triggered procedural recall.

### D6. The L1→L2 promotion trigger is brittle for continuous agents

"At project end" / "post-session" is not well defined for an agent that runs for days or crashes
mid-run. Reflection firing only at a clean session end lets L1 grow unbounded, and an ungraceful
exit loses undistilled episodes — L1 is specified in Redis (ephemeral), so the loss is real.

- **Fix.** Define explicit reflection triggers (time, episode-count, salience/surprise) and make
  L1 durable enough to survive a crash without losing undistilled experience.

---

## Gaps (missing concepts)

### G1. Memory safety / poisoning

A system built on durable, self-authored memory that is re-injected into future tasks carries an
error-amplification and prompt-injection surface: one malicious document or one wrong inference,
once distilled into a "lesson," becomes durable and re-contaminates later tasks. ADR-06 governs
KB writes; episode and distilled-memory writes are agent-authored with no injection screening and
no provenance-trust weighting.

- **Fix.** Trust-weight memory by source (human-approved > agent-inferred), screen for injection
  at reflection, and quarantine + review before any memory crosses an agent or tenant boundary.

### G2. Cross-agent promotion is undefined

"Shared/team namespace" does not state what makes one agent's lesson safe for others. There is no
corroboration threshold before a per-agent lesson graduates to `space` scope, and no attribution
when one agent uses another's memory. L3 "agent identity" is asserted but implemented as a
`standing_preferences` JSONB bag — a preferences dictionary, not an evolving identity.

- **Fix.** Require corroboration (across agents/projects) before graduating a memory to shared
  scope; attribute shared memories; either build an identity model or rename the layer
  "agent-scoped memory."

### G3. No evaluation concept

The platform thesis is "distilled memory makes agents better," and nothing measures it: no memory
benchmark (LoCoMo / LongMemEval-style), no task-success with-vs-without memory, no retrieval
precision/recall target, no memory-utilization metric. This is deferred to BRD-06 (a sketch).

- **Fix.** Make a memory-quality eval harness a first-class, early deliverable — the "whole game"
  cannot be tuned without a scorecard.

### G4. Context assembly / working-memory manager under-designed

The runtime bottleneck is what exactly enters the finite context window, in what order, at what
token budget — the core of MemGPT/Letta. The design borrows the "bounded working set" idea but
not the mechanism: no context-budget manager, no prioritization when retrieved memories exceed
budget, no summary-vs-full-text tradeoff.

- **Fix.** Specify a context-assembly component (the operative "working memory").

### G5. Episodes flattened to text

`content_raw TEXT` discards the structure of real agent episodes (tool calls, diffs, file paths,
error traces, outcomes) that would sharpen both retrieval and distillation.

- **Fix.** A typed episode schema (structured events), not a prose blob.

---

## Positioning tension (unstated)

The three documents encode a conceptual progression the repo does not acknowledge:
`MEMORY_LANDSCAPE` recommends file-first, adopt-don't-build, defer the heavyweight tier, avoid
lock-in; `MEMORY_DESIGN` shifts to Postgres-canonical + LangMem-behind-RAC; `ARCHITECTURE` lands
on building Engramory as a platform. The final position is the strongest of the three but
overrides the landscape document's own advice ("do not build your own; defer the infrastructure")
without stating that it does.

- **Fix.** State the leap explicitly — why per-agent distillation, consolidation control, and
  portability justify building a platform rather than adopting Mem0/Cipher — and record that this
  is a larger commitment than "adopt a tool." Mark the two research documents as historical input
  superseded by ARCHITECTURE/ADRs where they conflict.

---

## Prioritized recommendations

| # | Change | Rationale |
|---|--------|-----------|
| 1 | Memory-utility feedback loop (retrieval → outcome → confidence/pruning) + eval harness | Converts distillation tuning from blind to measured |
| 2 | Define `confidence` semantics + outcome-based update rule | Makes quarantine/pruning principled |
| 3 | Contradiction detection + context-scoped validity | Prevents stale-fact resurfacing and wrongful supersession |
| 4 | Memory safety (source trust-weighting, injection screening, cross-boundary quarantine) | Closes the self-poisoning loop |
| 5 | Split the "endless" claim; add an explicit forgetting policy; plan store growth | Corrects a false claim; makes retention designed |
| 6 | Type-specific distillation + multi-signal retrieval (intent routing, situation-triggered procedural) | Realizes the value of the three-type taxonomy |
| 7 | Context-assembly/working-memory manager; structured episodes | Addresses the runtime bottleneck and improves distillation input |
| 8 | State the build-vs-adopt leap; banner the superseded research docs | Resolves the governing conceptual frame for contributors |

**Retain unchanged:** the Postgres-canonical spine, the three memory types, provenance, temporal
validity, and the portability principle. Direct the next design effort at the learning half —
feedback, confidence dynamics, conflict, forgetting, safety, and evaluation — which determines
whether Engramory is a durable memory store or an agent that measurably improves with use.

The recommended way to act on this — build the plane, adopt the memory engine, and lead with
evaluation — is recorded in [../STRATEGY.md](../STRATEGY.md).
