# Installing Engramory (dev tier)

> **Scope:** single-tenant dev/pre-prod host (ADR-10 dev tier). Production
> deployment (MCP gateway + OIDC) is a later cycle. Contract for the CLI used
> below: `sdd/06_SPEC/SPEC-07_cli_face.yaml`.

## Prerequisites

- Python ≥ 3.12
- Docker + Docker Compose
- Ports: the store publishes on `5432` by default. **If 5432 is taken on your
  host** (common when another Postgres runs locally), set an override before
  `make up` — e.g. in `.env` at the repo root:

  ```bash
  POSTGRES_HOST_PORT=55432
  ```

  The Makefile loads `.env` automatically; compose then publishes
  `55432 -> 5432`.

## 1. Stand up the store

```bash
git clone https://github.com/vladm3105/aidoc-flow-engramory.git
cd aidoc-flow-engramory
make up        # pgvector/pg16 + the rest of the dev stack
make migrate   # applies db/migrations/*.sql in order
```

Credentials default to `engramory` / `change_me` / db `engramory`
(override via `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` in `.env`).

## 2. Install the CLI

```bash
pip install -e .
engramory --help
```

`python -m engramory` is an exact alias when the entry point is not on PATH.

## 3. Initialize your agent identity

```bash
engramory init \
  --agent-id my-agent \
  --project-id my-project \
  --tenant-id default \
  --dsn "postgresql://engramory:change_me@localhost:${POSTGRES_HOST_PORT:-5432}/engramory"
```

This scaffolds `.engramory/config.toml` (gitignored — it carries the DSN),
registers the agent's L3 profile, and is idempotent. The identity in this
file is client-supplied and therefore trusted **only** in the dev tier:
the CLI refuses to run when `ENGRAMORY_PROFILE` is anything but `dev`
(exit 2, per ADR-10).

## 4. Smoke check

```bash
engramory status --json
# {"store": "ok", "episodes": 0, "kb_sections": 0}
```

First full round-trip (see `AGENT-QUICKSTART.md` for the agent workflow):

```bash
engramory memory add --content "first remembered fact"
engramory memory distill        # interim reflect: episodes -> retrievable memories
engramory memory search --query "fact" --json
```

## Exit codes (normative, SPEC-07)

| Code | Meaning |
|---|---|
| `0` | success |
| `1` | fail-closed reject — authz deny, invalid input, unknown id |
| `2` | usage / config / environment (including the dev-tier fence) |
| `3` | retryable — the store is unreachable; re-run later |

## Uninstall / reset

```bash
make down                # stop the stack (data volumes persist)
docker compose down -v   # stop AND delete data volumes
pip uninstall engramory
rm -rf .engramory
```
