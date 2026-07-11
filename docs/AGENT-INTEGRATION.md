# Using Engramory from an AI agent — any vendor

> How an AI coding agent of any vendor uses Engramory as its memory, through
> the `engramory` CLI (ADR-10 dev/CI face; contract:
> `../sdd/06_SPEC/SPEC-07_cli_face.yaml`). Companion: worked first-memory walk
> in [`AGENT-QUICKSTART.md`](AGENT-QUICKSTART.md); the Claude reference Skill
> at `../skills/engramory-memory/SKILL.md`.

## The one idea

**Engramory's agent interface is the `engramory` CLI — vendor-neutral.**
Every agent, regardless of vendor, remembers and retrieves through the same
commands (`init · memory add · memory distill · memory search · memory
feedback · memory forget · profile get · status`). Agents differ only in *how
they are told* to run the CLI — the instruction surface each vendor reads
(`SKILL.md`, `GEMINI.md`, `AGENTS.md`, `.github/copilot-instructions.md`, a
rules file). The instruction file carries the **judgment** (what is worth
remembering, when to search, always close the feedback loop); the CLI carries
the **mechanics**.

The MCP gateway is the authenticated production face and arrives in a later
cycle (ADR-10); until then, every shell-capable agent is covered by the CLI.

## The integration pattern

An agent is taught to use Engramory by putting three things into its native
instruction file, and nothing more:

1. **Triggers** — "at session start, `memory search` what you know about this
   project; when you learn a durable fact (a decision, constraint, lesson,
   outcome), `memory add` it; after using a retrieved hit, send
   `memory feedback`."
2. **The curation discipline** — durable facts, not transcripts; never
   secrets, credentials, or personal data; forget deliberately (soft,
   audited).
3. **A pointer to the mechanics** — "run `engramory <cmd> --help`; see
   `docs/AGENT-QUICKSTART.md`."

Do **not** restate the CLI's flag reference inside the instruction file — it
drifts. Point at SPEC-07 / `--help` instead.

## Per-vendor setup

| Agent / host | Instruction surface | How to wire it |
|---|---|---|
| **Claude** (Claude Code, claude.ai) | `.claude/skills/engramory-memory/SKILL.md` | Copy the reference Skill (below) |
| **Gemini** (Gemini CLI) | `GEMINI.md` at repo root | Add the memory snippet (below) |
| **OpenAI Codex** | `AGENTS.md` at repo root | Add the memory snippet |
| **GitHub Copilot** | `.github/copilot-instructions.md` | Add the memory snippet |
| **Cursor / Windsurf** | `.cursor/rules/*` / `.windsurfrules` | Add the memory snippet |
| **Any other shell agent** | that agent's rules file | Add the memory snippet, pointing at `AGENT-QUICKSTART.md` |

### Claude — the reference Skill

```bash
mkdir -p .claude/skills/engramory-memory
cp <engramory-repo>/skills/engramory-memory/SKILL.md .claude/skills/engramory-memory/
```

The Skill is the worked reference for what every other vendor's instruction
file should say.

### Every other vendor — the memory snippet

Paste into the vendor's instruction file:

```markdown
## Engramory — persistent memory for this repo

This repo uses Engramory as agent memory (CLI: `engramory`; requires a one-time
`engramory init` — see `docs/INSTALL.md`).

- **Session start:** `engramory memory search --query "<current task topic>" --json`
  and read the hits before repeating work.
- **When you learn a durable fact** (decision, constraint, lesson, outcome —
  not transcripts, never secrets/credentials/personal data):
  `engramory memory add --content "<the fact>" --kind <decision|error|outcome|note>`
  then `engramory memory distill` to make it retrievable.
- **After using a retrieved hit:** close the loop —
  `engramory memory feedback --retrieval-id <id> --outcome <useful|not_useful|harmful>`.
- **When a memory is wrong/superseded:**
  `engramory memory forget --memory-id <id> --reason "<why>"`.

Exit codes: 0 ok · 1 rejected (fix the input, don't route around) ·
2 config/usage (run `engramory init`) · 3 retryable (store down; try later).
Mechanics: `engramory <cmd> --help` / `docs/AGENT-QUICKSTART.md`.
```

## Trust boundary

The CLI trusts `.engramory/config.toml` identity **only** in the dev tier
(`ENGRAMORY_PROFILE=dev`; anything else exits 2 per ADR-10). Every call is
authorized default-deny, audited, and fails closed — a denial (exit 1) is the
authorization boundary working, not an error to work around.
