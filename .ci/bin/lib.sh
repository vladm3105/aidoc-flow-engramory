# shellcheck shell=bash
# Shared helpers for aidoc-local-flow-ci scripts. Sourced, not executed.

ci_root() { git rev-parse --show-toplevel 2>/dev/null; }

# Base branch that changes are compared against (default: main).
ci_base_branch() { echo "${CI_BASE_BRANCH:-$(ci_config BASE_BRANCH main)}"; }

# Best available base ref: push-remote/<base>, then origin/<base>, then local
# <base>, else empty. The push remote leads so Gitea-first checkouts (daily
# work on `gitea`, GitHub a mirror) diff against the live base, not a stale one.
ci_base_ref() {
  local b pr; b=$(ci_base_branch); pr=$(ci_push_remote)
  if git rev-parse --verify --quiet "$pr/$b" >/dev/null; then echo "$pr/$b"
  elif git rev-parse --verify --quiet "origin/$b" >/dev/null; then echo "origin/$b"
  elif git rev-parse --verify --quiet "$b" >/dev/null; then echo "$b"
  fi
}

# Merge-base of HEAD with the base ref; empty when unavailable (e.g. first commit).
ci_merge_base() {
  local ref; ref=$(ci_base_ref)
  [[ -n $ref ]] && git merge-base "$ref" HEAD 2>/dev/null
}

# Read KEY from .ci-config (simple KEY=VALUE lines, # comments). Never sourced/executed.
ci_config() {
  local key=$1 default=${2:-} file; file="$(ci_root)/.ci-config"
  if [[ -f $file ]]; then
    local v; v=$(grep -E "^[[:space:]]*${key}=" "$file" | tail -n1 | cut -d= -f2-)
    if [[ ${#v} -ge 2 && ${v:0:1} == '"' && ${v: -1} == '"' ]]; then v=${v:1:${#v}-2}; fi
    [[ -n $v ]] && { echo "$v"; return; }
  fi
  echo "$default"
}

# ci_list KEY [DEFAULT] — ci_config for space-separated lists: unset/empty still means
# DEFAULT, but the whole value "none" clears the list. Only an exact whole-value match
# clears, so "anon", "xnone" and "None" survive. (SEMGREP_CONFIGS and
# PROMOTION_REQUIRED_CHECKS are carved out and stay on ci_config.)
ci_list() {
  local v; v=$(ci_config "$1" "${2:-}")
  [[ $v == none ]] && echo "" || echo "$v"
}

# ISO-8601 UTC -> whole minutes ago (GNU date, BSD fallback). Empty/fail
# when unparseable. age_hours is the same in hours (identical values to
# the pre-lib inbox implementation: floor of the minute count / 60).
age_minutes() {
  local then_s now_s
  then_s=$(date -u -d "$1" +%s 2>/dev/null || date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$1" +%s 2>/dev/null) || return 1
  [[ $then_s =~ ^[0-9]+$ ]] || return 1
  now_s=$(date -u +%s) || return 1
  echo $(( (now_s - then_s) / 60 ))
}
age_hours() {
  local m
  m=$(age_minutes "$1") || return 1
  echo $((m / 60))
}

# Mirror freshness for one branch (D2 gate): prints JSON
# {configured, equal, lag_min, detail}. configured ⟺ gitea forge with
# MIRROR_ENABLED=1. equal ⟺ replica head == Gitea head, fetched live.
# Fail-closed: a missing mirror remote or a transport failure yields
# equal:false (an unverified mirror never passes). Never dies.
mirror_gate_status() {
  local src mtarget s m at lag
  if [[ $(ci_forge) != gitea || $(ci_config MIRROR_ENABLED 1) != 1 ]]; then
    echo '{"configured":false,"equal":null,"lag_min":null,"detail":"mirror not configured"}'
    return 0
  fi
  src=$(ci_push_remote)
  mtarget=$(ci_config MIRROR_REMOTE github)
  if ! git remote get-url "$mtarget" >/dev/null 2>&1; then
    echo "{\"configured\":true,\"equal\":false,\"lag_min\":null,\"detail\":\"remote '$mtarget' missing (MIRROR_REMOTE)\"}"
    return 0
  fi
  lag=null
  at=$(jq -r '.updated_at // empty' "$(git rev-parse --absolute-git-dir)/aidoc-ci/mirror.json" 2>/dev/null)
  [[ -n $at ]] && lag=$(age_minutes "$at" 2>/dev/null) || lag=null
  [[ $lag =~ ^[0-9]+$ ]] || lag=null
  # Full fetches: per-ref fetch aborts on missing refs, hiding transport errors.
  if ! git fetch --quiet "$src" 2>/dev/null; then
    echo "{\"configured\":true,\"equal\":false,\"lag_min\":$lag,\"detail\":\"cannot fetch $src\"}"
    return 0
  fi
  if ! git fetch --quiet "$mtarget" 2>/dev/null; then
    echo "{\"configured\":true,\"equal\":false,\"lag_min\":$lag,\"detail\":\"cannot fetch $mtarget\"}"
    return 0
  fi
  s=$(git rev-parse --verify -q "refs/remotes/$src/$1") || s=""
  m=$(git rev-parse --verify -q "refs/remotes/$mtarget/$1") || m=""
  if [[ -n $s && $s == "$m" ]]; then
    echo "{\"configured\":true,\"equal\":true,\"lag_min\":$lag,\"detail\":\"replica head matches\"}"
  elif [[ -z $s ]]; then
    echo "{\"configured\":true,\"equal\":false,\"lag_min\":$lag,\"detail\":\"'$1' absent on $src\"}"
  elif [[ -z $m ]]; then
    echo "{\"configured\":true,\"equal\":false,\"lag_min\":$lag,\"detail\":\"'$1' absent on replica — run task mirror\"}"
  elif [[ $lag == null ]]; then
    echo "{\"configured\":true,\"equal\":false,\"lag_min\":null,\"detail\":\"heads differ, never synced — run task mirror\"}"
  else
    echo "{\"configured\":true,\"equal\":false,\"lag_min\":$lag,\"detail\":\"heads differ (last sync ${lag}min ago) — run task mirror\"}"
  fi
}

# ERE matching hardcoded local workspace paths (.ci-config PATHS_DENY).
# The default covers home devd checkouts and the legacy /opt/data tree;
# the whole value "none" clears it (callers report n/a).
ci_paths_deny() {
  local v; v=$(ci_config PATHS_DENY "(/home/[a-zA-Z0-9_-]+/devd|/Users/[a-zA-Z0-9_-]+/devd|/opt/data/aidoc-flow)")
  [[ $v == none ]] && echo "" || echo "$v"
}

# ci_config_committed KEY [DEFAULT] [FILE] — ci_config pinned to the merge-base commit.
# Trust-root settings (who judges a review) must not come from the uncommitted worktree
# or unmerged branch commits. Falls back to the worktree value when there is no merge
# base yet (bootstrap) or the key is absent at the base.
ci_config_committed() {
  local key=$1 default=${2:-} file=${3:-.ci-config} base content v
  base=$(ci_merge_base)
  if [[ -n $base ]] && content=$(git show "$base:$file" 2>/dev/null); then
    v=$(grep -E "^[[:space:]]*${key}=" <<<"$content" | tail -n1 | cut -d= -f2-)
    if [[ ${#v} -ge 2 && ${v:0:1} == '"' && ${v: -1} == '"' ]]; then v=${v:1:${#v}-2}; fi
    [[ -n $v ]] && { echo "$v"; return; }
  fi
  ci_config "$key" "$default"
}

# :(exclude) pathspecs shared by the file-listing helpers: the vendored framework
# plus LINT_EXCLUDE from .ci-config. One spec per line, always non-empty.
ci_exclude_specs() {
  printf '%s\n' ':(exclude).ci/**'
  local ex; for ex in $(ci_list LINT_EXCLUDE ""); do printf '%s\n' ":(exclude)$ex"; done
}

# Files (tracked + untracked, non-ignored) matching git pathspecs, excluding the vendored
# framework and LINT_EXCLUDE pathspecs from .ci-config. One path per line, existing files only.
ci_files() {
  # Excludes FIRST: trailing :(exclude) specs after multiple slash-positives match nothing
  # on newer git (verified on 2.53.0); set-difference semantics are order-independent.
  local spec; mapfile -t spec < <(ci_exclude_specs)
  spec+=("$@")
  { git ls-files -- "${spec[@]}"; git ls-files --others --exclude-standard -- "${spec[@]}"; } \
    | sort -u | while IFS= read -r f; do [[ -f $f ]] && printf '%s\n' "$f"; done
}

# Files changed vs the merge-base with the base branch (added/copied/modified/renamed) plus
# untracked files. Falls back to all tracked files when there is no base yet.
ci_changed_files() {
  local base spec; base=$(ci_merge_base)
  if [[ -z $base ]]; then ci_files .; return; fi
  mapfile -t spec < <(ci_exclude_specs)
  { git diff --name-only --diff-filter=ACMR "$base" -- "${spec[@]}" .
    git ls-files --others --exclude-standard -- "${spec[@]}" .; } \
    | sort -u | while IFS= read -r f; do [[ -f $f ]] && printf '%s\n' "$f"; done
}

# True when the named universal check is in the report-only list (.ci-config REPORT_ONLY).
# Accepts "svc:task" (matches either the full name or just "task") or a universal name.
ci_report_only() { local l; l=" $(ci_list REPORT_ONLY "") "; [[ $l == *" $1 "* || $l == *" ${1#*:} "* ]]; }

# sha256 of stdin (Linux sha256sum or macOS shasum).
ci_sha256() { if command -v sha256sum >/dev/null; then sha256sum | cut -d' ' -f1; else shasum -a 256 | cut -d' ' -f1; fi; }

# Server-side check state for a commit on GitHub: success | pending | failure | missing.
# Looks at check runs (GitHub Actions job names, e.g. "verify") and commit statuses.
ci_remote_check() {
  local sha=$1 name=$2 run st
  run=$(gh api -X GET "repos/{owner}/{repo}/commits/$sha/check-runs?per_page=100" -f check_name="$name" \
    --jq '[.check_runs[] | (.conclusion // "pending")] | if length == 0 then "missing"
          elif all(. == "success" or . == "skipped" or . == "neutral") then "success"
          elif any(. == "pending") then "pending" else "failure" end' 2>/dev/null) || run=missing
  [[ -n $run ]] || run=missing
  if [[ $run == missing ]]; then
    # The check name reaches jq only via --arg: interpolating it into the
    # program would let a quote-bearing name escape the predicate (#16).
    st_json=$(gh api "repos/{owner}/{repo}/commits/$sha/status" 2>/dev/null) || st_json=""
    st=$(jq -r --arg name "$name" '[.statuses[]? | select(.context == $name) | .state] | first // "missing"' <<<"$st_json" 2>/dev/null) || st=missing
    [[ -n $st ]] || st=missing
    case $st in success) run=success ;; pending) run=pending ;; failure|error) run=failure ;; *) run=missing ;; esac
  fi
  echo "$run"
}

# Every path changed vs the merge-base (committed + uncommitted, incl. deletions and both sides of
# renames) plus untracked files. Returns 1 when it cannot be computed (caller must fail closed).
ci_impact_files() {
  local base out
  base=$(ci_merge_base); [[ -n $base ]] || return 1
  out=$(git diff --no-renames --name-only "$base") || return 1
  { printf '%s\n' "$out"; git ls-files --others --exclude-standard; } | sed '/^$/d' | sort -u
}

# ci_glob_match PATH PATTERN... — shell-style globs where "*" also matches "/" ("docs/*" = docs tree).
ci_glob_match() {
  local f=$1 p; shift
  for p in "$@"; do
    # shellcheck disable=SC2053
    [[ $f == $p ]] && return 0
  done
  return 1
}

# --- Forge abstraction (PLAN-006): github (gh CLI) or gitea (curl API) ---
# ci_forge — resolution: CI_FORGE env/--forge flag wins; else the current
# branch's upstream remote URL (github.com = github, anything else = gitea);
# remotes-less repos default to gitea. Callers must keep both backends'
# machine-readable output shapes identical.
ci_forge() {
  if [[ -n ${CI_FORGE:-} ]]; then echo "$CI_FORGE"; return; fi
  local b remote url
  b=$(git branch --show-current 2>/dev/null)
  remote=$(git config "branch.$b.remote" 2>/dev/null)
  [[ -z $remote ]] && remote=origin
  url=$(git remote get-url "$remote" 2>/dev/null)
  [[ $url == *github.com* ]] && echo github || echo gitea
}

# Remote name carrying forge traffic (fetch/push/PRs) for the active forge:
# the URL-consistent remote, preferring the branch upstream.
ci_push_remote() {
  local b remote url
  b=$(git branch --show-current 2>/dev/null)
  remote=$(git config "branch.$b.remote" 2>/dev/null)
  url=$(git remote get-url "$remote" 2>/dev/null)
  if [[ $(ci_forge) == github ]]; then
    [[ $url == *github.com* ]] && { echo "$remote"; return; }
    echo origin; return
  fi
  if [[ -n $url && $url != *github.com* ]]; then echo "$remote"; return; fi
  if git remote get-url gitea >/dev/null 2>&1; then echo gitea; return; fi
  echo origin
}

# owner/repo from the push-remote URL (https, ssh:// and scp-like forms).
forge_repo() {
  local url
  url=$(git remote get-url "$(ci_push_remote)" 2>/dev/null) || return 1
  sed -E -e 's#^https?://[^/]+/##' -e 's#^ssh://[^/]+/##' -e 's#^git@[^:]+:##' -e 's#\.git$##' <<<"$url"
}

# Gitea API call. Usage: forge_api METHOD PATH [DATA]; PATH like
# repos/o/r/pulls. Token: GITEA_TOKEN. HTTP via ${FORGE_CURL_BIN:-curl}
# (tests point it at the stub, which must append |CODE like curl -w).
# Prints the body; fails on transport errors, unset token, or HTTP>=400.
forge_api() {
  [[ -n ${GITEA_TOKEN:-} ]] || { echo "forge_api: GITEA_TOKEN not set" >&2; return 1; }
  local method=$1 path=$2 data=${3:-}
  local base curl_bin=${FORGE_CURL_BIN:-curl} args=(-sS -m 30 -H "Authorization: token $GITEA_TOKEN")
  base=$(forge_instance) || return 1
  [[ $method != GET ]] && args+=(-X "$method")
  [[ -n $data ]] && args+=(-H 'Content-Type: application/json' -d "$data")
  local out code
  out=$("$curl_bin" "${args[@]}" -w '|%{http_code}' "$base/api/v1/$path") || return 1
  code=${out##*|}; out=${out%|*}
  printf '%s' "$out"
  [[ ${code:-0} =~ ^[0-9]+$ && $code -lt 400 ]]
}

# Ensure a repo label exists (idempotent, both forges). COLOR is hex w/o '#'.
forge_ensure_label() {
  if [[ $(ci_forge) == github ]]; then
    gh label create "$1" --color "$2" --description "$3" --force >/dev/null 2>&1 || true
    return 0
  fi
  local repo
  repo=$(forge_repo) || return 0
  forge_api POST "repos/$repo/labels" \
    "$(jq -n --arg n "$1" --arg c "#$2" --arg d "$3" '{name:$n,color:$c,description:$d}')" \
    >/dev/null 2>&1 || true
}

# Label id for NAME (gitea needs ids to attach; github uses the name itself).
# Optional COLOR (hex w/o '#') tints the ensure-on-use creation on gitea.
forge_label_id() {
  if [[ $(ci_forge) == github ]]; then echo "$1"; return 0; fi
  local repo
  repo=$(forge_repo) || return 1
  forge_ensure_label "$1" "${2:-B60205}" "$1" >/dev/null 2>&1
  forge_api GET "repos/$repo/labels" | jq -r --arg n "$1" '[.[] | select(.name == $n) | .id] | first // empty'
}

# GET a paged Gitea list endpoint; prints the concatenated JSON array ([] when
# empty). Pages until a short page (per_page=50 is the server max) so large
# PR lists are never silently truncated. PATH carries its query already
# (e.g. "repos/o/r/pulls?state=open").
forge_gitea_pages() {
  local page=1 chunk n pages=()
  while true; do
    chunk=$(forge_api GET "$1&per_page=50&page=$page" 2>/dev/null) || return 1
    n=$(jq 'if type == "array" then length else 0 end' <<<"$chunk" 2>/dev/null) || return 1
    [[ $n -gt 0 ]] || break
    pages+=("$chunk")
    [[ $n -lt 50 ]] && break
    page=$((page + 1))
  done
  printf '%s\n' "${pages[@]+"${pages[@]}"}" | jq -sc 'add // []'
}

# PR number for the CURRENT branch (open PR), or return 1. Arbitrary branches
# go through forge_pr_view. The github call is byte-identical to the
# pre-fork guard it replaces.
forge_pr_number() {
  if [[ $(ci_forge) == github ]]; then
    local out
    out=$(gh pr view --json number 2>/dev/null | jq -r .number) || return 1
    [[ -n $out ]] && echo "$out"
    return
  fi
  local repo out b
  b=$(git branch --show-current 2>/dev/null)
  repo=$(forge_repo) || return 1
  out=$(forge_gitea_pages "repos/$repo/pulls?state=open" 2>/dev/null \
    | jq -r --arg b "$b" '[.[] | select(.head.ref == $b) | .number] | first // empty')
  [[ -n $out ]] && echo "$out"
}

# Post commit status STATE (success|failure|pending) with CONTEXT/DESCRIPTION.
forge_post_status() {
  if [[ $(ci_forge) == github ]]; then
    gh api "repos/{owner}/{repo}/statuses/$1" -f state="$2" -f context="$3" -f description="$4" >/dev/null
    return
  fi
  local repo
  repo=$(forge_repo) || return 1
  forge_api POST "repos/$repo/statuses/$1" \
    "$(jq -n --arg s "$2" --arg c "$3" --arg d "$4" '{state:$s,context:$c,description:$d}')" >/dev/null
}

# Comment FILE on PR NUM. NUM selects the PR on gitea; github targets the
# current branch (all callers run on the PR branch), keeping the exact
# pre-fork gh invocations.
forge_pr_comment() {
  if [[ $(ci_forge) == github ]]; then
    gh pr comment --body-file "$2" >/dev/null
    return
  fi
  local repo body
  repo=$(forge_repo) || return 1
  body=$(jq -n --rawfile b "$2" '{body:$b}')
  forge_api POST "repos/$repo/issues/$1/comments" "$body" >/dev/null
}

# Create a PR; prints the URL (github) or number (gitea).
forge_pr_create() {
  if [[ $(ci_forge) == github ]]; then
    gh pr create --base "$1" --head "$2" --title "$3" --body-file "$4"
    return
  fi
  local repo body
  repo=$(forge_repo) || return 1
  body=$(jq -n --arg base "$1" --arg head "$2" --arg t "$3" --rawfile b "$4" \
    '{base:$base,head:$head,title:$t,body:$b}')
  forge_api POST "repos/$repo/pulls" "$body" | jq -r '.html_url // .number'
}

forge_label_add() {  # NUM NAME [COLOR] — COLOR tints gitea ensure-on-use only
  if [[ $(ci_forge) == github ]]; then
    gh pr edit --add-label "$2" >/dev/null 2>&1
    return
  fi
  local repo id
  repo=$(forge_repo) || return 1
  id=$(forge_label_id "$2" "${3:-}") && [[ -n $id ]] || return 1
  forge_api POST "repos/$repo/issues/$1/labels" "{\"labels\":[$id]}" >/dev/null
}

forge_label_remove() {
  if [[ $(ci_forge) == github ]]; then
    gh pr edit --remove-label "$2" >/dev/null 2>&1
    return
  fi
  local repo id
  repo=$(forge_repo) || return 1
  id=$(forge_label_id "$2") && [[ -n $id ]] || return 1
  forge_api DELETE "repos/$repo/issues/$1/labels" "{\"labels\":[$id]}" >/dev/null
}

# Arm merge-on-green for PR NUM (squash|merge|rebase). GitHub only in practice:
# Gitea 1.27 answers 405 while checks run, so Gitea callers merge on observed
# green instead (pr-status --watch, forge_merge_now) — a direct Gitea call
# succeeds only when the PR is already green.
forge_merge_auto() {
  if [[ $(ci_forge) == github ]]; then
    gh pr merge --auto "--$2" --delete-branch >/dev/null
    return
  fi
  local repo
  repo=$(forge_repo) || return 1
  forge_api POST "repos/$repo/pulls/$1/merge" \
    "$(jq -n --arg "do" "$2" '{Do:$do,MergeWhenChecksSucceed:true}')" >/dev/null
}

# Merge PR NUM right now (merge|squash|rebase). Unlike forge_merge_auto this
# performs the merge immediately: the caller must have observed green first.
# (Gitea 1.27 rejects MergeWhenChecksSucceed while checks run — pilot probe,
# PLAN-006 Phase 4 — so Gitea merge-on-green polls to green, then calls this.)
forge_merge_now() {
  if [[ $(ci_forge) == github ]]; then
    gh pr merge "$1" "--$2" >/dev/null
    return
  fi
  local repo
  repo=$(forge_repo) || return 1
  forge_api POST "repos/$repo/pulls/$1/merge" "$(jq -n --arg "do" "$2" '{Do:$do}')" >/dev/null
}

# Combined commit status for SHA: prints success|pending|failure|missing and
# exits 0; exits 1 on transport/parse failure (still printing missing). missing
# means no statuses reported at all. Pure statuses read on both forges:
# promotion keeps ci_remote_check's check-runs-first semantics on github.
forge_commit_status() {
  local out
  if [[ $(ci_forge) == github ]]; then
    out=$(gh api "repos/{owner}/{repo}/commits/$1/status" 2>/dev/null) || { echo missing; return 1; }
  else
    local repo
    repo=$(forge_repo) || { echo missing; return 1; }
    out=$(forge_api GET "repos/$repo/commits/$1/status" 2>/dev/null) || { echo missing; return 1; }
  fi
  jq -r 'if (.statuses | length) == 0 then "missing"
    elif .state == "success" then "success"
    elif .state == "failure" or .state == "error" then "failure"
    else "pending" end' <<<"$out" 2>/dev/null || { echo missing; return 1; }
}

# Single status-context read for SHA: prints success|pending|failure|missing,
# exits 1 on transport failure (still printing missing). On gitea the context
# names are the twin-posted ones (signoff, review, verify-source, ...). The
# gitea path reads the plural statuses log: the combined endpoint's per-row
# .state is always null on 1.27 (live probe) while the log carries the true
# .status per row (latest id wins; skipped counts as success, matching
# ci_remote_check's check-run semantics on github).
forge_check_status() {
  local out
  if [[ $(ci_forge) == github ]]; then
    out=$(gh api "repos/{owner}/{repo}/commits/$1/status" 2>/dev/null) || { echo missing; return 1; }
    jq -r --arg name "$2" '[.statuses[]? | select(.context == $name) | .state] | first // "missing"
      | if . == "success" then "success" elif . == "failure" or . == "error" then "failure"
        elif . == "missing" then "missing" else "pending" end' <<<"$out" 2>/dev/null \
      || { echo missing; return 1; }
    return
  fi
  local repo
  repo=$(forge_repo) || { echo missing; return 1; }
  out=$(forge_api GET "repos/$repo/commits/$1/statuses" 2>/dev/null) || { echo missing; return 1; }
  jq -r --arg name "$2" 'sort_by(.id) | map(select(.context == $name)) | last | .status // "missing"
    | if . == "success" or . == "skipped" then "success" elif . == "failure" or . == "error" then "failure"
      elif . == "missing" then "missing" else "pending" end' <<<"$out" 2>/dev/null \
    || { echo missing; return 1; }
}

# Open PRs as [{number,head,base,title,labels:[names]}]. Optional LABEL keeps
# only PRs carrying it (server-side on github, client-side on gitea).
forge_pr_list() {
  local label=${1:-}
  if [[ $(ci_forge) == github ]]; then
    local args=(--state open --limit 1000 --json "number,headRefName,baseRefName,title,labels") out
    [[ -n $label ]] && args+=(--label "$label")
    out=$(gh pr list "${args[@]}" 2>/dev/null) || return 1
    [[ -n $out ]] || { echo "[]"; return 0; }
    jq '[.[] | {number, head:.headRefName, base:.baseRefName, title, labels:[.labels[]?.name]}]' <<<"$out"
    return
  fi
  local repo out
  repo=$(forge_repo) || return 1
  out=$(forge_gitea_pages "repos/$repo/pulls?state=open" 2>/dev/null) || return 1
  if [[ -n $label ]]; then
    jq --arg l "$label" '[.[] | select(any(.labels[]?; .name == $l))
      | {number, head:.head.ref, base:.base.ref, title, labels:[.labels[]?.name]}]' <<<"$out"
  else
    jq '[.[] | {number, head:.head.ref, base:.base.ref, title, labels:[.labels[]?.name]}]' <<<"$out"
  fi
}

# Rerun the latest completed failed Actions run for SHA. Prints the run id.
# Exit 0 reran, 1 nothing retryable (still running or no failed run — the
# caller escalates without a retry), 2 transport error. Scope differs by
# forge: github reruns failed jobs only (gh run rerun --failed); Gitea 1.27
# reruns the whole run (the run-level rerun re-queues every job and no
# per-job endpoint exists — both probed live on the pilot), so callers must
# not claim failed-only there.
forge_rerun_checks() {
  if [[ $(ci_forge) == github ]]; then
    local runs id
    runs=$(gh run list --commit "$1" --json databaseId,conclusion,status 2>/dev/null) || return 2
    id=$(jq -r '[.[] | select(.status == "completed")][0]
      | select(.conclusion == "failure") | .databaseId // empty' <<<"$runs") || return 2
    [[ -n $id ]] || return 1
    gh run rerun "$id" --failed >/dev/null 2>&1 || return 2
    echo "$id"; return 0
  fi
  local repo runs id
  repo=$(forge_repo) || return 2
  runs=$(forge_api GET "repos/$repo/actions/runs?limit=50" 2>/dev/null) || return 2
  id=$(jq -r --arg s "$1" '[.workflow_runs[]? | select(.head_sha == $s and .status == "completed")]
    | sort_by(.id) | last | select(.conclusion == "failure") | .id // empty' <<<"$runs") || return 2
  [[ -n $id ]] || return 1
  forge_api POST "repos/$repo/actions/runs/$id/rerun" >/dev/null 2>&1 || return 2
  echo "$id"; return 0
}

# Age in whole hours of the oldest still-running Actions run for SHA, or
# nothing when no run is pending (or the age is unknowable). Lets the
# supervisor tell "running" from "hung": a pending run older than the
# threshold is stuck, not slow. Never fails the caller (exit 0; empty = n/a).
forge_oldest_pending_age() {
  local oldest=""
  if [[ $(ci_forge) == github ]]; then
    local runs
    runs=$(gh run list --commit "$1" --json databaseId,status,createdAt 2>/dev/null) || return 0
    oldest=$(jq -r '[.[] | select(.status != "completed" and .status != "cancelled" and .status != "canceled") | .createdAt] | sort | first // empty' <<<"$runs" 2>/dev/null) || return 0
  else
    local repo runs
    repo=$(forge_repo) || return 0
    runs=$(forge_api GET "repos/$repo/actions/runs?limit=50" 2>/dev/null) || return 0
    oldest=$(jq -r --arg s "$1" '[.workflow_runs[]? | select(.head_sha == $s and .status != "completed" and .status != "cancelled" and .status != "canceled") | .created_at] | sort | first // empty' <<<"$runs" 2>/dev/null) || return 0
  fi
  [[ -n $oldest && $oldest != null ]] || return 0
  age_hours "$oldest" 2>/dev/null || return 0
}

# API base (scheme://host[:port]) for the push-remote (https for ssh/scp forms).
# GITEA_URL overrides the derivation (ssh remotes, non-https schemes, custom ports).
forge_instance() {
  if [[ -n ${GITEA_URL:-} ]]; then printf '%s' "${GITEA_URL%/}"; return; fi
  local url
  url=$(git remote get-url "$(ci_push_remote)" 2>/dev/null) || return 1
  sed -E -e 's#(https?://[^/]+)/.*#\1#' -e 's#^ssh://git@([^/]+)/.*#https://\1#' -e 's#^git@([^:]+):.*#https://\1#' <<<"$url"
}

# Full PR view for BRANCH in gh vocabulary (both forges): number, state
# (OPEN|MERGED|CLOSED), mergeable, mergeStateStatus, autoMergeRequest,
# url, mergedAt, headRefOid, labels ([names]). Gitea behindness is
# API-computed (merge_base vs base tip); auto-merge state is not exposed
# there, so autoMergeRequest is always null (re-arm is idempotent).
forge_pr_view() {
  if [[ $(ci_forge) == github ]]; then
    local out
    out=$(gh pr view "$1" --json number,state,mergeable,mergeStateStatus,autoMergeRequest,url,mergedAt,headRefOid,labels 2>/dev/null) || return 1
    [[ -n $out ]] || return 1
    jq '.labels = [.labels[]?.name]' <<<"$out"
    return
  fi
  local repo pr base_ref tip mb behind=false
  repo=$(forge_repo) || return 1
  pr=$(forge_gitea_pages "repos/$repo/pulls?state=all" 2>/dev/null \
    | jq --arg b "$1" '[.[] | select(.head.ref == $b)] | first // empty') || return 1
  [[ -n $pr ]] || return 1
  base_ref=$(jq -r .base.ref <<<"$pr")
  tip=$(forge_api GET "repos/$repo/branches/$base_ref" 2>/dev/null | jq -r .commit.sha 2>/dev/null)
  mb=$(jq -r .merge_base <<<"$pr")
  [[ -n $tip && $tip != null && -n $mb && $mb != "$tip" ]] && behind=true
  jq -n --argjson pr "$pr" --arg behind "$behind" '{
    number: $pr.number,
    state: (if $pr.merged then "MERGED" elif $pr.state == "open" then "OPEN" else "CLOSED" end),
    mergeable: (if $pr.mergeable == true then "MERGEABLE" elif $pr.mergeable == false then "CONFLICTING" else "UNKNOWN" end),
    mergeStateStatus: (if $pr.merged then "MERGED" elif $pr.mergeable == false then "DIRTY" elif $behind == "true" then "BEHIND" elif $pr.mergeable == true then "CLEAN" else "UNKNOWN" end),
    autoMergeRequest: null,
    url: $pr.html_url,
    mergedAt: $pr.merged_at,
    headRefOid: $pr.head.sha,
    labels: ($pr.labels // [] | map(.name))
  }'
}

# Issue text for review requirements: "Issue #N: title\n\nbody".
forge_issue_text() {
  if [[ $(ci_forge) == github ]]; then
    gh issue view "$1" --json number,title,body --jq '"Issue #\(.number): \(.title)\n\n\(.body)"' 2>/dev/null
    return
  fi
  local repo
  repo=$(forge_repo) || return 1
  forge_api GET "repos/$repo/issues/$1" 2>/dev/null \
    | jq -r '"Issue #\(.number): \(.title)\n\n\(.body)"'
}

# Base64 file content for REPO/PATH (fleet checks across repos on one instance).
forge_contents() {
  if [[ $(ci_forge) == github ]]; then
    gh api "repos/$1/contents/$2" 2>/dev/null | jq -r .content
    return
  fi
  forge_api GET "repos/$1/contents/$2" 2>/dev/null | jq -r .content
}
