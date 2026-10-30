#!/usr/bin/env bash
# Test check-frontmatter.py against fixture trees.
#
# main() globs the REAL tree via ROOT-derived paths, so fixtures cannot reach
# it in place. The seam is a temp tree mirroring the repo layout: the script
# is copied under $T/.agents/scripts/ (which re-roots it to $T), with
# fixture .agents/{agents,rules,skills}/ plus a stub instruction file.
# Fixtures are inline heredocs: the scripts allowlist admits only *.sh/*.py,
# so separate fixture files would never commit.
#
# Usage: .agents/scripts/test-check-frontmatter.sh    (exit 0 = all pass)

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

pass=0; fail=0
ok() { pass=$((pass+1)); printf 'ok   %s\n' "$1"; }
no() { fail=$((fail+1)); printf 'FAIL %s\n' "$1"; }

mkdir -p "$T/.agents/scripts" "$T/.agents/agents" "$T/.agents/rules" "$T/.agents/skills/ok"
cp "$ROOT/.agents/scripts/check-frontmatter.py" "$T/.agents/scripts/"
printf '# stub\n' > "$T/AGENTS.md"
cat > "$T/.agents/agents/x.md" <<'EOF'
---
name: x
description: fixture agent
model: inherit
---
EOF
cat > "$T/.agents/skills/ok/SKILL.md" <<'EOF'
---
name: ok
description: fixture skill
---
EOF
cat > "$T/.agents/rules/good.md" <<'EOF'
---
paths:
  - "**/*.md"
---
EOF

run() { "$PYTHON_BIN" "$T/.agents/scripts/check-frontmatter.py" >/dev/null 2>&1; }

if run; then ok "clean tree passes"; else no "clean tree passes"; fi

cat > "$T/.agents/rules/bad.md" <<'EOF'
---
paths:
  - "**/*.md"
applyTo: '**/*.md'
---
EOF
if run; then no "unknown rule key fails"; else ok "unknown rule key fails"; fi
rm "$T/.agents/rules/bad.md"

cat > "$T/.agents/rules/empty.md" <<'EOF'
---
paths: []
---
EOF
if run; then no "empty paths list fails"; else ok "empty paths list fails"; fi
rm "$T/.agents/rules/empty.md"

printf '%s\n' '---' 'name: bad' '---' > "$T/.agents/agents/bad.md"
if run; then no "agent without model fails"; else ok "agent without model fails"; fi

echo
echo "passed: $pass   failed: $fail"
[ "$fail" -eq 0 ]
