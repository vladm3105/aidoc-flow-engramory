---
paths:
  - "**/*.md"
  - "**/*.rst"
  - "**/*.txt"
---

# Writing documentation

**Objective and factual.** Imperative verbs for procedures; conditionals for
error handling; precise data types; explicit scope limits; measurable impact
criteria where available.

**Never write in *prose docs*:** benefit statements ("this will help you…") ·
efficiency or
speed claims · ease-of-use assertions ("simply…", "just…") · future promises
("you'll be able to…") · superlatives (best, optimal, powerful, easy) · verbose
introductions · time estimates.

Planning artifacts (`PLAN-*`, `IPLAN-*`) are exempt from the time-estimate ban
only — effort estimation is their deliverable. The remaining bans stay
universal.

**Always state:** implementation complexity (1 = minimal config, 5 =
architectural change) · resource requirements (CPU / memory / network /
storage) · failure modes and error conditions · dependencies with versions ·
the validation procedure.

**Density:** one sentence per function description · tables for parameter specs
· bullets for configuration options · abbreviated notation (HTTP status codes,
RFC references).

**Code in docs:** inline is fine under ~50 lines; beyond that put it in a real
file and reference it as `[See Code Example: filename.py - function_name()]`.
Mermaid for complex logic. Pick the format that serves clarity.

**Size:** target ≤50,000 tokens (200KB) per file, hard cap 100,000 (400KB).
Split into sequential files (`doc_001.md`, `doc_002.md`) only past the cap or at
a logical boundary. Keep under 30KB where GitHub Copilot is a consumer, or ship
a companion summary. For Gemini CLI use the file-read tool rather than `@` above
10,000 tokens — no splitting needed.
