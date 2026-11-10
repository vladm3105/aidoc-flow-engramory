#!/usr/bin/env bash
# Test the README hook-restore snippet against fixtures.
#
# The snippet is documentation, so it rots: a test holding its own copy of
# the jq program would prove nothing about the shipped text. This test
# extracts the fenced block from README.md and runs THAT, failing loudly
# when extraction finds nothing.
#
# Usage: .agents/scripts/test-hook-snippet.sh    (exit 0 = all pass)

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0
ok() { pass=$((pass+1)); printf 'ok   %s\n' "$1"; }
no() { fail=$((fail+1)); printf 'FAIL %s\n' "$1"; }

# The fence is identified by content, not position: the ```bash block whose
# body mentions .hooks.PreToolUse. The jq program is the fence minus the
# setup lines (`S=$(mktemp)`, the EXIT trap), the leading
# `jq --arg root ... '`, the trailing `' \` continuation, and the
# settings.json redirection line.
README_FILE="$ROOT/README.md"
[ -f "$ROOT/.agents/README.md" ] && README_FILE="$ROOT/.agents/README.md"
fence=$(awk '/^```bash$/{b="";f=1;next} /^```$/{if(f&&b~/\.hooks\.PreToolUse/){printf "%s",b;exit};f=0;next} f{b=b $0 "\n"}' "$README_FILE")
[ -n "$fence" ] || { no "hook snippet fence not found in README.md"; echo; echo "passed: $pass   failed: $fail"; exit 1; }

# The guard path must interpolate the config root, not hardcode ~/.agents:
# a custom AGENTS_CONFIG_DIR would otherwise restore a hook pointing at a
# nonexistent path (#254).
if printf '%s' "$fence" | grep -Fq -- '--arg root "${AGENTS_CONFIG_DIR:-~/.agents}"'; then
  ok "snippet interpolates the config root"
else
  no "snippet interpolates the config root"
fi

# Secrets must never transit a fixed world-readable path: the fence stages
# through mktemp (mode 600) and contains no fixed /tmp/ write.
if printf '%s' "$fence" | grep -q 'mktemp'; then
  ok "snippet stages through mktemp"
else
  no "snippet stages through mktemp"
fi
if printf '%s' "$fence" | grep -q '/tmp/'; then
  no "snippet has no fixed /tmp/ write"
else
  ok "snippet has no fixed /tmp/ write"
fi

# Settings transit mktemp, so an interrupted run must still clean up (#240).
if printf '%s' "$fence" | grep -Fq -- "trap 'rm -f"; then
  ok "snippet traps EXIT for its temp file"
else
  no "snippet traps EXIT for its temp file"
fi

prog=$(printf '%s' "$fence" | sed -e '/^S=$(mktemp)$/d' -e "/^trap 'rm -f/d" -e 's/^jq --arg root "[^"]*" .//' -e '$d' | sed -e "\$s/' \\\\$//")
[ -n "$prog" ] || { no "jq program extraction failed"; echo; echo "passed: $pass   failed: $fail"; exit 1; }

ROOT_UNDER_TEST="$HOME/.agents"
run() { # <fixture> <out> — execute the EXTRACTED program, nothing else
  jq --arg root "$ROOT_UNDER_TEST" "$prog" "$1" > "$2" 2>/dev/null
}

# 1. Empty settings: the matcher is added (missing .hooks tolerated).
printf '%s' '{}' > "$TMP/empty.json"
if run "$TMP/empty.json" "$TMP/empty.out" \
   && jq -e '.hooks.PreToolUse | length == 1' "$TMP/empty.out" >/dev/null \
   && jq -e --arg want "$ROOT_UNDER_TEST/scripts/guard-bash.sh" '.hooks.PreToolUse[0].hooks[0].command == $want' "$TMP/empty.out" >/dev/null; then
  ok "empty settings gain the matcher"
else
  no "empty settings gain the matcher"
fi

# 2. Other matchers survive: merge, no clobber.
cat > "$TMP/other.json" <<'EOF'
{"hooks": {"PreToolUse": [{"matcher": "WebFetch", "hooks": [{"type": "command", "command": "other.sh"}]}]}}
EOF
if run "$TMP/other.json" "$TMP/other.out" \
   && jq -e '.hooks.PreToolUse | length == 2' "$TMP/other.out" >/dev/null \
   && jq -e '.hooks.PreToolUse[0] == {"matcher": "WebFetch", "hooks": [{"type": "command", "command": "other.sh"}]}' "$TMP/other.out" >/dev/null \
   && jq -e --arg want "$ROOT_UNDER_TEST/scripts/guard-bash.sh" '.hooks.PreToolUse[1].hooks[0].command == $want' "$TMP/other.out" >/dev/null; then
  ok "pre-existing matcher preserved"
else
  no "pre-existing matcher preserved"
fi

# 3. Already wired: byte-identical output (idempotent, no duplicate).
run "$TMP/other.out" "$TMP/twice.out" \
  && jq -e '.hooks.PreToolUse | length == 2' "$TMP/twice.out" >/dev/null \
  && cmp -s "$TMP/other.out" "$TMP/twice.out" \
  && ok "re-run is idempotent" \
  || no "re-run is idempotent"

# 4. Custom root: the restored hook points at it, not ~/.agents.
ROOT_UNDER_TEST="/data/agents"
printf '%s' '{}' > "$TMP/custom.json"
if run "$TMP/custom.json" "$TMP/custom.out" \
   && jq -e --arg want "/data/agents/scripts/guard-bash.sh" '.hooks.PreToolUse[0].hooks[0].command == $want' "$TMP/custom.out" >/dev/null; then
  ok "custom root honored"
else
  no "custom root honored"
fi

echo
echo "passed: $pass   failed: $fail"
[ "$fail" -eq 0 ]
