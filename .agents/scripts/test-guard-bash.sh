#!/usr/bin/env bash
# Mutation test for guard-bash.sh — checks BOTH directions.
#
# A guard tested only on what it should block is half-tested: the expensive
# failure is the false positive that blocks legitimate work, because it presents
# as the tool being broken rather than as the guard being wrong.
#
# Usage: .agents/scripts/test-guard-bash.sh    (exit 0 = all pass)

set -uo pipefail

GUARD="$(cd "$(dirname "$0")" && pwd)/guard-bash.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Two throwaway repos: one parked on main, one on a feature branch.
for r in on-main on-branch; do
  git init -q "$TMP/$r"
  git -C "$TMP/$r" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
  git -C "$TMP/$r" branch -M main
done
git -C "$TMP/on-branch" checkout -q -b feat/thing
# Nested repo for cumulative -C: $TMP/nest/inner parked on main (#268).
mkdir -p "$TMP/nest"
git init -q "$TMP/nest/inner"
git -C "$TMP/nest/inner" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
git -C "$TMP/nest/inner" branch -M main

pass=0; fail=0

check() { # <expect: deny|allow> <cwd> <command>
  local expect="$1" cwd="$2" cmd="$3" out decision
  out=$(jq -n --arg c "$cmd" --arg d "$cwd" \
        '{hook_event_name:"PreToolUse",tool_name:"Bash",cwd:$d,tool_input:{command:$c}}' \
        | "$GUARD" 2>/dev/null)
  if [ -z "$out" ]; then decision="allow"
  else decision=$(printf '%s' "$out" | jq -r '.hookSpecificOutput.permissionDecision // "allow"' 2>/dev/null || echo allow)
  fi
  if [ "$decision" = "$expect" ]; then
    pass=$((pass+1)); printf '  ok   %-6s %s\n' "$expect" "$cmd"
  else
    fail=$((fail+1)); printf '  FAIL want=%s got=%s  %s\n' "$expect" "$decision" "$cmd"
  fi
}

echo "== rule 1: gh --body -  (must block) =="
check deny "$TMP/on-branch" "gh issue create --title x --body -"
check deny "$TMP/on-branch" "gh pr create --body -"
check deny "$TMP/on-branch" "gh issue comment 5 -b -"
check deny "$TMP/on-branch" "gh issue create --body=-"
check deny "$TMP/on-branch" "cd /tmp && gh issue create --body -"

echo "== rule 1: must NOT block (false-positive guard) =="
check allow "$TMP/on-branch" "gh issue create --title x --body-file -"
check allow "$TMP/on-branch" "gh issue create --body-file - <<'EOF'"
check allow "$TMP/on-branch" "gh issue create --body 'real text'"
check allow "$TMP/on-branch" "gh issue view 5 --json body --jq '.body|length'"
check allow "$TMP/on-branch" "gh pr list --state all"
check allow "$TMP/on-branch" "echo '--body -'"
check allow "$TMP/on-branch" "some-tool --body - --flag"

echo "== rule 2: push onto main (must block) =="
check deny "$TMP/on-main" "git push"
check deny "$TMP/on-main" "git push origin main"
check deny "$TMP/on-main" "git push origin HEAD"
check deny "$TMP/on-main" "git push -u origin main"
check deny "$TMP/on-main" "git push --force origin main"
check deny "$TMP/on-main" "gh pr list && git push"
# A flag between remote and refspec must not become an escape hatch, and the
# whole-repo forms push main whatever else is on the line.
check deny "$TMP/on-main" "git push origin --delete main"
check deny "$TMP/on-main" "git push origin -d master"
check deny "$TMP/on-main" "git push origin --all"
check deny "$TMP/on-main" "git push origin --mirror"
check deny "$TMP/on-main" "git push origin --tags main"
check deny "$TMP/on-main" "git push origin --force-with-lease main"
# A refspec updates its DESTINATION, which is the half after the colon.
check deny "$TMP/on-main" "git push origin feat/thing:main"
check deny "$TMP/on-main" "git push origin HEAD:master"
check deny "$TMP/on-main" "git push origin :main"
# A flag VALUE must not be mistaken for the refspec and let the real one pass.
check deny "$TMP/on-main" "git push origin -o ci.skip main"
# An unrecognised remote (e.g. a fork) with an explicitly-named refspec is
# judged on the refspec alone (#130): non-main destinations cannot land on
# main, so they pass; main-targeting and bare pushes still deny.
check allow "$TMP/on-main" "git push myremote feat/thing"
check allow "$TMP/on-main" "git push myremote --delete feat/thing"
check allow "$TMP/on-main" "git push fork/upstream feat/thing:feat/other"
check deny "$TMP/on-main" "git push myremote main"
check deny "$TMP/on-main" "git push myremote master"
check deny "$TMP/on-main" "git push myremote HEAD"
check deny "$TMP/on-main" "git push myremote feat/thing:main"
check deny "$TMP/on-main" "git push myremote --delete main"
check deny "$TMP/on-main" "git push myremote"
# A named remote with NO refspec is still a bare push: with push.default=simple
# it sends the current branch. This is the only state where the two guards in
# push_targets_main's fall-through disagree, so it is the one that pins the OR.
check deny "$TMP/on-main" "git push origin"
check deny "$TMP/on-main" "git push upstream"
check deny "$TMP/on-main" "git push -u origin"
# Quoting a branch name is ordinary defensive shell, not an exotic evasion.
# The tokens arrive field-split but NOT quote-removed.
check deny "$TMP/on-main" 'git push origin "main"'
check deny "$TMP/on-main" "git push origin 'master'"
check deny "$TMP/on-main" 'git push origin "feat/thing:main"'
check deny "$TMP/on-main" 'git push "origin" main'
# Adjacent quoted runs concatenate, so this reaches git as plain `main`; a
# strip of one balanced pair leaves `"main"` and lets it through.
check deny "$TMP/on-main" 'git push origin ""main""'
check deny "$TMP/on-main" 'git push origin "ma"in'
# Every one of these reaches git as plain `main`. They are here as a set
# because fixing them one spelling at a time is what went wrong first.
check deny "$TMP/on-main" "git push origin \$'main'"
check deny "$TMP/on-main" 'git push origin $"main"'
check deny "$TMP/on-main" 'git push origin ma\in'
check deny "$TMP/on-main" "git push origin 'ma'\"in\""
# A global option whose value is a separate token must not hide the push from
# the pre-filter -- when it does, rule 2 is skipped rather than denied.
check deny "$TMP/on-main" "git -c user.name=x push origin main"
check deny "$TMP/on-main" "git -C /tmp/repo push origin main"
check deny "$TMP/on-main" "git --git-dir=/tmp/r/.git push origin main"
check deny "$TMP/on-main" "git -c a=b -C /tmp/r push"
# --git-dir takes its value either glued or as a separate token; only the glued
# form was covered, and the separate form skipped rule 2 entirely.
check deny "$TMP/on-main" "git --git-dir /tmp/r/.git push origin main"
check deny "$TMP/on-main" "git --namespace ns push origin main"
check deny "$TMP/on-main" "git --config-env n=E push origin main"
# Quoting the SUBCOMMAND is byte-identical to git, but a raw-text scan sees the
# quote where it demands whitespace and skips the rule.
check deny "$TMP/on-main" 'git "push" origin main'
check deny "$TMP/on-main" "git 'push' origin main"
check deny "$TMP/on-main" 'git push origin m"ain"'
# A fully-qualified destination reaches main just as well as the short name.
check deny "$TMP/on-main" "git push origin feat/thing:refs/heads/main"
check deny "$TMP/on-main" "git push origin HEAD:refs/heads/main"
check deny "$TMP/on-main" "git push origin refs/heads/x:refs/heads/master"
check deny "$TMP/on-main" "git push origin refs/heads/main"
# `@` is an alias for HEAD; `+` forces; an empty destination is malformed.
check deny "$TMP/on-main" "git push origin @"
check deny "$TMP/on-main" "git push origin +main"
check deny "$TMP/on-main" "git push origin feat/thing:"
# ...and the qualified form of a SAFE branch must still pass.
check allow "$TMP/on-main" "git push origin feat/thing:refs/heads/feat/thing"
check allow "$TMP/on-main" "git push origin refs/heads/feat/thing"
check allow "$TMP/on-main" 'git push origin "feat/thing"'
check allow "$TMP/on-main" "git -C /tmp/repo push origin feat/thing"
# A global option consumes its value, so a directory literally named `push` is
# not a subcommand -- this must stay ordinary work, not a deny.
check allow "$TMP/on-main" "git -C push status"
check allow "$TMP/on-main" "git --git-dir /tmp/r/.git push origin feat/thing"
check allow "$TMP/on-main" "git -c a=b status"

echo "== wrappers and -c unwrap (must block) =="
check deny "$TMP/on-branch" "FOO=bar gh issue create --body -"
check deny "$TMP/on-main" "FOO=bar git push origin main"
check deny "$TMP/on-main" "command git push origin main"
check deny "$TMP/on-main" "sudo git push origin main"
check deny "$TMP/on-main" "sudo -u deploy git push origin main"
check deny "$TMP/on-main" "/usr/bin/git push origin main"
check deny "$TMP/on-main" "env FOO=bar git push origin main"
check deny "$TMP/on-main" "env -i git push origin main"
check deny "$TMP/on-main" "/usr/bin/env git push origin main"
check deny "$TMP/on-main" "/usr/bin/sudo git push origin main"
check deny "$TMP/on-main" "FOO=bar command sudo git push origin main"
check deny "$TMP/on-main" "/bin/sh -c 'git push origin main'"
check deny "$TMP/on-main" "sh -c 'VAR=x git push origin main'"
check deny "$TMP/on-main" "sh -c 'env git push origin main'"

echo "== rule 2: a flag before the refspec (issue #105) =="
check allow "$TMP/on-main" "git push origin --delete feat/thing"
check allow "$TMP/on-main" "git push origin -d feat/thing"
check allow "$TMP/on-main" "git push origin --force-with-lease feat/thing"
check allow "$TMP/on-main" "git push origin --tags"
check allow "$TMP/on-main" "git push origin --delete :feat/thing"
check allow "$TMP/on-main" "git push --force-with-lease origin feat/thing"

echo "== rule 2: explicit main-targeting denies from ANY branch (#148) =="
check deny "$TMP/on-branch" "git push origin main"
check deny "$TMP/on-branch" "git push origin feat/thing:main"
check deny "$TMP/on-branch" "git push myremote main"
check deny "$TMP/on-branch" "git push origin --all"
check deny "$TMP/on-branch" "git push --tags origin main"
check deny "$TMP/on-branch" "git push -u origin master"

echo "== rule 2: -C/--git-dir win over the payload cwd (#148) =="
check deny "/tmp" "git -C $TMP/on-main push"
check allow "/tmp" "git -C $TMP/on-branch push"
check deny "/tmp" "git --git-dir $TMP/on-main/.git push"
check allow "/tmp" "git --git-dir $TMP/on-branch/.git push origin feat/thing"
check deny "/tmp" "git push"
check deny "$TMP/on-branch" "git -C $TMP/nest -C inner push"
check allow "$TMP" "git -C on-branch push"

echo "== rule 2: must NOT block (false-positive guard) =="
check allow "$TMP/on-main" "git push origin feat/thing"
check allow "$TMP/on-main" "git push --dry-run"
check allow "$TMP/on-main" "git push origin main --dry-run"
check allow "$TMP/on-branch" "git push"
check allow "$TMP/on-branch" "git push -u origin feat/thing"
check allow "$TMP/on-branch" "git push origin"
check allow "$TMP/on-main" "git push --tags"
check allow "$TMP/on-branch" "git push --tags"
check allow "$TMP/on-main" "git push myremote --tags"
check allow "$TMP/on-main" "git status"
check allow "$TMP/on-main" "git log --oneline -1"
check allow "$TMP/on-main" "echo git push"

echo "== documented residuals (allowed by design, pinned) =="
check allow "$TMP/on-main" "command -p git push origin main"
check allow "$TMP/on-main" "command -v git"
check allow "$TMP/on-main" "env --ignore-environment git push origin main"
check allow "$TMP/on-main" "timeout 30 git push origin main"
check allow "$TMP/on-main" "bash -e -c 'git push origin main'"
check allow "$TMP/on-main" "sh -c 'sh -c \"git push origin main\"'"

echo "== string mentions must NOT block (the realistic false positive) =="
check allow "$TMP/on-main" "git commit -m 'docs: explain why git push from main is blocked'"
check allow "$TMP/on-main" "echo git push"
check allow "$TMP/on-branch" "git commit -m 'use gh issue create --body - is wrong'"
check allow "$TMP/on-branch" "grep -rn 'git push' docs/"
check allow "$TMP/on-main" "cat README.md | grep 'git push origin main'"

echo "== degenerate input must never wedge the session =="
check allow "$TMP/on-main" ""
printf '%s' 'not json' | "$GUARD" >/dev/null 2>&1 && { pass=$((pass+1)); echo "  ok   allow  <malformed payload>"; } || { fail=$((fail+1)); echo "  FAIL <malformed payload> did not exit 0"; }

echo
echo "passed: $pass   failed: $fail"
[ "$fail" -eq 0 ]
