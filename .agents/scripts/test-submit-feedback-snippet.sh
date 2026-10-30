#!/usr/bin/env bash
# Test the submit-feedback body round-trip snippet against the shipped text.
#
# The snippet shells out to `gh`, so the gate cannot run it — but the defect
# classes are testable statically. Like test-hook-snippet.sh, this test reads
# the SHIPPED fence, not a copy. History: the fence once taught
# `--jq -r '.body'`, which real `gh` rejects (`-r` is a jq flag, not a gh
# one), yielding an empty file the edit step then pushed over the real body
# (#189 wiped #188's body exactly this way). The fence must therefore fetch
# raw via `--template '{{.body}}'` (gh-native, verified live) and refuse to
# edit from an empty fetch.
#
# Usage: .agents/scripts/test-submit-feedback-snippet.sh    (exit 0 = all pass)

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SKILL="$ROOT/.agents/skills/submit-feedback/SKILL.md"

pass=0; fail=0
ok() { pass=$((pass+1)); printf 'ok   %s\n' "$1"; }
no() { fail=$((fail+1)); printf 'FAIL %s\n' "$1"; }

# The fence is identified by content, not position: ```bash blocks whose body
# round-trips an issue body through a file.
snip=$(awk '/^```bash$/{b="";f=1;next} /^```$/{if(f&&b~/--body-file "\$BODY"/){printf "%s",b};f=0;next} f{b=b $0 "\n"}' "$SKILL")
[ -n "$snip" ] || { no "body round-trip fence not found in submit-feedback/SKILL.md"; echo; echo "passed: $pass   failed: $fail"; exit 1; }
ok "round-trip fence extracted from shipped text"

# The extraction concatenates every matching fence, so a second fence without
# the guards would pass on the first fence's. Exactly one may match (#214).
fences=$(awk '/^```bash$/{f=1;b="";next} /^```$/{if(f&&b~/--body-file "\$BODY"/)c++;f=0;next} f{b=b $0 "\n"} END{print c+0}' "$SKILL")
if [ "$fences" -eq 1 ]; then
  ok "exactly one round-trip fence"
else
  no "expected 1 round-trip fence, found $fences (guards must hold per fence)"
fi

# The rejected flag must be gone: `--jq -r` makes gh error out, which
# yields the empty file the edit step then pushes over the real body.
if printf '%s' "$snip" | grep -Eq -- "--jq -r"; then
  no "--jq -r present (gh rejects -r; #189)"
else
  ok "no --jq -r fetch"
fi

# A bare `--jq '.body'` fetch is the other half of the same trap: it writes
# a JSON-quoted string that --body-file republishes as literal escapes.
if printf '%s' "$snip" | grep -Eq -- "--jq '\.body'"; then
  no "non-raw --jq '.body' fetch present (republishes JSON quoting)"
else
  ok "no non-raw exact-body fetch"
fi

# The positive control: the fence must fetch raw via --template, else the
# assertions above pass on a snippet that round-trips nothing.
if printf '%s' "$snip" | grep -Fq -- "--template '{{.body}}'"; then
  ok "raw --template fetch present (positive control)"
else
  no "no --template '{{.body}}' fetch found"
fi

# The fence must refuse to edit from an empty fetch, so a failed fetch can
# never again silently become a body wipe.
if printf '%s' "$snip" | grep -Fq -- '[ -s "$BODY" ]'; then
  ok "non-empty guard present"
else
  no 'no non-empty guard ([ -s "$BODY" ])'
fi

# The fence must clean up after itself even when interrupted: a session dying
# between fetch and `rm -f` would otherwise leak repro text into /tmp (#213).
if printf '%s' "$snip" | grep -Fq -- "trap 'rm -f \"\$BODY\"' EXIT"; then
  ok "EXIT trap present"
else
  no "no EXIT trap on the body temp file"
fi

echo
echo "passed: $pass   failed: $fail"
[ "$fail" -eq 0 ]
