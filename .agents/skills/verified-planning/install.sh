#!/usr/bin/env bash
# Install the verified-planning GATE into one or more repos as a committed copy.
# The master is this script's own directory.
#
# What gets committed into a repo, and what deliberately does NOT:
#
#   YES — check_plan.py, plan-gate-reusable.yml, the pre-commit snippet, the
#         plan template, tests. CI and the hook run inside the repo, so the
#         gate has to be in the tree they check out. The caller workflow
#         (`.github/workflows/plan-gate.yml`) is consumer-owned: install.sh
#         never creates or refreshes it, so tuning there survives updates.
#
#   NO  — SKILL.md. The skill is already available in EVERY repo from
#         ~/.agents/skills/. Current precedence makes that user skill
#         win over a same-name project skill, so another SKILL.md is a masked
#         duplicate that never updates.
#
#   NO  — install.sh itself. A repo carrying its own installer can re-infect
#         itself with a stale copy list, which is how the above would come back.
#
# Re-running this is the remedy as well as the install: it DELETES a stale
# SKILL.md left by an older version of this script.
#
# The installer never silently overwrites: a destination file that exists and
# differs from the master is SKIPPED with a warning, and the run exits 1 at
# the end when anything was skipped. Pass --force to overwrite differing
# files, --backup DIR to keep copies of what --force replaces, --dry-run to
# print what would happen without changing anything.
#
# Usage: install.sh [--dry-run] [--force] [--backup DIR] [--no-ci] [--] <repo-dir> [<repo-dir> ...]
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"   # the master skill dir
DRY_RUN=0
FORCE=0
NO_CI=0
BACKUP_DIR=""
SKIPPED=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

usage() {
  echo "usage: install.sh [--dry-run] [--force] [--backup DIR] [--no-ci] [--] <repo-dir>..." >&2
  exit 2
}

install_file() { # $1 = src, $2 = dst, $3 = repo — never silently overwrites
  local src="$1" dst="$2" repo="$3" rel ns
  if [ ! -e "$dst" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then echo "would install: $dst"; return 0; fi
    mkdir -p "$(dirname "$dst")" || return 2
    # A failed copy must fail the install, not print success over missing bytes.
    cp "$src" "$dst" || return 2
    return 0
  fi
  if cmp -s "$src" "$dst"; then return 0; fi
  if [ "$DRY_RUN" -eq 1 ]; then echo "would skip differing file (use --force): $dst"; return 0; fi
  if [ "$FORCE" -eq 1 ]; then
    if [ -n "$BACKUP_DIR" ]; then
      rel=${dst#"$repo"/}
      # Per-repo namespace: a shared --backup across repos would otherwise
      # stack every repo's originals onto the same relative paths.
      ns=$(basename "$repo")
      mkdir -p "$BACKUP_DIR/$ns/$(dirname "$rel")" || return 2
      cp "$dst" "$BACKUP_DIR/$ns/$rel" || return 2
    fi
    cp "$src" "$dst" || return 2
    echo "overwrote (forced): $dst"
    return 0
  fi
  echo "SKIP differing file (unchanged; use --force to overwrite): $dst"
  SKIPPED=$((SKIPPED+1))
  return 0
}

backup_delete() { # $1 = file, $2 = repo — stage the doomed file in BACKUP_DIR when set, then remove
  # Mirrors pull.sh: a --backup run promises reversibility, and self-heal
  # deletions are deletions. Layout matches install_file.
  local file="$1" repo="$2" rel ns
  if [ -n "$BACKUP_DIR" ]; then
    rel=${file#"$repo"/}
    ns=$(basename "$repo")
    mkdir -p "$BACKUP_DIR/$ns/$(dirname "$rel")" || return 2
    cp "$file" "$BACKUP_DIR/$ns/$rel" || return 2
  fi
  rm -f "$file" || return 2
}

install_into() {  # $1 = repo dir
  local repo="$1" f t
  local dst="$repo/.agents/skills/verified-planning"
  # Early refusals RETURN (not exit) so a multi-repo run attempts the rest;
  # the driver collects failures and exits 2 at the end.
  if [ ! -d "$repo" ]; then echo "error: not a directory: $repo" >&2; return 2; fi
  if ! git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    echo "error: not a git repository: $repo (manual fallback: GOVERNANCE.md §Pull process)" >&2; return 2
  fi
  if [ "$DRY_RUN" -eq 0 ]; then
    mkdir -p "$dst/tests" || return 2
    # --no-ci leaves no GitHub behind: other forges wire the gate into their
    # own CI (see SKILL.md) instead of carrying a dead .github/ tree.
    if [ "$NO_CI" -eq 0 ]; then mkdir -p "$repo/.github/workflows" || return 2; fi
  fi
  # guard: never copy the master onto itself (a repo running its own copy)
  if [ "$dst" -ef "$SRC" ]; then echo "skip: $repo is the master"; return; fi
  for f in check_plan.py PLAN.md DESIGN.md PLAN-TEMPLATE.snippet.md \
           precommit-hook.snippet.txt plan-gate-reusable.yml; do
    install_file "$SRC/$f" "$dst/$f" "$repo" || return 2
  done
  # tests tree, file by file, so differing files get the same skip/force rule
  # (process substitution, not a pipe: the SKIP counter must survive the loop)
  while IFS= read -r -d '' t; do
    install_file "$SRC/tests/$t" "$dst/tests/$t" "$repo" || return 2
  done < <(cd "$SRC/tests" && find . \( -type d \( -name __pycache__ -o -name .pytest_cache \) -prune \) -o \( -type f ! -name '*.pyc' -print0 \))
  # self-heal: older versions of this script installed these two. Both are
  # defects — SKILL.md is masked by the user skill, install.sh re-infects.
  # The SKILL.md removal needs the mask to exist: with no user-scope copy
  # (container/CI without a writable $HOME), deleting it silently drops the
  # skill in that environment — so keep it and say so instead.
  if [ -f "$dst/SKILL.md" ]; then
    # Neither root set (minimal container with env -i): say what to set
    # instead of dying on "HOME: unbound variable" inside the expansion.
    if [ -z "${AGENTS_CONFIG_DIR:-}" ] && [ -z "${HOME:-}" ]; then
      echo "error: cannot resolve user root — set AGENTS_CONFIG_DIR or HOME" >&2
      return 2
    fi
    user_skill="${AGENTS_CONFIG_DIR:-$HOME/.agents}/skills/verified-planning/SKILL.md"
    if [ -f "$user_skill" ] && ! [ "$user_skill" -ef "$dst/SKILL.md" ]; then
      if [ "$DRY_RUN" -eq 1 ]; then echo "would remove stale $dst/SKILL.md"
      else backup_delete "$dst/SKILL.md" "$repo" || return 2
        echo "removed $dst/SKILL.md — it was masked by the user skill"
      fi
    elif [ -f "$user_skill" ]; then
      echo "warning: kept $dst/SKILL.md — user scope is the repo itself (nothing masks it here)"
    else
      echo "warning: kept $dst/SKILL.md — no user-scope copy masks it here"
    fi
  fi
  if [ -f "$dst/install.sh" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then echo "would remove stale $dst/install.sh"
    else backup_delete "$dst/install.sh" "$repo" || return 2
      echo "removed $dst/install.sh — stale copy list, re-infection path"
    fi
  fi
  cat > "$TMP/README.md" <<'README' || return 2
# verified-planning — committed gate only

This directory holds the **gate**, not the skill. `check_plan.py` and
`plan-gate-reusable.yml` live here because CI and the pre-commit hook run
inside this repo and need them in the checked-out tree.

**Do not add a `SKILL.md` here.** The skill is available in every repo from
`~/.agents/skills/verified-planning/`. A local `SKILL.md` does not
supplement that copy — the user skill masks it, so the local file can drift
while appearing authoritative.

`PLAN.md` / `DESIGN.md`, when present, are canon history — the record this
gate was extracted from — not live docs. The live gate is `check_plan.py`.

Refresh this directory with `$CANON/.agents/skills/verified-planning/install.sh <repo>`
(`$CANON` = your canon checkout — README §Consuming the canon). The command
names the convention, never this installer's own absolute path: a literal
path would publish username and layout on push, and rot on every machine
that is not this one.
README
  install_file "$TMP/README.md" "$dst/README.md" "$repo" || return 2
  if [ "$NO_CI" -eq 0 ]; then
    install_file "$SRC/plan-gate-reusable.yml" "$repo/.github/workflows/plan-gate-reusable.yml" "$repo" || return 2
  # The caller is consumer-owned and never touched here. If the file present
  # is the pre-split canon workflow (inline run block, not a `uses:` caller),
  # say how to migrate; a real caller is silently none of our business.
  if grep -q 'name: Gate changed PLAN files' "$repo/.github/workflows/plan-gate.yml" 2>/dev/null \
     && ! grep -q 'plan-gate-reusable.yml' "$repo/.github/workflows/plan-gate.yml" 2>/dev/null; then
    echo "note: $repo/.github/workflows/plan-gate.yml is the pre-split canon workflow — migrate it to a caller of plan-gate-reusable.yml (see verified-planning/SKILL.md)"
  fi
  else
    echo "note: --no-ci — skipping the GitHub workflow; wire check_plan.py into your CI (see SKILL.md)"
  fi
  # If the repo ignores the install path, re-include the gate — testing the
  # EFFECTIVE ignore state, not the ignore file's spelling: `.agents/*`,
  # `/.agents/` and a bare `.agents/` line all hide the gate equally, and a
  # pattern match on the file misses every variant but one. Same
  # deny-then-allow-by-extension shape as the canon .gitignore, so a stray
  # .env under the gate stays ignored.
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "would ensure .gitignore re-includes the gate when it is ignored"
  else
    if git -C "$repo" check-ignore -q -- .agents/skills/verified-planning/check_plan.py 2>/dev/null \
       && ! grep -q 'verified-planning skill must be committed' "$repo/.gitignore" 2>/dev/null; then
      cat >> "$repo/.gitignore" <<'GITIGNORE' || return 2

# verified-planning skill must be committed (overrides .agents/ ignore)
!.agents/
.agents/*
!.agents/skills/
.agents/skills/*
!.agents/skills/verified-planning/
.agents/skills/verified-planning/**
!.agents/skills/verified-planning/**/
!.agents/skills/verified-planning/**/*.md
!.agents/skills/verified-planning/**/*.py
!.agents/skills/verified-planning/**/*.sh
!.agents/skills/verified-planning/**/*.yml
!.agents/skills/verified-planning/**/*.txt
GITIGNORE
    fi
    # Warn whenever the gate ends up ignored, however we got here. A
    # pre-existing marker plus a defeating later rule used to sail through
    # silently and print success with an uncommittable gate (#185).
    if git -C "$repo" check-ignore -q -- .agents/skills/verified-planning/check_plan.py 2>/dev/null; then
      echo "warning: gate still ignored in $repo — check .gitignore; the gate will not be committed" >&2
    fi
  fi
  echo "installed into $repo"
}

REPOS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --force) FORCE=1; shift ;;
    --no-ci) NO_CI=1; shift ;;
    --backup)
      [ $# -ge 2 ] || usage
      BACKUP_DIR=$2; shift 2 ;;
    --backup=*) BACKUP_DIR=${1#--backup=}; shift ;;
    -h|--help) usage ;;
    --) shift; while [ $# -gt 0 ]; do REPOS+=("$1"); shift; done; break ;;
    -*) echo "error: unknown flag: $1" >&2; usage ;;
    *) REPOS+=("$1"); shift ;;
  esac
done
[ "${#REPOS[@]}" -ge 1 ] || usage
FAILED=0
FAILED_REPOS=""
for repo in "${REPOS[@]}"; do
  # Every repo is attempted: a failure is recorded and named, and the rest
  # still run. (Condition position disables `set -e` inside install_into,
  # so every fallible command in that tree carries an explicit guard —
  # without one, a failed copy would read as success.)
  if install_into "$repo"; then :; else
    echo "error: install into $repo failed — continuing with remaining repos" >&2
    FAILED=$((FAILED+1))
    FAILED_REPOS="$FAILED_REPOS $repo"
  fi
done
if [ "$FAILED" -gt 0 ]; then
  echo "$FAILED repo(s) failed:$FAILED_REPOS" >&2
  exit 2
fi
if [ "$SKIPPED" -gt 0 ]; then
  echo "$SKIPPED file(s) skipped (differing; re-run with --force to overwrite)" >&2
  exit 1
fi
