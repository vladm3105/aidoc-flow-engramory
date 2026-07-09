# Engramory documentation

| Doc | Purpose |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Target architecture, SQL schema, portability matrix, build roadmap |
| [CORES.md](CORES.md) | The two bounded cores (Memory & Knowledge), their boundary, and the shared spine |
| [STRATEGY.md](STRATEGY.md) | Build strategy & recommendation: build the plane, adopt the engine, lead with evaluation |
| [MEMORY_DESIGN.md](MEMORY_DESIGN.md) | Historical design input: layered memory (L0–L3), distillation loop, portability |
| [PORTABILITY.md](PORTABILITY.md) | Self-hosted → GCP/Azure principles and migration |
| _(ROADMAP.md consolidated into `../roadmap/ROADMAP.md` 2026-07-08 per PLAN-003 §5.5 Wave 3; see `DECISIONS.md` E-0001)_ | Canonical roadmap now at `../roadmap/ROADMAP.md` — includes both MVP cycle plan + engineering Phase view |
| [HOW_TO_USE_THE_FRAMEWORK.md](HOW_TO_USE_THE_FRAMEWORK.md) | Authoring SDD artifacts the UCX / aidoc-flow way |
| [UCX_INTEGRATION.md](UCX_INTEGRATION.md) | How Engramory fits UCX and replaces `ucx_kb` |
| [GLOSSARY.md](GLOSSARY.md) | Terms |
| [adr/](adr/) | Conceptual/descriptive architecture decision records |
| [research/](research/) | Background reviews and the memory-tool landscape |

The SDD lifecycle artifacts (BRD→IPLAN) live in the [`sdd/`](../sdd/) tree; `sdd/05_ADR/` holds the canonical implementing ADRs.
