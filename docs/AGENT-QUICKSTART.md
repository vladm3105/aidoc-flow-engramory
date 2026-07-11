# Agent Quickstart — remember and retrieve your first memory

Engramory is the aidoc-flow workspace's memory plane: agents store episodes,
the distillation pass turns them into retrievable memories, and retrieval
outcomes feed back into memory confidence.

**The agent interface is the `engramory` CLI** — the ADR-10 dev/CI face over
the authorized access surface. Any agent with a shell runs the same commands;
there is no per-vendor tool. Install first: [`INSTALL.md`](INSTALL.md).
Flag reference: `engramory <cmd> --help` and
[`../sdd/06_SPEC/SPEC-07_cli_face.yaml`](../sdd/06_SPEC/SPEC-07_cli_face.yaml)
(do not restate flags from memory — they drift).

Three tracks; all end with a retrieved memory and a recorded outcome.

## The loop (the model in four sentences)

`memory add` appends an **episode** (raw experience; idempotent by content
hash). `memory distill` projects episodes into retrievable **memories**
(interim reflect pass — the adopted distillation engine later replaces it;
the command stays). `memory search` returns scored hits, each carrying a
`retrieval_id`. `memory feedback` records whether a hit was `useful`,
`not_useful`, or `harmful` — this drives confidence updates, so **always
close the loop**.

## Track A — AI agent in a terminal

```bash
# once per repo (see INSTALL.md for the DSN)
engramory init --agent-id my-agent --project-id my-project \
  --tenant-id default --dsn "$ENGRAMORY_DSN"

# remember a durable fact
engramory memory add --content "this workspace pins postgres at pg16" --kind note

# make it retrievable, then find it
engramory memory distill
engramory memory search --query "postgres version" --json

# close the loop with the retrieval_id from the hit
engramory memory feedback --retrieval-id <id> --outcome useful

# retire a memory that turned out wrong (soft, audited)
engramory memory forget --memory-id <id> --reason "superseded by pg17 upgrade"
```

## Track B — CI / scripted usage

```yaml
- name: Record run outcome into engramory
  run: |
    pip install -e .
    engramory memory add --content "nightly eval: retrieval p95 held" --kind outcome
    engramory memory distill
```

Exit `3` is retryable (store unreachable) — `|| [ $? -eq 3 ]` keeps a
best-effort step green. Exit `1` is a real reject; do not route around it.

## Track C — interactive coding agent (Skill over the CLI)

Claude-based agents install the reference Skill; it carries the judgment
(what is worth remembering, when to search, always send feedback) and
delegates mechanics to the CLI:

```bash
mkdir -p .claude/skills/engramory-memory
cp <engramory-repo>/skills/engramory-memory/SKILL.md .claude/skills/engramory-memory/
```

**Other vendors** reach the identical capability by running the same CLI,
guided by their own instruction file pointing at this quickstart — see
[`AGENT-INTEGRATION.md`](AGENT-INTEGRATION.md) for per-vendor snippets.

## Curation discipline (what belongs in memory)

- **Store durable facts, not transcripts.** A decision, a lesson, a
  constraint, an outcome — not raw model I/O or logs.
- **Never store secrets, credentials, or personal data.**
- **Close the loop.** A search whose hits you used deserves a
  `memory feedback` call; it is the learning signal.
- **Forget deliberately.** `memory forget` is soft (end-dated, audited,
  provenance retained) — use it when a memory is wrong or superseded.

## Trust boundary (dev tier)

The identity in `.engramory/config.toml` is client-supplied and trusted only
under `ENGRAMORY_PROFILE=dev` (single-tenant dev host; the CLI refuses
otherwise, exit 2). Production access goes through the MCP gateway with
OIDC-verified identity (ADR-10). Every call — either face — is authorized
(default deny), audited, and fails closed.

## Deeper reading

| Question | Doc |
|---|---|
| Install / stand up the store | [`INSTALL.md`](INSTALL.md) |
| Wire any agent vendor | [`AGENT-INTEGRATION.md`](AGENT-INTEGRATION.md) |
| Every flag + exit code | `engramory <cmd> --help` · [`../sdd/06_SPEC/SPEC-07_cli_face.yaml`](../sdd/06_SPEC/SPEC-07_cli_face.yaml) |
| The tool contract (all six agent tools) | `../sdd/06_SPEC/SPEC-01_access_surface.yaml` |
| Why CLI + Skill now, MCP gateway for production | `../sdd/05_ADR/ADR-10_agent_facing_packaging.yaml` |
| Memory model (L0–L3, distillation) | [`MEMORY_DESIGN.md`](MEMORY_DESIGN.md) |
