#!/usr/bin/env bash
# backend.sh — lifecycle of the stack under test (called by `uts backend …` and layer preflights).
#
#   backend.sh up      start compose (managed only) + wait for health (always)
#   backend.sh down    stop compose (managed only)
#   backend.sh reset   run the project's DB reset hook (managed only; refuses on unmanaged stacks)
#
# Inputs (exported by `uts` from unified-test-suite.config.json; exported env always wins):
#   BACKEND_URL, APP_URL, BACKEND_HEALTH_PATH, BACKEND_WAIT_SECONDS
#   UTS_COMPOSE_FILES   space-separated compose files (stack.compose.files); empty = no managed stack
#   COMPOSE_ENV_FILE    env file for compose ONLY (stack.compose.envFile; never set in CI)
#   UTS_DB_RESET        reset command (stack.dbReset); empty = warn
#   BACKEND_MANAGED     1 = this machine owns the stack, 0 = someone else does (staging).
#                       Default: 1 iff BACKEND_URL is loopback AND compose files are configured.
# Exit 3 = infrastructure problem (`uts layer` records the layer as infra_error).
set -o pipefail   # no -u: macOS bash 3.2 treats empty arrays as unbound

BACKEND_URL="${BACKEND_URL:-}"
APP_URL="${APP_URL:-}"
HEALTH_PATH="${BACKEND_HEALTH_PATH:-/health}"
WAIT_SECONDS="${BACKEND_WAIT_SECONDS:-120}"
read -r -a FILES <<<"${UTS_COMPOSE_FILES:-}"

host="${BACKEND_URL#*://}"; host="${host%%/*}"; host="${host%:*}"
case "$host" in 127.*|localhost|"[::1]"|"[::1") loopback=1 ;; *) loopback=0 ;; esac
default_managed=0; [[ $loopback == 1 && ${#FILES[@]} -gt 0 ]] && default_managed=1
MANAGED="${BACKEND_MANAGED:-$default_managed}"

warn() { echo "WARN: $*" >&2; [[ -n "${LAYER_WARN_FILE:-}" ]] && echo "$*" >>"$LAYER_WARN_FILE"; return 0; }
infra() { echo "INFRA: $*" >&2; exit 3; }

compose() {
  [[ ${#FILES[@]} -gt 0 ]] || infra "BACKEND_MANAGED=1 but stack.compose.files is not configured"
  command -v docker >/dev/null 2>&1 || infra "docker not found but BACKEND_MANAGED=1 (set BACKEND_MANAGED=0 to target an existing stack)"
  docker info >/dev/null 2>&1 || infra "docker daemon not reachable"
  local args=()
  for f in "${FILES[@]}"; do args+=(-f "$f"); done
  if [[ -n "${COMPOSE_ENV_FILE:-}" ]]; then
    [[ -f "$COMPOSE_ENV_FILE" ]] || infra "COMPOSE_ENV_FILE=$COMPOSE_ENV_FILE does not exist"
    args=(--env-file "$COMPOSE_ENV_FILE" "${args[@]}")
  fi
  docker compose "${args[@]}" "$@"
}

wait_for() { # url label
  local url="$1" label="$2" deadline=$(( $(date +%s) + WAIT_SECONDS ))
  until curl -fsS -o /dev/null --max-time 5 "$url"; do
    (( $(date +%s) >= deadline )) && infra "$label not healthy at $url after ${WAIT_SECONDS}s"
    sleep 2
  done
  echo "backend.sh: $label healthy at $url"
}

[[ -n "$BACKEND_URL" || "${1:-up}" == "reset" ]] || { echo "INFRA: BACKEND_URL / stack.backendUrl is not set" >&2; exit 3; }

case "${1:-up}" in
  up)
    if [[ "$MANAGED" == "1" ]]; then
      echo "backend.sh: starting managed stack for $BACKEND_URL"
      compose up -d --wait --wait-timeout "$WAIT_SECONDS" || infra "docker compose up failed"
    else
      echo "backend.sh: unmanaged backend — health-checking $BACKEND_URL only"
    fi
    wait_for "${BACKEND_URL%/}${HEALTH_PATH}" "backend"
    if [[ "${WAIT_FOR_APP:-0}" == "1" ]]; then
      [[ -n "$APP_URL" ]] || infra "layer needsApp but stack.appUrl / APP_URL is not set"
      wait_for "$APP_URL" "app"
    fi
    exit 0
    ;;
  down)
    [[ "$MANAGED" == "1" ]] || { echo "backend.sh: unmanaged backend — nothing to stop"; exit 0; }
    compose down --remove-orphans
    ;;
  reset)
    if [[ "$MANAGED" != "1" ]]; then
      # Never wipe a shared/staging DB. Tests on unmanaged stacks namespace data by TEST_RUN_ID.
      warn "DB reset skipped: backend is unmanaged ($BACKEND_URL); relying on TEST_RUN_ID data namespacing"
      exit 0
    fi
    if [[ -n "${UTS_DB_RESET:-}" ]]; then
      bash -o pipefail -c "$UTS_DB_RESET" || infra "stack.dbReset failed: $UTS_DB_RESET"
    else
      warn "stack.dbReset not configured — state may leak between runs"
    fi
    ;;
  *) echo "usage: backend.sh up|down|reset" >&2; exit 2 ;;
esac
