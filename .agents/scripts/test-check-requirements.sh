#!/usr/bin/env bash
# requirements-dev.txt must be hash-pinned and CI must enforce it.
# A range-pinned, hash-less file installs whatever the index serves today;
# --require-hashes makes a substituted distribution fail closed at install.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REQ="$ROOT/requirements-dev.txt"
[ -f "$ROOT/.agents/requirements-dev.txt" ] && REQ="$ROOT/.agents/requirements-dev.txt"
CI="$ROOT/.github/workflows/config-checks.yml"

# Every requirement is ==-pinned and carries at least one --hash line.
awk '
  function flush() {
    if (req != "" && !hashed) { print "unhashed: " req; bad = 1 }
  }
  BEGIN { bad = 0; req = "" }
  /^#/ || /^[[:space:]]*$/ { next }
  /^[A-Za-z0-9_.\[]+==[^[:space:]\\]+ \\$/ { flush(); req = $0; hashed = 0; next }
  /^[[:space:]]+--hash=sha256:[0-9a-f]{64}[[:space:]]*\\?$/ { hashed = 1; next }
  { print "unexpected line: " $0; bad = 1 }
  END { flush(); exit bad }
' "$REQ" || { echo "FAIL: requirements-dev.txt is not fully hash-pinned"; exit 1; }

# CI enforces the hashes (without the flag, pip ignores them), or uv.lock enforces sha256 hashes.
if [ -f "$CI" ]; then
  grep -q -- '--require-hashes' "$CI" \
    || { echo "FAIL: config-checks.yml installs without --require-hashes"; exit 1; }
else
  grep -q -- 'sha256:' "$ROOT/uv.lock" \
    || { echo "FAIL: uv.lock does not contain sha256 package hashes"; exit 1; }
fi

echo "PASS"
