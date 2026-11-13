#!/usr/bin/env bash
# with-node-junit.sh — run a command (verbatim) with per-test JUnit output
# from every `node --test` it spawns, by injecting reporters through NODE_OPTIONS.
#
#   with-node-junit.sh npm run verify:product
#
# The command itself is unchanged, so gate semantics stay identical to `npm run verify:product`.
# Node rejects mismatched --test-reporter/--test-reporter-destination counts, so if any
# package.json script already passes --test-reporter we do NOT inject, and the summarizer
# reports the frontend layer at coarse (exit-code-only) granularity.
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../node" && pwd)"
dir="${NODE_TEST_JUNIT_DIR:-${RESULTS_DIR:-test-results}/junit/node}"
rm -rf "$dir"; mkdir -p "$dir"

if grep -q -- '--test-reporter' package.json 2>/dev/null; then
  echo "WARN: package.json scripts set --test-reporter; frontend results will be coarse (exit code only)" >&2
  [[ -n "${LAYER_WARN_FILE:-}" ]] && echo "frontend: coarse granularity (package.json sets --test-reporter)" >>"$LAYER_WARN_FILE"
  exec "$@"
fi

export NODE_TEST_JUNIT_DIR="$dir"
export NODE_OPTIONS="${NODE_OPTIONS:-} --test-reporter=spec --test-reporter-destination=stdout --test-reporter=$here/node-junit-reporter.mjs --test-reporter-destination=stderr"
exec "$@"
