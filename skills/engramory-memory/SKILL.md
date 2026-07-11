---
name: engramory-memory
description: >-
  Use Engramory as persistent memory across sessions and projects. Use at
  session start to recall what is already known about the task or project
  (memory search), when a durable fact is learned — a decision, constraint,
  lesson, or outcome — (memory add + distill), after acting on a retrieved
  hit (memory feedback), and when a memory proves wrong (memory forget).
  Requires the `engramory` CLI on PATH and a `.engramory/config.toml`
  (run `engramory init` per docs/INSTALL.md). Not for transcripts, logs,
  secrets, credentials, or personal data.
---

# Engramory memory — remember, retrieve, close the loop

Engramory is the workspace's memory plane. Your job with this skill is the
**judgment** — what is worth remembering, when to recall, and always closing
the feedback loop; the **`engramory` CLI is the mechanics**. Do not restate
flags from memory — run `engramory <cmd> --help`, and read
`docs/AGENT-QUICKSTART.md` (worked walk) or `sdd/06_SPEC/SPEC-07_cli_face.yaml`
(contract) if present in the repo.

## When to recall

- **Session start / new task:** `engramory memory search --query "<topic>"
  --json` before repeating work — prior lessons, decisions, and constraints
  may already exist.
- Prefer specific queries (an error message, a component name) over broad
  ones; hits are token-budgeted.

## When to remember (and when NOT to)

**Remember** a durable, reusable fact:

| Situation | `--kind` |
|---|---|
| A decision was made (and why) | `decision` |
| An error's root cause was found | `error` |
| A run/experiment concluded | `outcome` |
| A reusable lesson or constraint | `note` |

```bash
engramory memory add --content "<one distilled sentence-to-paragraph>" --kind <kind>
engramory memory distill   # projects episodes into retrievable memories
```

**Do NOT remember:** raw transcripts or logs (distill the fact instead);
anything containing a secret, credential, token, or personal data; transient
session chatter with no reuse value.

## Always close the loop

After you *use* a retrieved hit (it shaped your action), record the outcome —
this is the learning signal that tunes memory confidence:

```bash
engramory memory feedback --retrieval-id <from the hit> --outcome useful   # or not_useful | harmful
```

Mark `harmful` when acting on the memory made things worse — it matters more
than useful.

## When memory is wrong

Retire it (soft: end-dated, audited, provenance retained — never a hard
delete):

```bash
engramory memory forget --memory-id <id> --reason "<why it is wrong/superseded>"
```

## Exit codes and boundaries

`0` ok · `1` rejected — an authorization deny or invalid input; the boundary
working, never route around it · `2` config/usage — run `engramory init`
(see `docs/INSTALL.md`); also the dev-tier fence (this CLI only runs with
`ENGRAMORY_PROFILE=dev`, per ADR-10) · `3` retryable — store unreachable,
try again later; your task is not failed.

## Notes

- **Other agent vendors** reach the same capability by running the same CLI,
  guided by their own instruction file — see `docs/AGENT-INTEGRATION.md`.
  There is no per-vendor Engramory tool.
- Memory is scoped (agent/project/tenant) and every call is audited; you only
  ever see memory your scopes grant.
