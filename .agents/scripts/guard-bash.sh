#!/usr/bin/env bash
# PreToolUse(Bash) guard — enforces two AGENTS.md rules that are mechanically
# detectable and have both already failed as advisory prose.
#
#   1. `--body -` (and `-b -`) on a `gh` command. It sets the body to a literal
#      "-", exits 0, and prints a URL, so it looks like it worked. The rule is
#      `--body-file -`.
#   2. `git push` that would land directly on main/master. The PR gate is
#      bypassed silently on repos whose main is unprotected. An explicit
#      main-targeting refspec denies from ANY branch; a bare push denies
#      only on main/master or when the branch is unresolvable. The branch
#      resolves from -C/--git-dir when present, else the payload cwd.
#
# AGENTS.md is advisory context; a hook is deterministic. Per the official
# guidance: "To block an action regardless of what Claude decides, use a
# PreToolUse hook."
#
# Contract: reads the PreToolUse JSON payload on stdin, writes a
# permissionDecision to stdout, exits 0. Exit 0 + JSON is used rather than
# exit 2 so the reason reaches Claude as structured, actionable text.
#
# FAIL-OPEN BY DESIGN: any internal error exits 0 with no decision, so a broken
# guard never wedges the session. The tradeoff is that a broken guard is silent
# — `.agents/scripts/test-guard-bash.sh` is what catches that.

set -uo pipefail

deny() {
  jq -n --arg r "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $r
    }
  }'
  exit 0
}

payload=$(cat) || exit 0
command -v jq >/dev/null 2>&1 || exit 0

cmd=$(printf '%s' "$payload" | jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
cwd=$(printf '%s' "$payload" | jq -r '.cwd // empty' 2>/dev/null) || exit 0
[ -n "$cmd" ] || exit 0

# Split into shell segments and test each as a COMMAND, not as a substring.
# Substring matching blocks `git commit -m "...why git push is wrong..."`, which
# is precisely the message someone writes while documenting these rules.
# Known limit: a separator inside a quoted string over-splits. That can only
# produce a spurious segment, never miss a real one, and it is rare enough to
# accept rather than write a shell parser here.
segments=$(printf '%s' "$cmd" | tr ';|&\n' '\n\n\n\n')

starts_with() { # <segment> <argv0>
  printf '%s' "$1" | grep -qE "^[[:space:]]*$2([[:space:]]|$)"
}

# Strip shell quoting and escaping so every stage below sees one canonical text.
# `set -- $seg` field-splits text that is ALREADY expanded, so it never performs
# quote removal: `"main"`, `\'main\'`, `$\'main\'`, `$"main"`, `""main""`, `m"ain"`
# and `ma\\in` all reach git as plain `main`. Enumerating those spellings is a
# losing game -- each fix left another one. Deleting the characters outright can
# only make a token look MORE like a protected name, so the worst case is an
# over-block, never a bypass.
normalise() { # <segment>
  local n=${1:-}
  printf '%s' "${n//[\"\'\\$]/}"
}

# Strip command wrappers so the match sees the real argv0. Runs AFTER normalise
# (quote removal) and BEFORE any match: rule 2 already normalises first, and
# the inner re-entry below repeats that order. Wrappers, shells, and the final
# command are all recognised by basename, so /usr/bin/env, /usr/bin/sudo, and
# /bin/sh match their bare forms.
# Single-shot `-c` unwrap: inner segments re-enter stripping but never unwrap
# again. `command` strips as a bare word only — its flags are never consumed,
# so `command -v git` (which never executes) cannot become a match.
# OUT, documented and pinned allow in the test suite: aliases, shell
# functions, $()/backtick invocation, deeper-than-one -c nesting, pre-`-c`
# shell options and glued -c'...', env flags outside -i/-u/assignments, all
# `command` flags, time/timeout/nice/stdbuf, quoted-space assignment values,
# sudo flags outside -E/-n/-H/-u. Rule 1 never normalises, so a quoted `gh`
# argv0 or `--body '-'` still evades it (unchanged behavior, not new scope).
canonicalize() { # <segment> [may_unwrap] — prints the canonical segment
  local seg=${1:-} may_unwrap=${2:-1} tok base out="" first=1
  set -f
  # shellcheck disable=SC2086
  set -- $seg
  set +f
  while [ $# -gt 0 ]; do
    tok=$1
    case "$tok" in
      [A-Za-z_][A-Za-z0-9_]*=*) shift; continue ;;
    esac
    base=${tok##*/}
    case "$base" in
      env)
        shift
        while [ $# -gt 0 ]; do
          case "$1" in
            -i) shift ;;
            -u) shift; [ $# -gt 0 ] && shift ;;
            -u*) shift ;;
            [A-Za-z_][A-Za-z0-9_]*=*) shift ;;
            *) break ;;
          esac
        done
        continue ;;
      command)
        shift; continue ;;
      sudo)
        shift
        while [ $# -gt 0 ]; do
          case "$1" in
            -E|-n|-H) shift ;;
            -u) shift; [ $# -gt 0 ] && shift ;;
            -u*) shift ;;
            *) break ;;
          esac
        done
        continue ;;
      *) break ;;
    esac
  done
  if [ "$may_unwrap" = 1 ] && [ $# -ge 2 ] && [ "$2" = "-c" ]; then
    case "${1##*/}" in
      sh|bash|dash|zsh)
        shift 2
        canonicalize "$*" 0
        return ;;
    esac
  fi
  [ $# -eq 0 ] && { printf ''; return; }
  for tok in "$@"; do
    if [ "$first" = 1 ]; then
      first=0
      case "$tok" in */*) tok=${tok##*/} ;; esac
    fi
    out="${out:+$out }$tok"
  done
  printf '%s' "$out"
}

# Is this segment a `git push`? A REGEX over the raw text got this wrong twice,
# and both times it failed OPEN -- no match meant "not a push", which SKIPS the
# rule rather than denying. `git "push" ...` evaded it on the quotes, and
# `--git-dir <path>` (separate value) evaded it because the scan could not pair
# an option with its value. A token walk decides the subcommand deterministically.
is_git_push() { # <normalised segment>
  local tok skip=0
  set -f
  # shellcheck disable=SC2086
  set -- ${1:-}
  set +f
  [ "${1:-}" = "git" ] || return 1
  shift
  while [ $# -gt 0 ]; do
    tok=$1; shift
    if [ "$skip" -eq 1 ]; then skip=0; continue; fi
    case "$tok" in
      # git's closed set of global options that take a SEPARATE value token.
      -C|-c|--exec-path|--git-dir|--work-tree|--namespace|--super-prefix|--config-env|--attr-source)
        skip=1 ;;
      -*) ;;                 # a boolean global option
      push) return 0 ;;      # the subcommand
      *) return 1 ;;         # some other subcommand -- not our business
    esac
  done
  return 1
}

# Decide on the REFSPEC, not on whatever token happens to follow the remote.
# Two ways the token-after-remote test got it wrong (#105):
#   - a flag may sit between remote and refspec (`git push origin --delete X`),
#     so the test saw `--delete` and denied a push that cannot touch main;
#   - a refspec updates its DESTINATION, the half after the colon, so
#     `git push origin feat/x:main` read as "some other branch" and was ALLOWED.
# Tri-state contract: 0 = explicit main target (deny from ANY branch),
# 1 = safe, 2 = bare push (the caller denies iff on main/master/unknown).
# Splitting explicit from bare is what lets `git push` pass on a feature
# branch while `git push origin main` from the same branch denies (#148).
push_targets_main() { # <segment>
  local seg="${1:-}" tok remote="" nref=0 dst skip=0 past_push=0
  # These push every branch, main included, whatever else is on the line.
  if printf '%s' "$seg" | grep -qE '(^|[[:space:]])(--all|--mirror)([[:space:]]|$)'; then
    return 0
  fi
  set -f            # a refspec may contain a glob character
  # shellcheck disable=SC2086
  set -- $seg
  set +f
  for tok in "$@"; do
    if [ "$past_push" -eq 0 ]; then
      # Before the subcommand: mirror is_git_push's walk, so a global
      # option's VALUE (a directory literally named `push`) is not mistaken
      # for the subcommand.
      if [ "$skip" -eq 1 ]; then skip=0; continue; fi
      case "$tok" in
        -C|-c|--exec-path|--git-dir|--work-tree|--namespace|--super-prefix|--config-env|--attr-source)
          skip=1 ;;
        -*) ;;
        push) past_push=1 ;;
        *) ;;
      esac
      continue
    fi
    case "$tok" in -*) continue ;; esac
    if [ -z "$remote" ]; then remote=$tok; continue; fi
    # Every non-flag token after the remote counts as a candidate refspec. A
    # flag's VALUE landing here can only over-block, never let a real main
    # through -- `git push origin -o ci.skip main` still sees `main`.
    nref=$((nref+1))
    # Normalise the destination before matching it. A fully-qualified ref
    # (`feat/x:refs/heads/main`) and `@` (an alias for HEAD) both land on main
    # and both read as "some other branch" unless spelled out here. An empty
    # destination is malformed rather than safe, so it denies too.
    dst=${tok##*:}
    dst=${dst#+}
    dst=${dst#refs/heads/}
    case "$dst" in ""|HEAD|@|main|master) return 0 ;; esac
  done
  if [ "$past_push" -eq 0 ]; then return 0; fi # unreachable; fail closed
  # No remote at all, or a remote with no refspec: bare-push semantics, which
  # send the current branch. The caller classifies these by branch. An
  # explicitly-named non-main refspec on an unrecognised remote (e.g. a fork)
  # cannot land on main, so it passes (#130); main-targeting refspecs already
  # returned above, on every remote.
  # `--tags` with no refspec is the exception -- it updates tags only, never
  # a branch ref, so it is safe from anywhere on any remote (#148/C4).
  if [ -z "$remote" ] || [ "$nref" -eq 0 ]; then
    if [ "$nref" -eq 0 ] \
       && printf '%s' "$seg" | grep -qE '(^|[[:space:]])--tags([[:space:]]|$)'; then
      return 1
    fi
    return 2
  fi
  return 1
}

# Which branch does this push run against? -C/--git-dir in the command win
# over the payload cwd: `git -C ~/other push ...` from /tmp must read the
# target repo's branch, not "" (which the old cwd-only lookup produced, and
# which used to mean "allow"). Prints the branch or nothing.
resolve_push_branch() { # <canonical segment> <cwd>
  local seg="$1" cwd="$2" tok skip=0 prev="" dir="" gitdir=""
  set -f
  # shellcheck disable=SC2086
  set -- $seg
  set +f
  shift # argv0 is git (the caller verified the subcommand already)
  while [ $# -gt 0 ]; do
    tok=$1; shift
    if [ "$skip" -eq 1 ]; then
      case "$prev" in -C) dir=$tok ;; --git-dir) gitdir=$tok ;; esac
      skip=0; continue
    fi
    case "$tok" in
      -C|--git-dir) prev=$tok; skip=1 ;;
      --git-dir=*) gitdir=${tok#--git-dir=} ;;
      -c|--exec-path|--work-tree|--namespace|--super-prefix|--config-env|--attr-source)
        prev=""; skip=1 ;;
      -*) ;;
      *) break ;; # subcommand: nothing past it changes which repo this is
    esac
  done
  if [ -n "$dir" ] && [ -d "$dir" ]; then
    git -C "$dir" branch --show-current 2>/dev/null || true
  elif [ -n "$gitdir" ]; then
    git --git-dir "$gitdir" branch --show-current 2>/dev/null || true
  elif [ -n "$cwd" ] && [ -d "$cwd" ]; then
    git -C "$cwd" branch --show-current 2>/dev/null || true
  fi
}

# ---------------------------------------------------------------- rule 1: --body -
# Scoped to `gh` so other tools that legitimately take `--body -` are unaffected.
# `--body-file -` must NOT match: after `--body` comes `-file`, not whitespace.
while IFS= read -r seg; do
  seg=$(canonicalize "$seg")
  starts_with "$seg" "gh" || continue
  if printf '%s' "$seg" | grep -qE '(^|[[:space:]])(--body|-b)([[:space:]]+|=)-([[:space:]]|$)'; then
    deny "Blocked: \`--body -\` sets the body to a literal \"-\", exits 0, and prints a URL — it looks like it worked.

Use \`--body-file -\` and pipe the body in via a heredoc:

  gh issue create --title '...' --body-file - <<'EOF'
  ...body...
  EOF

Then prove it published:
  gh issue view N --json body --jq '.body|length'   # non-zero is the only proof

(AGENTS.md § Repository state and task tracking)"
  fi
done <<EOF
$segments
EOF

# ------------------------------------------------------- rule 2: push onto main
while IFS= read -r seg; do
  seg=$(normalise "$seg")
  seg=$(canonicalize "$seg")
  starts_with "$seg" "git" || continue
  is_git_push "$seg" || continue
  # --dry-run changes nothing on the remote.
  if ! printf '%s' "$seg" | grep -qE '(^|[[:space:]])(--dry-run|-n)([[:space:]]|$)'; then
    branch=$(resolve_push_branch "$seg" "$cwd")
    push_targets_main "$seg"; rc=$?
    if [ "$rc" -eq 0 ]; then
      # An explicit main-targeting refspec denies from ANY branch: pushing
      # main from a feature branch lands on main just the same (#148).
      deny "Blocked: this push names main (or equivalent) as its destination
and would land directly on it, bypassing the PR gate.

Several repos here have an unprotected main, so nothing else would stop it.

Push the working branch by name instead:

  git push -u origin ${branch:-<branch>}

If you genuinely intend to write to main, do it deliberately — not through a
command whose refspec happens to name it.

(AGENTS.md § Git and merge authority)"
    elif [ "$rc" -eq 2 ]; then
      case "$branch" in
        main|master)
          # A bare push sends the current branch, so on main/master it lands
          # on main. From a feature branch it is safe and passes.
          deny "Blocked: you are on \`$branch\` and this push would land directly on it, bypassing the PR gate.

Several repos here have an unprotected main, so nothing else would stop it.

Branch first, then push the branch:

  git checkout -b <type>/<slug>
  git push -u origin <type>/<slug>

If you genuinely intend to write to $branch, do it deliberately — not through a
command whose default target happens to be $branch.

(AGENTS.md § Git and merge authority; memory: branch-first)"
          ;;
        "")
          deny "Blocked: the branch could not be resolved (detached HEAD or an
unresolvable directory), so this bare push cannot prove it avoids main.

Resolve the branch first, or name the refspec explicitly:

  git push origin <branch>

(AGENTS.md § Git and merge authority)"
          ;;
      esac
    fi
  fi
done <<EOF
$segments
EOF

exit 0
