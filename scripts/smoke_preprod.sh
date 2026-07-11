#!/usr/bin/env bash
# Pre-prod smoke (PLAN-002 Phase 4): the full agent memory loop through the
# real `engramory` CLI against the compose store. Asserts SPEC-07 exit codes.
# Usage: make smoke   (honors POSTGRES_HOST_PORT / POSTGRES_* from .env)
set -euo pipefail

PORT="${POSTGRES_HOST_PORT:-5432}"
USER="${POSTGRES_USER:-engramory}"
PASS="${POSTGRES_PASSWORD:-change_me}"
DB="${POSTGRES_DB:-engramory}"
DSN="postgresql://${USER}:${PASS}@localhost:${PORT}/${DB}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

fail() { echo "smoke FAILED: $1" >&2; exit 1; }

command -v engramory >/dev/null || fail "engramory CLI not on PATH (pip install -e .)"

echo "1/8 init"
engramory --json init --agent-id smoke-agent --project-id preprod-smoke \
  --tenant-id smoke --dsn "$DSN" >/dev/null || fail "init (exit $?)"

echo "2/8 profile get"
engramory --json profile get | grep -q '"agent_id": "smoke-agent"' || fail "profile get"

echo "3/8 memory add"
engramory --json memory add --content "smoke: memory loop $(date +%s)-$$-$RANDOM" --kind outcome \
  | grep -q episode_id || fail "memory add"

echo "4/8 memory distill"
engramory --json memory distill | grep -qE '"created": [0-9]+' || fail "distill"

echo "5/8 memory search (hit with retrieval_id)"
HITS="$(engramory --json memory search --query "memory loop" -k 5)"
RID="$(python3 -c "import json,sys; h=json.loads(sys.argv[1])['hits']; print(h[0]['retrieval_id'] if h else '')" "$HITS")"
MID="$(python3 -c "import json,sys; h=json.loads(sys.argv[1])['hits']; print(h[0]['memory_id'] if h else '')" "$HITS")"
[ -n "$RID" ] && [ -n "$MID" ] || fail "search returned no hit"

echo "6/8 memory feedback"
engramory --json memory feedback --retrieval-id "$RID" --outcome useful >/dev/null || fail "feedback"

echo "7/8 memory forget + fence"
engramory --json memory forget --memory-id "$MID" --reason "smoke cleanup" >/dev/null || fail "forget"
set +e
ENGRAMORY_PROFILE=gcp engramory memory add --content x >/dev/null 2>&1
[ $? -eq 2 ] || fail "dev-tier fence should exit 2"
set -e

echo "8/8 status"
engramory --json status | grep -q '"store": "ok"' || fail "status"

echo "smoke OK — full agent loop verified (init/profile/add/distill/search/feedback/forget/fence/status)"
