# Contributing to Engramory

Thanks for helping build Engramory. This guide keeps the codebase portable and the memory layer trustworthy.

## Principles (do not break these)

1. **Depend on ports, not vendors.** Application code imports from `engramory.ports`, never a concrete adapter. New infrastructure = a new adapter behind an existing port.
2. **Postgres is canonical.** Distilled memory lives in Postgres as plain rows with `content_raw` + `provenance`. Embeddings and the graph are **rebuildable projections**, never the source of truth.
3. **One model gateway.** All LLM/embedding calls go through the LiteLLM endpoint. No direct provider SDK calls in core code.
4. **Keep memory migratable.** Always store the raw text a memory was distilled from, plus the embedding model + dims, so any model/platform migration can re-embed.

## Workflow

1. Open an issue / ADR for non-trivial design choices (see Architecture decisions below).
2. Branch, implement behind the relevant port, add tests.
3. Run `make lint typecheck test` before opening a PR.
4. PRs require green CI and one review. A CI workflow (`.github/workflows/ci.yml`) and a `tests/` dir exist, so this gate is satisfiable via `make lint test` (and `make typecheck`).

## Architecture decisions

Significant decisions are recorded as ADRs. Canonical implementing decisions go in `sdd/05_ADR/`; conceptual/descriptive records live in `docs/adr/`. Add a new numbered file using the existing format.

## Code of conduct

Be respectful and constructive. Assume good faith.
