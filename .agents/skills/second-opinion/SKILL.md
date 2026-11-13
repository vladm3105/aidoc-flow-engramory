---
name: second-opinion
description: >-
  Run an independent fresh-context judgment of a high-stakes output, recommendation, plan, approval item, or status rollup before it reaches a decision-maker. Judge and generator must be different; this never replaces human approval.
---

# Second opinion (LLM-as-judge)

An independent review pass that raises decision **quality** before work leaves
your hands. It **does not replace a human gate** — where a human authorizes an
action, nothing the judge passes is thereby authorized.

## The independence rule — judge ≠ generator

This is the whole mechanism; everything else is packaging.

- The reviewer gets the **artifact plus its cited sources** — **not** the
  generator's reasoning or chain-of-thought. Handing over the reasoning invites
  the judge to grade the rationalization instead of the work.
- **Dispatch the `second-opinion-judge` agent.** Fresh context by construction;
  the rubric, output format and independence rules are baked into
  `${AGENTS_CONFIG_DIR:-~/.agents}/agents/second-opinion-judge.md`. **Do not re-author a judge prompt
  ad hoc** — prompt variance silently weakens the judge, and a weakened judge
  still returns confident-looking verdicts. Where the agent is not installed
  (project-only consumers), dispatch via the `Agent` tool with an explicit
  `model` instead — same artifact-plus-sources input, same independence rules.
- **The rubric lives in the agent file, not here.** One source; duplicated
  rubrics drift. Rubric changes are edits to the agent, versioned by its
  `rubric vN` output stamp.
- Diversity axes, in increasing strength: same model fresh context (default) →
  a different model tier via the `model` override → a different vendor.
  A different vendor goes through the platform LLM gateway
  **platform-litellm-laya** (OpenAI-compatible `LLM_URL` + the project's virtual
  key `LLM_API_KEY`, a gateway alias such as `tier-frontier`), the same endpoint
  the aidoc-hybrid-ci self-review uses. Never call an upstream vendor API with a
  direct provider key.

**This skill owns the independent-review contract.** `verified-planning` and
`preprod-review` apply it to their own artifacts; where any of them disagree on
what independence means, this file governs.

## When to run

- Any **high-stakes or irreversible** recommendation before it is acted on.
- Before an **approval item is surfaced to a human** (see `approval-gate`).
- Before a **status rollup** reaches whoever will act on it.
- Before **arming auto-merge on a PR** — judge the diff with the agent's
  code-change lenses (Pass 2; the author's own gates are Pass 1).
- A recommendation to take a non-stack-compliant path — judge the
  violation, the risk, and the compliant alternative (see the
  `platform-standards` rule) before it reaches approval.
- **Granularity:** judge the *plan*, the *final*, and any *irreversible step* —
  not every micro-step. Skip trivial green chatter.

## Output

The agent emits a `SECOND-OPINION — <artifact>` block carrying verdict /
confidence / issues / fix, stamped with its rubric version. The block's
authoritative shape lives in `${AGENTS_CONFIG_DIR:-~/.agents}/agents/second-opinion-judge.md` — do not copy it
here; the skill's copy would stale on the next rubric bump.

- **pass** → proceed, and stamp the provenance line (reviewer + rubric version)
  on the artifact so an audit can tell which judge saw it.
- **revise** → return to the generator with the issues named; re-review.
- **block, low confidence, or judge-vs-generator disagreement** → **escalate
  with the dissent stated**. Do not bury a disagreement by re-running until it
  agrees.

## Cost and limits — be honest about these

- **Single judge** by default. A jury (diverse panel) is for the highest-stakes
  cases only.
- The judge **is itself an LLM** — injectable, and biased by position, verbosity
  and self-preference. It can *introduce* errors as well as catch them.
- **Agreement ≠ correctness** for unverifiable claims. Two models liking a
  strategy call is not evidence the call is right.
- Don't judge trivial work. A judge that runs on everything gets skimmed, and a
  skimmed judge is worse than none because it launders work as reviewed.

## Measure it, or drop it

**Log every real run** — date · item · verdict · confidence · reviewer ·
outcome — and mark each one **catch / false-positive / redundant / miss**. Every
~5–10 cycles, read the tally and make a **keep / narrow / drop** decision.

This ledger is the guard against cargo-culting the judge. A review pass that
cannot show catches is costing tokens and lending false confidence, and the
honest response is to narrow its scope or stop running it. **A skill that cannot
be shown to earn its keep should be retired** — including this one.

Where the log lives is the repo's call: whatever its governance table declares
for decision records, or a simple ledger file. If a repo declares nothing, create
the ledger file — or, where even that is unwanted, keep the tally as a memory
(`metadata.type: project`).

**Not the session report.** A tally read at cycle 10 cannot live in something
discarded at cycle 1, which makes the keep / narrow / drop decision unrunnable
and this whole section decorative.

## `[workspace]` — aidoc-flow specifics

In the aidoc-flow workspace this implements the workspace's **judge-v0**
decision for the Crew (record kept in the workspace, not in this canon),
and:

Paths below (`ops/judge-log.md`, `/weekly`, `/supervisor-brief`,
`ops/inbox/`, `weekly-cadence`) live in the aidoc-flow workspace repo,
not in this canon.

- Run it before a `/weekly` rollup or `/supervisor-brief` reaches the founder,
  and before staging an `ops/inbox/` 🟡/🔴 item (it extends `approval-gate`).
- **Fixed-primary for governance:** governance and decision outputs use one
  primary generator plus this judge — no divergent hybrid candidates.
- The run log is `ops/judge-log.md`; the founder or `ceo` marks the outcome
  column, and the tally drives the keep/narrow/drop call.
- The rubric extends the `weekly-cadence` quality bar.

## Adaptive multi-lens dispatch & 2-pass pre-CI fold orchestration

When dispatching a fresh-context subagent judge for plans or code diffs (before outer CI `task review`):

- **Pass caller-selected 2–4 best-fit lenses as context (without altering the canonical judge rubric in `second-opinion-judge.md`):** Use `aiops sdd select-lenses` or `aiops sdd review-clusters` to select the 2–4 best-fit engineering lenses (`google_sre_design`, `anthropic_evaluator_boundary`, `aws_saga_resilience`, `microsoft_stride_contracts`; minimum-2 fallback `google_sre_design` + `microsoft_stride_contracts`) and include `<selected_review_lenses>` plus `<cross_chunk_index>` in the artifact context packet.
- **2-pass review-and-fold convergence (3 reviews max):** Run **Pass 1** in a fresh-context subagent (`reviewer_id != generator_id`) to surface all Critical, Major, and Medium gaps; fold all fixes and regression tests; then run **Pass 2** to confirm `0` remaining blocking findings before invoking `task review` or arming auto-merge.

