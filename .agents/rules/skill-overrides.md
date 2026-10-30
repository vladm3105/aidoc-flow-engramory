---
paths:
  - "**/.agents/skills/**"
  - "**/.agents/agents/*.md"
  - "**/SKILL.md"
---

# Handling repo-local skill and agent name collisions

Project components live under `.agents/`; native loader mirrors (`.claude/`)
are adaptation artifacts outside the scan. Component precedence is asymmetric:

- A **project agent overrides a user agent** with the same frontmatter `name`.
- A **user skill masks a project skill** with the same directory name.

Keep one copy where possible. The collision detector encodes these two different
outcomes; do not infer precedence from file location alone. Canon vs
project-local ownership, the `-local` suffix, and the pull process are governed
by `.agents/GOVERNANCE.md`.

## Project-agent overrides

A deliberate project-agent override declares itself directly under its H1:

```markdown
> **OVERRIDES GLOBAL** — `~/.agents/agents/<file>.md`
> **Why:** <what this repo needs that the user agent does not do>
> **Reconciled:** YYYY-MM-DD
```

- Keep the marker in the body, not frontmatter.
- Start the quote line exactly with `> **OVERRIDES GLOBAL**`. A prose mention
  does not count.
- Refresh `Reconciled:` when comparing it with the user agent.

An undeclared project-agent collision is a defect. Either add the declaration
or remove the duplicate. Agent identity comes from frontmatter `name`, not the
filename. When the project agent must coexist with rather than replace the
user agent, give it a distinct `<name>-local` name instead; different names
never collide.

## Same-name project skills

A project skill cannot override a user skill. A declaration marker changes
nothing: the project skill stays masked under the unqualified command. Resolve
the collision by doing one of these:

1. Rename the project skill to `<name>-local` when it is intentionally
   project-specific (see `.agents/GOVERNANCE.md`).
2. Promote the better definition to user scope and remove the project copy.
3. Remove the user skill when the project definition genuinely must own the
   unqualified name.

A `.agents/skills/<name>/` directory containing helper files but no
`SKILL.md` is infrastructure, not a skill collision.

## Vendoring a skill's executable gate

Some skills install files that CI or hooks must read from the checkout. Vendor
only the executable artifacts:

| Vendored file | Belongs in the repo? |
|---|---|
| gate script and workflow | yes |
| templates/snippets consumed by repo docs | yes |
| `SKILL.md` | no — it becomes a masked duplicate |
| installer | no — a stale copy list can recreate the duplicate |

Leave a README beside a vendored gate explaining why `SKILL.md` is absent, and
test that installers do not copy either `SKILL.md` or themselves.

## Run the detector

```bash
~/.agents/scripts/check-skill-shadowing.sh [--no-recurse] [--check-remote] [repo-path]
# exit 0 clean; 1 collision defect; 2 incomplete scan or usage error
```

When the global copy lives elsewhere, point the scan at it with
`AGENTS_CONFIG_DIR` (same invocation, that variable exported) —
`~/.agents` is the default root, not the only one.

Run it at the outermost repository. It follows initialized submodules recursively
by default. Automation must fail on both exit 1 and exit 2; exit 2 means the scan
could not conclude.

`--check-remote` (opt-in) classifies each hit against the origin default
(`origin/HEAD`, else `origin/main`, else `origin/master`, resolved per repo)
as COMMITTED (needs a PR) or LOCAL ONLY (already fixed upstream or never
committed — sync the checkout instead), and prints how far behind the checkout
is. It reads the local refs as-is, so fetch first when they may be stale.
Default output without the flag answers "what shadows me right now";
the flag answers "does this repo have a defect".

Exit 1 is wired in two places, not left to memory:

- CI (`config-checks.yml`, `shadowing-selfcheck` job) runs the detector's test
  suite and fails on exit 1 or 2.
- `start-session` runs the detector in the repo being worked at session open.
