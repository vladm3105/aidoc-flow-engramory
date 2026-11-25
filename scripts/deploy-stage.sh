#!/usr/bin/env bash
# Retired by PLAN-026 / CHG-012 (C11): direct imperative SSH/rsync deploys to stage
# violate the Everything-as-Code mandate. Deploy via platform-stage-ops OpenTofu + pyinfra.
set -euo pipefail

cat >&2 <<'EOF'
ERROR: scripts/deploy-stage.sh is retired (PLAN-026 / CHG-012).
Stage deployments and secret custody (OpenBao /dev/shm) are governed declaratively via:
  platform-stage-ops (tofu/stage-config-management + stacks/platform-engramory + manifest.tsv)
Refusing imperative SSH/rsync mutation of stage.
EOF
exit 1
