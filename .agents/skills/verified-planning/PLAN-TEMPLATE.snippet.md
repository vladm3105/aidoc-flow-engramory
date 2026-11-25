## Claim ledger

> Every load-bearing claim (file path, signature, field/key, event/enum value,
> behavioral assertion) cites the `file:line` you actually read. `UNVERIFIED`
> rows must be resolved before the plan is ready.
> `.agents/skills/verified-planning/check_plan.py` checks each citation resolves.
>
> A claim source CANNOT settle — a live setting, an API behaviour, a runtime
> observation — is `PROBE: <command>`, and its claim text must name the phase it
> **blocks** — a phase that EXISTS as a `### Phase <N>` heading or a bold
> `**B…**` ID below (the gate resolves the token against definitions, never
> prose mentions). The command
> must SETTLE the claim, not merely relate to it: if the claim is "X permits Y",
> the command attempts Y. Run it on a throwaway, never on the repo you care
> about. It reaches ready; the phase it gates does not. Do not answer an
> unmeasurable claim in prose: that is how one plan answered the same question
> three different ways across three review passes.

| #   | Claim                              | Symbol     | Citation           |
| --- | ---------------------------------- | ---------- | ------------------ |
| 1   | <claim>                            | `<symbol>` | <path>:<line>      |
| 2   | <claim> — **blocks Phase \<N\>**    | `n/a`      | PROBE: <command>   |

> Sub-typed probes (optional in `v1`/`v2`): use `PROBE[experiment]: <cmd>` for external/throwaway experiments that gate `### Phase <N>` without entering a Link-0 SWF, or `PROBE[link0]: <readonly_cmd>` for hermetic Link-0 read-only local checks (`pytest`, `ruff`, `mypy`, `true`, `echo`, `test`, `git` with zero shell metacharacters).

## Architecture Decision Scorecard (100-Point Model)

> Required when evaluating competing technical options in plans. Passing threshold: >= 80/100 total score; no individual pillar < 50% allocated weight.

| Pillar | Max Pts | Option A: <Recommended Approach> | Option B: <Alternative Approach> |
|---|:---:|:---:|:---:|
| **1. Security, Fencing & Least Privilege** | 25 | **pts** *(Rationale)* | pts *(Rationale)* |
| **2. Reliability, Rollback & Blast Radius** | 25 | **pts** *(Rationale)* | pts *(Rationale)* |
| **3. Operational Excellence & EaC** | 20 | **pts** *(Rationale)* | pts *(Rationale)* |
| **4. Developer & Agent DX** | 15 | **pts** *(Rationale)* | pts *(Rationale)* |
| **5. Performance & Token Economy** | 15 | **pts** *(Rationale)* | pts *(Rationale)* |
| **TOTAL SCORE** | **100** | **pts (APPROVED / REJECTED)** | pts (APPROVED / REJECTED) |

## Phases

> Phase definitions the ledger's PROBE rows resolve against. Keep the
> `### Phase <N>` shape (or bold `**B…**` IDs) — the gate does not accept
> prose mentions or other heading levels.

### Phase <N>

- <work gated by the ledger's PROBE rows>

## Review log

> ≥2 passes before ready. At least one pass MUST be an independent fresh-context
> review (dispatch the `verified-planning-reviewer` agent — `Agent` tool +
> explicit `model` only as fallback; author self-review does not count as
> the independent one). The final pass must state zero findings.

### Pass 1 - <ISO-date>

- <finding → how the plan changed>

### Pass 2 - <ISO-date> - independent

- <findings from the fresh-context reviewer; fold each fix back in>

> **Fold subtractively.** Deleting or narrowing a claim is free; ADDING one
> needs its own citation and a `NEW@passN` marker, because new prose is new
> review surface. If the ledger grew, or pass N+1 raises more load-bearing
> findings than pass N, **cut scope instead of folding again** — the loop does
> not converge while each fold manufactures its own next round. When a reviewer
> refutes a claim, delete it or mark it `PROBE`; do not assert the opposite.

> When the final independent pass is clean, end it with an explicit marker the
> gate recognizes unambiguously: **Result:** ready — no further findings.

<!-- plan-gate-v2-snippet:begin -->
## Task convergence

> Opt-in for `<!-- plan-gate: v2 -->` / `check_plan.py --v2`: every task carries a canonical 64-hex `SHA-256` (compute/update at Stage 1 with `check_plan.py --hash-tasks --fix <plan>`), 4-pillar scores (`0.0`–`5.0`), and status in `LOCKED | FIX | STALE | PROBE | REDESIGN`. `LOCKED` requires `Total >= 16.0`, every pillar `>= 2.5`, non-zero 64-hex hash, and an independent pass (`2..3` `### Pass N` headings max).

| Task ID | SHA-256 | P1 | P2 | P3 | P4 | Total | Status |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| TASK-1 | `<64-hex-sha256>` | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | PROBE |
<!-- plan-gate-v2-snippet:end -->
