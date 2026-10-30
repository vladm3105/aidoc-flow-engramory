#!/usr/bin/env bash
# aidoc-hybrid-ci canonical pre_push_guard.sh
# Dual-purpose pre-push hook:
#   1. Branch protection: blocks direct pushes from protected branches
#      (BASE_BRANCH + PROMOTION_BRANCHES from .ci-config) while permitting
#      one-hop promotion refspecs along that chain.
#   2. Local CI verification: runs `task check` before push to catch defects locally.
#   3. Audit trail verification: delegates to .ci/bin/audit-trail (phrases,
#      REVIEW=required verdicts, and range all live there).

set -uo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
  echo "::error::pre_push_guard.sh requires bash 4+ (found ${BASH_VERSION:-unknown})." >&2
  exit 2
fi

toplevel="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "::error::pre_push_guard.sh must run inside a git working tree." >&2
  exit 2
}
cd "$toplevel" || exit 2

# shellcheck disable=SC2034
remote="${1:-origin}"
# shellcheck disable=SC2034
url="${2:-}"

# Chain from .ci-config (standalone grep: this hook must work even before
# .ci/ is trusted). Protected = BASE_BRANCH + PROMOTION_BRANCHES; promotion
# pushes go one hop along that chain. Fallback: the standard dev chain.
cfg_val() { grep -E "^[[:space:]]*$1=" .ci-config 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '"'; }
base_branch="$(cfg_val BASE_BRANCH)"; base_branch="${CI_BASE_BRANCH:-${base_branch:-main}}"
promo_branches="$(cfg_val PROMOTION_BRANCHES)"
chain="$base_branch $promo_branches"
predecessor() { # BRANCH -> previous branch in the chain, or empty
  local prev="" b
  for b in $chain; do [[ "$b" == "$1" ]] && { echo "$prev"; return; }; prev="$b"; done
}
is_promotion() { [[ -n "$2" && "$1" == "$(predecessor "$2")" ]]; }

# Read standard input from git pre-push hook: <local_ref> <local_oid> <remote_ref> <remote_oid>
has_input=0
need_audit=0
while read -r local_ref local_oid remote_ref _remote_oid; do
  has_input=1
  remote_branch="${remote_ref#refs/heads/}"
  local_branch="${local_ref#refs/heads/}"
  # `git push <remote> HEAD:<branch>` reports the local side as HEAD.
  [[ "$local_branch" == "HEAD" ]] && local_branch="$(git branch --show-current 2>/dev/null || echo HEAD)"

  # Deleting remote branches is handled separately
  if [[ "$local_oid" == "0000000000000000000000000000000000000000" ]]; then
    continue
  fi

  # 1. Branch Protection Guard: no direct pushes from a protected branch.
  if [[ " $chain " == *" $remote_branch "* ]]; then
    if is_promotion "$local_branch" "$remote_branch"; then
      echo "pre-push: promotion push ($local_branch -> $remote_branch) detected — branch protection exempt."
      continue
    fi
    current_branch="$(git branch --show-current 2>/dev/null || echo "$local_branch")"
    if [[ " $chain " == *" $current_branch "* ]]; then
      echo "::error::Direct push to protected branch '$remote_branch' is strictly forbidden!" >&2
      echo "::error::You must develop on a dedicated feature/* or fix/* branch in a worktree and submit a PR." >&2
      exit 1
    fi
  fi

  # 2. Audit trail: delegated after the loop (one verdict per push).
  is_promotion "$local_branch" "$remote_branch" || need_audit=1
done

# OPS-0069 via the framework check itself: it honours AUDIT_TRAIL_PHRASES and,
# with REVIEW=required, a verified self-review PASS satisfies it. Never
# re-implement the phrase scan here — the two copies drifted before.
if [[ $need_audit -eq 1 && -f .ci/bin/audit-trail ]]; then
  bash .ci/bin/audit-trail || {
    echo "::error::OPS-0069 audit-trail check failed (see above)." >&2
    exit 1
  }
fi

if [[ $has_input -eq 0 ]]; then
  exit 0
fi
if [[ -f "scripts/secret_scan.py" ]]; then
  echo "pre-push: running fast regex secret scan..."
  python3 scripts/secret_scan.py . || {
    echo "::error::'scripts/secret_scan.py' detected potential secrets in working tree!" >&2
    exit 1
  }
fi
if command -v task >/dev/null 2>&1; then
  echo "pre-push: executing fast inner loop (task check)..."
  task check || {
    echo "::error::'task check' failed locally. Fix failures before pushing." >&2
    exit 1
  }
fi

echo "pre-push: all pre-flight guards passed."
exit 0
