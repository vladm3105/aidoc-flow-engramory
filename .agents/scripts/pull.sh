#!/usr/bin/env bash
# Pull the canon .agents/ PROJECT scope into a consumer repo.
# The consumer runs this FROM A CANON CHECKOUT; the canon never pushes.
#
# What gets pulled, and what deliberately does NOT:
#
#   YES — rules/ (additive by paths-match; the project pins the canon bytes),
#         scripts/check-skill-shadowing.sh (the detector: per-repo acceptance),
#         GOVERNANCE.md (travels with the canon), and the verified-planning
#         gate (delegated to that skill's install.sh — one code path, not two).
#
#   NO  — canon skills/agents. They are user-scope components: a same-name
#         project skill would be masked dead weight (install.sh already
#         deletes those), and a same-name project agent without a marker
#         would be an undeclared collision. Print a global-install pointer
#         instead; project scope carries only -local adaptations and
#         OVERRIDES-marked agents, which this script never touches.
#
#   NO  — pull.sh itself (a consumer running its own stale copy is the
#         re-infection path install.sh documents), the canon-dev harness
#         (check-config.sh, guard-bash.sh, check-frontmatter.py, test-*.sh),
#         and anything outside .agents/ (install.sh owns plan-gate-reusable.yml).
#
# Ownership is file-level per GOVERNANCE.md: overwrite only canon-owned
# filenames; never touch *-local, OVERRIDES-marked agents, unmarked
# same-name agents (flagged as defects), project skill dirs, or filenames
# canon does not ship. This script NEVER deletes (other than a stale
# pull.sh copy it self-heals) and NEVER auto-edits adaptations: reconcile
# items are reported for the human.
#
# Pull-state memory: every pull records the canon SHA it pulled in
# `<repo>/.agents/.canon-sha` (project-owned, so it survives). The next
# pull diffs the canon range for deletions and reports canon-side removals
# still present in the project as review advice — never deletes them.
# First pulls, unknown SHAs, and non-git canon checkouts degrade to a note.
#
# `--global` refreshes USER scope instead: skills/ + agents/ under
# `${AGENTS_CONFIG_DIR:-$HOME/.agents}` (full skill dirs — SKILL.md lives
# here — except install.sh, which is never shipped and self-heals like the
# repo rule). Same skip-unless-forced semantics; user-added filenames are
# never touched, and there is no `-local` concept at user scope (the user
# is already the top scope). No detector run (nothing project to scan) and
# no state file. `--force` here refreshes LIVE sessions' config and says so
# loudly. The user root must never resolve into the canon checkout.
#
# Semantics mirror install.sh: a destination file that exists and differs
# is SKIPPED with a warning, and the run exits 1 at the end when anything
# needs human attention (skips, defects, gate-installer skips, detector
# findings). Pass --force to refresh differing canon-owned files, --backup
# DIR to keep copies of what --force replaces, --dry-run to print what
# would happen without changing anything.
#
# Usage: pull.sh [--dry-run] [--force] [--backup DIR] [--] <repo-dir> [<repo-dir> ...]
#        pull.sh --global [--dry-run] [--force] [--backup DIR]
set -euo pipefail
shopt -s nullglob

# The master .agents dir. PULL_SRC overrides it — a test seam first (the
# removal report needs deletion history the live canon may not have) and
# an advanced escape hatch second. Normal runs leave it unset.
SRC="${PULL_SRC:-$(cd "$(dirname "$0")/.." && pwd)}"
SELF="$(cd "$(dirname "$0")" && pwd)/pull.sh"   # the running script, for staleness comparison
# A redirected canon source must never look like a clean pull: PULL_SRC is a
# test seam and an advanced escape hatch, and every run honoring it says so.
if [ -n "${PULL_SRC:-}" ]; then
  echo "WARNING: using PULL_SRC=$PULL_SRC (test seam / escape hatch — output reflects that tree, not the canon checkout)" >&2
fi
DRY_RUN=0
FORCE=0
GLOBAL=0
NO_CI=0
BACKUP_DIR=""
SKIPPED=0
DEFECTS=0
INSTALL_ATTENTION=0
DETECTOR_ATTENTION=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
CANON_ROOT="$(cd "$SRC/.." 2>/dev/null && pwd || true)"
CANON_SHA=""
CANON_TAG=""

usage() {
  echo "usage: pull.sh [--dry-run] [--force] [--no-ci] [--backup DIR] [--] <repo-dir>..." >&2
  echo "       pull.sh --global [--dry-run] [--force] [--backup DIR]  (user scope)" >&2
  exit 2
}

pull_file() { # $1 = src, $2 = dst, $3 = repo — never silently overwrites
  local src="$1" dst="$2" repo="$3" rel ns
  if [ ! -e "$dst" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then echo "would install: $dst"; return 0; fi
    mkdir -p "$(dirname "$dst")" || return 2
    # A failed copy must fail the pull, not print success over missing bytes.
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

backup_delete() { # $1 = file, $2 = root — stage the doomed file in BACKUP_DIR when set, then remove
  # Self-heal deletions are the one removal class; without this a --backup run
  # promises reversibility it does not deliver. Layout mirrors pull_file.
  local file="$1" root="$2" rel ns
  if [ -n "$BACKUP_DIR" ]; then
    rel=${file#"$root"/}
    ns=$(basename "$root")
    mkdir -p "$BACKUP_DIR/$ns/$(dirname "$rel")" || return 2
    cp "$file" "$BACKUP_DIR/$ns/$rel" || return 2
  fi
  rm -f "$file" || return 2
}

agent_name() { # $1 = agent file -> frontmatter name, or basename fallback
  local nm
  # Guard the read: an unmatched glob hands us a literal pattern, and awk on
  # a missing file must fall through to basename — never abort the caller.
  if [ -f "$1" ]; then
    nm=$(awk 'BEGIN{n=0} /^---$/{n++; if(n==2) exit; next} n==1 && /^name:/{sub(/^name:[[:space:]]*/,""); print; exit}' "$1" | tr -d "\"'")
  fi
  [ -n "$nm" ] || nm=$(basename "$1" .md)
  printf '%s' "$nm"
}

canon_agent_names() { # prints one canon agent name per line
  local f
  for f in "$SRC"/agents/*.md; do agent_name "$f"; printf '\n'; done
}

has_marker() { # $1 = agent file — same pattern the detector uses
  grep -Eq '^> \*\*OVERRIDES GLOBAL\*\*([[:space:]]|—|$)' "$1"
}

strip_decl() { # $1 = file -> bytes minus the 3 declaration lines (never match canon)
  # `|| true`: a declaration-only file yields no output (grep exit 1), and a
  # helper must not leak that failure to callers — same guard as changed_lines.
  grep -vE '^> \*\*(OVERRIDES GLOBAL|Why:|Reconciled:)\*\*' "$1" || true
}

changed_lines() { # $1 $2 = files -> count of differing lines (portable diff)
  diff "$1" "$2" 2>/dev/null | grep -c '^[<>]' || true
}

reconcile_agent() { # $1 = project file, $2 = base name, $3 = kind (override|local)
  local proj="$1" base="$2" kind="$3" namesake n buffering
  namesake="$SRC/agents/$base.md"
  if [ ! -f "$namesake" ]; then
    echo "kept adaptation: $proj (namesake gone from canon — stands alone)"
    return 0
  fi
  # The declaration block can never match canon (canon carries no markers),
  # so compare minus it: override == canon + declaration means droppable.
  strip_decl "$proj" > "$TMP/proj.stripped"
  if cmp -s "$TMP/proj.stripped" "$namesake"; then
    if [ "$kind" = "override" ]; then buffering="consider dropping"; else buffering="consider retiring"; fi
    echo "kept adaptation: $proj (identical — $buffering)"
    return 0
  fi
  n=$(changed_lines "$namesake" "$TMP/proj.stripped")
  echo "kept adaptation: $proj (differs: $n lines; diff $namesake $proj)"
}

reconcile_skill_dir() { # $1 = project -local dir, $2 = base name
  local proj="$1" base="$2" namesake n
  namesake="$SRC/skills/$base"
  if [ ! -d "$namesake" ]; then
    echo "kept adaptation: $proj (namesake gone from canon — stands alone)"
    return 0
  fi
  n=$(diff -rq "$namesake" "$proj" 2>/dev/null | wc -l) || true
  if [ "$n" -eq 0 ]; then
    echo "kept adaptation: $proj (identical — consider retiring)"
    return 0
  fi
  echo "kept adaptation: $proj (differs: $n items; diff -r $namesake $proj)"
}

pull_into() {  # $1 = repo dir
  local repo="$1" dst="$1/.agents" f rel skilldir base name names
  dst="$repo/.agents"
  # Early refusals RETURN (not exit) so a multi-repo run attempts the rest;
  # the driver collects failures and exits 2 at the end.
  if [ ! -d "$repo" ]; then echo "error: not a directory: $repo" >&2; return 2; fi
  if ! git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    echo "error: not a git repository: $repo (manual fallback: GOVERNANCE.md §Pull process)" >&2; return 2
  fi
  if [ -e "$dst" ] && [ ! -d "$dst" ]; then
    echo "error: $dst exists and is not a directory" >&2; return 2
  fi
  # guard: never pull the master onto itself (a repo running its own copy).
  # Contents would compare identical file-by-file, but the self-heal below
  # would eat the master's own pull.sh — so refuse before ANY action.
  if [ -e "$dst" ] && [ "$dst" -ef "$SRC" ]; then
    echo "error: $repo is the canon checkout itself" >&2; return 2
  fi
  if [ "$DRY_RUN" -eq 0 ]; then
    mkdir -p "$dst/skills" "$dst/agents" "$dst/rules" "$dst/scripts" || return 2
  fi
  # 1. canon-owned project-scope files
  pull_file "$SRC/GOVERNANCE.md" "$dst/GOVERNANCE.md" "$repo" || return 2
  if [ -d "$SRC/rules" ]; then
    # find runs in a subshell (counter survival), so re-root its relative
    # hits onto SRC before copying — never cp a bare ./-path.
    while IFS= read -r -d '' t; do
      rel=${t#./}
      pull_file "$SRC/rules/$rel" "$dst/rules/$rel" "$repo" || return 2
    done < <(cd "$SRC/rules" && find . \( -type d \( -name __pycache__ -o -name .pytest_cache \) -prune \) -o \( -type f ! -name '*.pyc' -print0 \))
  fi
  pull_file "$SRC/scripts/check-skill-shadowing.sh" \
    "$dst/scripts/check-skill-shadowing.sh" "$repo" || return 2
  # 2. self-heal: a stale pull.sh copy is the re-infection path — but only a
  # byte-identical copy is provably stale. Anything else at this canon-owned
  # filename is kept and flagged like any other skipped difference.
  if [ -f "$dst/scripts/pull.sh" ]; then
    if [ -f "$SELF" ] && cmp -s "$SELF" "$dst/scripts/pull.sh"; then
      if [ "$DRY_RUN" -eq 1 ]; then echo "would remove stale $dst/scripts/pull.sh"
      else backup_delete "$dst/scripts/pull.sh" "$repo" || return 2
        echo "removed $dst/scripts/pull.sh — run pull from a canon checkout"
      fi
    elif [ "$DRY_RUN" -eq 1 ]; then
      echo "would keep foreign file at canon-owned path: $dst/scripts/pull.sh"
    else
      echo "SKIP foreign file at canon-owned path (kept; remove it yourself): $dst/scripts/pull.sh"
      SKIPPED=$((SKIPPED+1))
    fi
  fi
  # 3. the gate belongs to install.sh — delegate, do not duplicate
  if [ -x "$SRC/skills/verified-planning/install.sh" ]; then
    local flags=()
    if [ "$DRY_RUN" -eq 1 ]; then flags+=(--dry-run); fi
    if [ "$FORCE" -eq 1 ]; then flags+=(--force); fi
    if [ "$NO_CI" -eq 1 ]; then flags+=(--no-ci); fi
    if [ -n "$BACKUP_DIR" ]; then flags+=(--backup "$BACKUP_DIR"); fi
    set +e
    # Guarded expansion: a bare "${flags[@]}" under `set -u` aborts on bash
    # < 4.4 when no flags were set (stock macOS bash is 3.2).
    bash "$SRC/skills/verified-planning/install.sh" "${flags[@]+"${flags[@]}"}" "$repo"
    local irc=$?
    set -e
    if [ "$irc" -eq 1 ]; then INSTALL_ATTENTION=1
    elif [ "$irc" -ne 0 ]; then return 2
    fi
  fi
  # 4. project-side classification: adaptations kept + reported, never touched
  if [ -d "$dst/skills" ]; then
    for skilldir in "$dst"/skills/*/; do
      base=$(basename "$skilldir")
      case "$base" in
        *-local) reconcile_skill_dir "$dst/skills/$base" "${base%-local}" ;;
        *) : ;; # same-name project skill dirs: the detector owns that verdict
      esac
    done
  fi
  if [ -d "$dst/agents" ]; then
    names=$(canon_agent_names)
    for f in "$dst"/agents/*.md; do
      name=$(agent_name "$f")
      case "$name" in
        *-local)
          base=${name%-local}
          reconcile_agent "$f" "$base" local ;;
        *)
          if printf '%s\n' "$names" | grep -qxF "$name"; then
            if has_marker "$f"; then
              reconcile_agent "$f" "$name" override
            else
              echo "DEFECT: undeclared same-name agent: $f (name '$name') — add an OVERRIDES GLOBAL declaration or remove it"
              DEFECTS=$((DEFECTS+1))
            fi
          else
            : # foreign name: project-owned, untouched and silent
          fi ;;
      esac
    done
  fi
  # 5. removal advice from pull-state memory (read-only; never deletes)
  report_removals "$repo" "$dst"
  # 6. acceptance: the canon detector against the target (read-only either way)
  set +e
  bash "$SRC/scripts/check-skill-shadowing.sh" "$repo" 2>&1
  local drc=$?
  set -e
  if [ "$drc" -eq 0 ]; then echo "detector: clean"
  elif [ "$drc" -eq 1 ]; then
    echo "detector: findings (exit 1) — resolve or consciously accept"
    DETECTOR_ATTENTION=1
  else
    echo "detector: incomplete scan (exit $drc)"
    DETECTOR_ATTENTION=1
  fi
  # 7. the rest of the update lives in user scope, not here
  echo "note: canon skills/agents are user-scope components — this pull does not copy them."
  echo "  refresh them with: $0 --global (see README §Consuming the canon)"
  if [ -n "$CANON_SHA" ]; then echo "canon HEAD: $CANON_SHA (confirm CI green on it before relying on this pull)"; fi
  # 8. record what was pulled (project-owned state; a dry run writes nothing)
  if [ "$DRY_RUN" -eq 0 ] && [ -n "$CANON_SHA" ]; then
    { echo "$CANON_SHA"; echo "TAG $CANON_TAG"; } > "$dst/.canon-sha" || return 2
  fi
  echo "pulled into $repo"
}

pull_global() {
  # Neither root set (minimal container with env -i): say what to set
  # instead of dying on "HOME: unbound variable" inside the expansion.
  if [ -z "${AGENTS_CONFIG_DIR:-}" ] && [ -z "${HOME:-}" ]; then
    echo "error: cannot resolve user root — set AGENTS_CONFIG_DIR or HOME" >&2
    return 2
  fi
  local root="${AGENTS_CONFIG_DIR:-$HOME/.agents}"
  root=${root%/}
  if [ -z "$root" ]; then echo "error: empty user root" >&2; exit 2; fi
  echo "user root: $root"
  # Refuse any root resolving into the canon checkout: installing the master
  # into itself (or scattering user files across the repo) is never what
  # --global means. The string match catches absolute nests; -ef catches
  # relative and symlinked forms of the same mistake.
  case "$root" in
    "$CANON_ROOT"|"$CANON_ROOT"/*)
      echo "error: user root is inside the canon checkout: $root" >&2; exit 2 ;;
  esac
  if [ -e "$root" ] && [ "$root" -ef "$CANON_ROOT" ]; then
    echo "error: user root is the canon checkout itself" >&2; exit 2
  fi
  if [ -e "$root/skills" ] && [ "$root/skills" -ef "$SRC/skills" ]; then
    echo "error: user root is the canon .agents dir itself" >&2; exit 2
  fi
  if [ "$FORCE" -eq 1 ]; then
    echo "WARNING: --force refreshes the LIVE user config at $root — running sessions may behave differently mid-run" >&2
  fi
  if [ "$DRY_RUN" -eq 0 ]; then
    mkdir -p "$root/skills" "$root/agents" || return 2
  fi
  local t rel
  while IFS= read -r -d '' t; do
    rel=${t#./}
    case "$rel" in
      verified-planning/install.sh) : ;;   # never ship the installer
      *) pull_file "$SRC/skills/$rel" "$root/skills/$rel" "$root" || return 2 ;;
    esac
  done < <(cd "$SRC/skills" && find . \( -type d \( -name __pycache__ -o -name .pytest_cache \) -prune \) -o \( -type f ! -name '*.pyc' -print0 \))
  while IFS= read -r -d '' t; do
    rel=${t#./}
    pull_file "$SRC/agents/$rel" "$root/agents/$rel" "$root" || return 2
  done < <(cd "$SRC/agents" && find . \( -type d \( -name __pycache__ -o -name .pytest_cache \) -prune \) -o \( -type f ! -name '*.pyc' -print0 \))
  # Self-heal mirrors the repo rule: only a byte-identical installer copy
  # is provably stale. Anything else at this canon-owned filename is kept
  # and flagged like any other skipped difference.
  if [ -f "$root/skills/verified-planning/install.sh" ]; then
    if cmp -s "$SRC/skills/verified-planning/install.sh" "$root/skills/verified-planning/install.sh"; then
      if [ "$DRY_RUN" -eq 1 ]; then echo "would remove stale $root/skills/verified-planning/install.sh"
      else backup_delete "$root/skills/verified-planning/install.sh" "$root" || return 2
        echo "removed $root/skills/verified-planning/install.sh — run install from a canon checkout"
      fi
    elif [ "$DRY_RUN" -eq 1 ]; then
      echo "would keep foreign file at canon-owned path: $root/skills/verified-planning/install.sh"
    else
      echo "SKIP foreign file at canon-owned path (kept; remove it yourself): $root/skills/verified-planning/install.sh"
      SKIPPED=$((SKIPPED+1))
    fi
  fi
  echo "pulled user scope into $root"
}

report_removals() { # $1 = repo, $2 = dst — advice only, never deletes
  local repo="$1" dst="$2" state old p
  [ -n "$CANON_SHA" ] || return 0   # non-git canon checkout: nothing to diff
  state="$dst/.canon-sha"
  if [ ! -f "$state" ]; then
    echo "note: no previous pull recorded — skipping removal report"
    return 0
  fi
  old=$(sed -n '1p' "$state")
  # The writer promises full SHAs; anything else (including option-shaped
  # text) must never reach git rev parsing.
  case "$old" in
    *[!0-9a-f]*|"")
      echo "note: cannot determine removals (unrecognized last-pulled state)"
      return 0 ;;
  esac
  if [ "${#old}" -ne 40 ]; then
    echo "note: cannot determine removals (unrecognized last-pulled state)"
    return 0
  fi
  if [ "$old" = "$CANON_SHA" ]; then
    echo "note: canon unchanged since last pull ($old)"
    return 0
  fi
  if ! git -C "$CANON_ROOT" merge-base --is-ancestor "$old" HEAD 2>/dev/null; then
    echo "note: cannot determine removals (last-pulled $old not in this checkout's history)"
    return 0
  fi
  # Only shipped paths count: a canon-deleted skill was never in the project
  # (user-scope), so reporting it would cry wolf on every cleanup.
  git -C "$CANON_ROOT" log --diff-filter=D --name-only --pretty=format: \
    "$old..HEAD" -- .agents 2>/dev/null | while IFS= read -r p; do
    [ -n "$p" ] || continue
    case "$p" in
      # The gate ships in two places: the inert .agents copy and the live
      # workflow. Advice naming only the former lets the live workflow keep
      # gating on deleted logic, unmentioned. Both spellings stay mapped:
      # the old master name for consumers mid-migration, the reusable name
      # going forward.
      .agents/skills/verified-planning/plan-gate-reusable.yml)
        if [ -e "$repo/$p" ]; then echo "removed from canon (still present — review, not deleted): $repo/$p"; fi
        if [ -e "$repo/.github/workflows/plan-gate-reusable.yml" ]; then echo "removed from canon (live workflow still present — review, not deleted): $repo/.github/workflows/plan-gate-reusable.yml"; fi ;;
      .agents/skills/verified-planning/plan-gate.yml)
        if [ -e "$repo/$p" ]; then echo "removed from canon (still present — review, not deleted): $repo/$p"; fi
        if [ -e "$repo/.github/workflows/plan-gate.yml" ]; then echo "removed from canon (live workflow still present — review, not deleted): $repo/.github/workflows/plan-gate.yml"; fi ;;
      .agents/rules/*|.agents/scripts/check-skill-shadowing.sh|.agents/GOVERNANCE.md|.agents/skills/verified-planning/*)
        if [ -e "$repo/$p" ]; then echo "removed from canon (still present — review, not deleted): $repo/$p"; fi ;;
      *) : ;;
    esac
  done || true   # advice-only: a git fault here must never fail the pull
}

REPOS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --force) FORCE=1; shift ;;
    --global) GLOBAL=1; shift ;;
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
[ "${#REPOS[@]}" -ge 1 ] || [ "$GLOBAL" -eq 1 ] || usage
# Resolve the pulled canon version once: full SHA for the state file (short
# SHAs are ambiguous across clones), tag name when HEAD is exactly one so a
# human can see pinned-tag vs floating-checkout at a glance. The removal
# logic needs no tag/floating distinction — SHAs cover both.
if [ -n "$CANON_ROOT" ]; then
  CANON_SHA=$(git -C "$CANON_ROOT" rev-parse HEAD 2>/dev/null || true)
  if [ -n "$CANON_SHA" ]; then
    CANON_TAG=$(git -C "$CANON_ROOT" describe --exact-match --tags HEAD 2>/dev/null || echo "(floating)")
  fi
fi
FAILED=0
FAILED_REPOS=""
if [ "$GLOBAL" -eq 1 ]; then
  [ "${#REPOS[@]}" -eq 0 ] || { echo "error: --global takes no repo arguments" >&2; exit 2; }
  pull_global
else
  # Every repo is attempted: a failure is recorded and named, and the rest
  # still run. (Condition position disables `set -e` inside pull_into,
  # so every fallible command in that tree carries an explicit guard —
  # without one, a failed copy would read as success.)
  for repo in "${REPOS[@]}"; do
    if pull_into "$repo"; then :; else
      echo "error: pull into $repo failed — continuing with remaining repos" >&2
      FAILED=$((FAILED+1))
      FAILED_REPOS="$FAILED_REPOS $repo"
    fi
  done
fi
if [ "$FAILED" -gt 0 ]; then
  echo "$FAILED repo(s) failed:$FAILED_REPOS" >&2
  exit 2
fi
if [ "$SKIPPED" -gt 0 ] || [ "$DEFECTS" -gt 0 ] \
   || [ "$INSTALL_ATTENTION" -eq 1 ] || [ "$DETECTOR_ATTENTION" -eq 1 ]; then
  echo "$SKIPPED file(s) skipped, $DEFECTS defect(s) flagged" \
    "(gate-installer attention: $INSTALL_ATTENTION, detector attention: $DETECTOR_ATTENTION)" >&2
  echo "re-run with --force to refresh differing canon-owned files; resolve or accept the rest" >&2
  exit 1
fi
