#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! "$PYTHON_BIN" -c 'import importlib.util, sys; sys.exit(0 if all(importlib.util.find_spec(m) for m in ("pytest", "yaml")) else 1)' 2>/dev/null; then
  echo "missing python modules for this gate (need pytest and PyYAML): ${PYTHON_BIN} -m pip install --requirement requirements-dev.txt" >&2
  exit 2
fi

"$PYTHON_BIN" .agents/scripts/check-frontmatter.py
bash -n .agents/scripts/*.sh .agents/skills/verified-planning/install.sh \
  .agents/skills/verified-planning/tests/test_install.sh
bash .agents/scripts/test-guard-bash.sh
bash .agents/scripts/test-check-skill-shadowing.sh
# No self-scan of this repo: with AGENTS_CONFIG_DIR==repo every hit trips the
# scanner's self-exclusion, so such a line always exits 0 and proves nothing.
# Real collision coverage is the test suite on the line above.
bash .agents/scripts/test-hook-snippet.sh
bash .agents/scripts/test-check-requirements.sh
bash .agents/scripts/test-submit-feedback-snippet.sh
bash .agents/scripts/test-check-frontmatter.sh
"$PYTHON_BIN" -m pytest -q .agents/skills/verified-planning/tests .agents/skills/memory-hygiene/tests
bash .agents/skills/verified-planning/tests/test_install.sh
bash .agents/scripts/test-pull.sh
