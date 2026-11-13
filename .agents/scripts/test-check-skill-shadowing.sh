#!/usr/bin/env bash
set -uo pipefail

CHECK="$(cd "$(dirname "$0")" && pwd)/check-skill-shadowing.sh"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
global="$tmp/global"
repo="$tmp/repo"
mkdir -p "$global/skills/shared" "$global/agents" "$repo"
printf '%s\n' '---' 'name: shared' 'description: shared' '---' > "$global/skills/shared/SKILL.md"
printf '%s\n' '---' 'name: reviewer' 'description: reviewer' '---' > "$global/agents/not-the-name.md"

pass=0
fail=0
expect_status() { # expected, label, command...
  local expected="$1" label="$2" actual
  shift 2
  "$@" >/dev/null 2>&1
  actual=$?
  if [ "$actual" -eq "$expected" ]; then
    printf 'ok   %s\n' "$label"
    pass=$((pass + 1))
  else
    printf 'FAIL %s (wanted %s, got %s)\n' "$label" "$expected" "$actual"
    fail=$((fail + 1))
  fi
}

expect_status 0 'clean repo' env AGENTS_CONFIG_DIR="$global" "$CHECK" "$repo"

mkdir -p "$repo/.agents/skills/shared"
printf '%s\n' '---' 'name: shared' 'description: local' '---' \
  '> **OVERRIDES GLOBAL** — cannot override a user skill' > "$repo/.agents/skills/shared/SKILL.md"
expect_status 1 'same-name project skill is masked even with marker' \
  env AGENTS_CONFIG_DIR="$global" "$CHECK" "$repo"
rm -rf "$repo/.agents/skills"

# Native loader mirrors are adaptation artifacts outside the scan contract:
# a same-name skill under .claude/ is unobserved (accepted risk), not clean.
mkdir -p "$repo/.claude/skills/shared"
printf '%s\n' '---' 'name: shared' 'description: local' '---' \
  > "$repo/.claude/skills/shared/SKILL.md"
expect_status 0 'legacy .claude/ layout is not scanned' \
  env AGENTS_CONFIG_DIR="$global" "$CHECK" "$repo"
rm -rf "$repo/.claude"

mkdir -p "$repo/.agents/agents"
printf '%s\n' '---' 'name: reviewer' 'description: local' '---' \
  'This prose mentions OVERRIDES GLOBAL but is not a declaration.' > "$repo/.agents/agents/different-file.md"
expect_status 1 'agent identity uses name and prose marker does not count' \
  env AGENTS_CONFIG_DIR="$global" "$CHECK" "$repo"

# Literal Markdown backticks are intentional.
# shellcheck disable=SC2016
printf '%s\n' '---' 'name: reviewer' 'description: local' '---' \
  '# Local reviewer' '> **OVERRIDES GLOBAL** — `~/.agents/agents/not-the-name.md`' \
  '> **Why:** project-specific review' '> **Reconciled:** 2026-08-16' \
  > "$repo/.agents/agents/different-file.md"
expect_status 0 'declared project-agent override' \
  env AGENTS_CONFIG_DIR="$global" "$CHECK" "$repo"
rm -rf "$repo/.agents"

mkdir -p "$repo/child with space/.git" "$repo/child with space/grand/.git" \
  "$repo/child with space/grand/.agents/skills/shared"
printf '%s\n' '[submodule "child"]' '  path = child with space' '  url = example' > "$repo/.gitmodules"
printf '%s\n' '[submodule "grand"]' '  path = grand' '  url = example' > "$repo/child with space/.gitmodules"
printf '%s\n' '---' 'name: shared' 'description: nested' '---' \
  > "$repo/child with space/grand/.agents/skills/shared/SKILL.md"
expect_status 1 'recurses through nested submodules with spaced paths' \
  env AGENTS_CONFIG_DIR="$global" "$CHECK" "$repo"

rm -rf "$repo/child with space"
expect_status 2 'uninitialized submodule makes scan incomplete' \
  env AGENTS_CONFIG_DIR="$global" "$CHECK" "$repo"

printf '%s\n' '[submodule "escape"]' '  path = ../outside' '  url = example' > "$repo/.gitmodules"
expect_status 2 'root-escaping submodule path makes scan incomplete' \
  env AGENTS_CONFIG_DIR="$global" "$CHECK" "$repo"

printf '%s\n' '[submodule "broken"' '  path = child' > "$repo/.gitmodules"
expect_status 2 'malformed submodule metadata makes scan incomplete' \
  env AGENTS_CONFIG_DIR="$global" "$CHECK" "$repo"

expect_status 2 'invalid global root fails closed' \
  env AGENTS_CONFIG_DIR="$tmp/missing" "$CHECK" "$repo"

rm -rf "$repo/.agents" "$repo/.gitmodules"
rrepo="$tmp/remote-repo"
mkdir -p "$global/skills/shared" "$rrepo/.agents/skills/shared"
printf '%s\n' '---' 'name: shared' 'description: local' '---' \
  > "$rrepo/.agents/skills/shared/SKILL.md"
git -C "$rrepo" init -q
git -C "$rrepo" config user.email t@t
git -C "$rrepo" config user.name t
git -C "$rrepo" add -A
git -C "$rrepo" commit -qm init
git -C "$rrepo" branch -Mq main
git -C "$rrepo" update-ref refs/remotes/origin/main HEAD
expect_status 1 '--check-remote keeps exit 1 and marks COMMITTED hit' \
  env AGENTS_CONFIG_DIR="$global" "$CHECK" --check-remote "$rrepo"
remote_out=$(env AGENTS_CONFIG_DIR="$global" "$CHECK" --check-remote "$rrepo" 2>&1 || true)
if printf '%s\n' "$remote_out" | grep 'COMMITTED on origin/main' >/dev/null; then
  printf 'ok   --check-remote COMMITTED classification\n'
  pass=$((pass + 1))
else
  printf 'FAIL --check-remote COMMITTED classification\n'
  fail=$((fail + 1))
fi

# A bare marker line with no trailing text is a declaration, not prose.
mkdir -p "$repo/.agents/agents"
printf '%s\n' '---' 'name: reviewer' 'description: local' '---' \
  '> **OVERRIDES GLOBAL**' > "$repo/.agents/agents/bare.md"
expect_status 0 'bare override marker counts as declared' \
  env AGENTS_CONFIG_DIR="$global" "$CHECK" "$repo"
rm -rf "$repo/.agents"

# A master-default repo classifies against origin/master, not origin/main.
mrepo="$tmp/master-repo"
mkdir -p "$mrepo/.agents/skills/shared"
printf '%s\n' '---' 'name: shared' 'description: local' '---' \
  > "$mrepo/.agents/skills/shared/SKILL.md"
git -C "$mrepo" init -q
git -C "$mrepo" config user.email t@t
git -C "$mrepo" config user.name t
git -C "$mrepo" add -A
git -C "$mrepo" commit -qm init
git -C "$mrepo" branch -M master
git -C "$mrepo" update-ref refs/remotes/origin/master HEAD
m_out=$(env AGENTS_CONFIG_DIR="$global" "$CHECK" --check-remote "$mrepo" 2>&1 || true)
if printf '%s\n' "$m_out" | grep 'COMMITTED on origin/master' >/dev/null; then
  printf 'ok   --check-remote master-default classification\n'
  pass=$((pass + 1))
else
  printf 'FAIL --check-remote master-default classification\n'
  fail=$((fail + 1))
fi

# A symbolic origin/HEAD wins over the main/master probes.
git -C "$mrepo" branch develop HEAD 2>/dev/null || git -C "$mrepo" branch -f develop HEAD
git -C "$mrepo" update-ref refs/remotes/origin/develop refs/remotes/origin/master
git -C "$mrepo" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/develop
d_out=$(env AGENTS_CONFIG_DIR="$global" "$CHECK" --check-remote "$mrepo" 2>&1 || true)
if printf '%s\n' "$d_out" | grep 'COMMITTED on origin/develop' >/dev/null; then
  printf 'ok   --check-remote symbolic-ref classification\n'
  pass=$((pass + 1))
else
  printf 'FAIL --check-remote symbolic-ref classification\n'
  fail=$((fail + 1))
fi

# The version floor is injectable, so the branch mechanics are testable
# without a bash 3.2 binary.
expect_status 2 'injectable bash floor fails closed' \
  env MIN_BASH_MAJOR=99 AGENTS_CONFIG_DIR="$global" "$CHECK" "$repo"

# Without realpath the script must fail closed, never scan blind.
mkdir -p "$tmp/noreal"
ln -sf "$(command -v bash)" "$tmp/noreal/bash"
expect_status 2 'missing realpath fails closed' \
  env PATH="$tmp/noreal" AGENTS_CONFIG_DIR="$global" "$CHECK" "$repo"

printf '\npassed: %d  failed: %d\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
