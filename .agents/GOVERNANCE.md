# Canon vs local: governing `.agents/` across repos

This repo's `.agents/` is the canon every new project copies. This document
defines what is canon-owned vs project-owned, the `-local` suffix, and the
pull process by which a project refreshes its copies without destroying
adaptations.

Direction is pull-only: this repo never rolls out, deploys, or opens pull
requests into other repos — there is no canon-run rollout, to any project,
ever. Each project watches the canon for updates and pulls, installs, and
adapts what it needs, on its own schedule. The canon is read-only for
other projects; the only feedback channel is GH issues on this repo, and
the canon accepts no PRs from consumers — a change proposal arrives as an
issue and the canon maintainer implements it.

Scope: skills, agents, rules, and scripts under `.agents/`. Loader behavior
below is Claude Code's; the precedence contract and detector live in
`.agents/rules/skill-overrides.md`.

## Ownership

- **Canon-owned**: every file whose name canon ships, unchanged. Source of
  truth is this repo. A project copy is overwritten when the project pulls.
- **Project-owned**: `*-local` skills/agents, project agents carrying an
  `OVERRIDES GLOBAL` marker, and rule/script files whose filename canon does
  not ship. A pull never touches these.

## The naming rule

Canon keeps clean names (`verified-planning`, `code-reviewer`). A project
adaptation takes the `-local` suffix: `verified-planning-local`, with the
skill directory and frontmatter `name` matching as usual.

- The separator is a hyphen: it fits the name pattern
  (`^[a-z0-9]+(?:-[a-z0-9]+)*$`) and survives every checkout. Colons
  (`:default`, `:project`) fail validation and break Windows checkouts —
  never use them.
- There is no `.agents/local/` directory. Loaders scan fixed subpaths, so a
  `local/` tree would be silently inert. Adaptations live beside canon
  files, distinguished by name.
- Canon MUST never ship a `*-local` name. The suffix is reserved for
  project adaptations; a canon file using it would collide with the very
  mechanism that keeps pulls safe.
- Suffix only what is adapted. An unadapted project copy is byte-identical
  to canon and needs no marker — sameness is the default.

## Skills

A user skill masks a same-name project skill, so a same-name project skill
is dead on arrival. A project adaptation MUST rename to `<name>-local`:

- `.agents/skills/verified-planning-local/SKILL.md` with
  `name: verified-planning-local`.
- Record why the adaptation exists in the body. The canon namesake is
  implied by the name minus `-local`; the reason is not.
- `<name>-local` never collides with `<name>` — different names are clean
  by construction, and the detector stays silent.

## Agents

Agent identity is the frontmatter `name`, not the filename (keep them equal
by convention). Two cases:

- **Replacement** (usual): keep the same `name` and declare it with the
  `OVERRIDES GLOBAL` marker plus a `Reconciled:` date, per
  `.agents/rules/skill-overrides.md`. The project agent wins; a pull refreshes
  the comparison but never overwrites the file.
- **Coexistence** (rare): the project needs both behaviors addressable.
  Name the project agent `<name>-local`. Prefer replacement — two agents
  with overlapping purpose make dispatch ambiguous.

A same-name project agent without the marker is a defect either way.

## Rules and scripts

No suffix. Rules are additive: they load by `paths:` match, so a project
rule never shadows a canon rule — it adds coverage. Scripts are invoked by
explicit path; there is no name registry and nothing to shadow.

- Give project-specific rules and scripts filenames canon does not use;
  a pull refreshes only canon-owned filenames, so distinct names survive.
- Editing a canon-owned rule/script copy in place is allowed but fragile:
  a differing file is skipped (exit 1) until a `--force` pull overwrites
  it. Prefer a new file, or upstream the change as a GH issue on the
  canon repo.

## Pull process (project pulls from canon)

The project runs this, in the project checkout, on the project's schedule,
from a canon checkout:

```bash
bash <canon>/.agents/scripts/pull.sh [--dry-run] [--force] [--backup DIR] [--] <project> [...]
bash <canon>/.agents/scripts/pull.sh --global [--dry-run] [--force] [--backup DIR]
```

The script implements the steps below: it refreshes canon-owned
project-scope files (rules, the detector, GOVERNANCE.md, and the gate via
install.sh delegation), never touches project-owned files, reports
reconcile items instead of editing them, and runs the detector as
acceptance. Canon skills/agents are user-scope components and are never
copied into the project — install them globally from the canon checkout
(see README §Consuming the canon). Every pull records the canon SHA in
`.agents/.canon-sha` (project-owned); the next pull reports canon-side
deletions still present as review advice, never deletes them. User-scope
skills/agents refresh separately (`pull.sh --global`), with the same
skip-unless-forced semantics; user-added filenames are never touched,
and there is no `-local` concept at user scope. The steps remain the
contract the script implements and the manual fallback when it cannot
run.

1. Diff canon `.agents/` against the project's. Note added, removed, and
   changed canon-owned files.
2. Refresh canon-owned files with the canon bytes — differing files are
   skipped (exit 1, review each) unless `--force`, with `--backup DIR` to
   keep what it replaces; add new ones. Never touch `*-local` files,
   `OVERRIDES`-marked agents, or filenames canon does not ship.
3. For each `OVERRIDES`-marked project agent: compare against the new canon
   namesake. Refresh the `Reconciled:` date; drop the override if canon now
   covers the need.
4. For each `*-local` skill or agent: compare against the new canon
   namesake (the name minus `-local`). Keep, update, or retire it.
5. Flag project files that now duplicate canon content under a different
   name. Do not auto-delete; review each one.
6. Validate: confirm the canon gate passed in canon CI on the pulled
   commit, and run the detector against the project
   (`check-skill-shadowing.sh <project-path>`). Both MUST pass.

## Failure modes

| Signal | Cause |
|---|---|
| Detector reports a masked skill | same-name project skill; rename to `<name>-local` or remove it |
| Detector reports an undeclared agent | same-name project agent without the marker; declare or remove it |
| Adaptation lost after a pull | it lived in a canon-owned file; move it to `-local`/override/distinct name |
| Skill or agent silently ignored | it sits outside a scanned subpath (e.g. a `local/` dir); move it into place |

## Layout reference

| Path | Contents |
|---|---|
| `.agents/GOVERNANCE.md` | this document; canon-owned, travels with the canon |
| `.agents/skills/<name>/` | canon skills (clean names; canon never ships `*-local`) |
| `.agents/skills/<name>-local/` | project skill adaptations |
| `.agents/agents/` | canon agents, plus project agents: same-name with `OVERRIDES GLOBAL` (replacement) or `<name>-local` (coexistence) |
| `.agents/rules/`, `.agents/scripts/` | canon files plus project files under distinct filenames; no suffix |
