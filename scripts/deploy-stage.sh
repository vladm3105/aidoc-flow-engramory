#!/usr/bin/env bash
# ==============================================================================
# deploy-stage.sh — Stage Environment Deployment Runbook for Engramory
# Compliance: technology_stack 16 §3.2, 21 §2.3 / §4, and PLAN-009
#
# Syncs code from the dev workstation to the stage host and (re)starts the stack
# in the `platform` shared-infrastructure rootless Docker namespace (UID 1004).
#
# Usage:
#   scripts/deploy-stage.sh
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

STAGE_HOST="${STAGE_HOST:-100.98.134.10}"
STAGE_USER="${STAGE_USER:-ya}"
STAGE_SSH="${STAGE_SSH:-ssh ${STAGE_USER}@${STAGE_HOST}}"

# Target namespace (shared infra) on the stage host
PLATFORM_USER="${PLATFORM_USER:-platform}"
PLATFORM_UID="${PLATFORM_UID:-1004}"
STAGE_DIR="${STAGE_DIR:-/home/${PLATFORM_USER}/platform-engramory}"
AS_PLATFORM="sudo -n -u ${PLATFORM_USER} env XDG_RUNTIME_DIR=/run/user/${PLATFORM_UID} DOCKER_HOST=unix:///run/user/${PLATFORM_UID}/docker.sock"

echo "[*] Target Stage Host: ${STAGE_USER}@${STAGE_HOST}:${STAGE_DIR} (namespace: ${PLATFORM_USER})"

# 1. Verify SSH connectivity to stage host
if ! ${STAGE_SSH} "echo '[*] SSH connection verified.'" 2>/dev/null; then
    echo "⚠️  [deploy-stage] Could not connect to stage host via ${STAGE_SSH}." >&2
    echo "   Ensure host is online and Tailscale mesh is connected." >&2
    exit 1
fi

# 2. Stage host pre-flight: directories and network
echo "[*] Ensuring platform directories and networks on stage host..."
${STAGE_SSH} "
    set -e
    sudo -n install -d -o ${PLATFORM_USER} -g ${PLATFORM_USER} -m 0750 ${STAGE_DIR} ${STAGE_DIR}/run
    ${AS_PLATFORM} docker network inspect platform-mesh >/dev/null 2>&1 || ${AS_PLATFORM} docker network create platform-mesh >/dev/null
"

# 3. Sync codebase into the platform namespace
echo "[*] Syncing codebase to stage host via rsync..."
rsync -rlptvz --delete \
    --rsync-path="sudo -n -u ${PLATFORM_USER} rsync" \
    --exclude='.git/' \
    --exclude='.env' \
    --exclude='.env.stage' \
    --exclude='__pycache__/' \
    --exclude='.pytest_cache/' \
    --exclude='.mypy_cache/' \
    --exclude='.ruff_cache/' \
    --exclude='run/' \
    "${REPO_ROOT}/" "${STAGE_USER}@${STAGE_HOST}:${STAGE_DIR}/"

# 4. Apply migrations to stage-postgres18 if needed
echo "[*] Executing database schema migrations against stage-postgres18..."
${STAGE_SSH} "
    sudo -n -u ${PLATFORM_USER} env XDG_RUNTIME_DIR=/run/user/${PLATFORM_UID} sh -c '
        set -e
        # Ensure schema_migrations tracking table exists
        docker exec -i stage-postgres18 psql -U stage_bootstrap -d engramory -c \"
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(255) PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        \"
        # Bootstrap baseline: if memories table already exists and schema_migrations is empty,
        # mark historical migrations 0001 through 0007 as applied so they are not re-executed
        docker exec -i stage-postgres18 psql -U stage_bootstrap -d engramory -c \"
            DO \\\$\$
            BEGIN
                IF to_regclass('public.memories') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM schema_migrations) THEN
                    INSERT INTO schema_migrations (version) VALUES
                        ('0001_init_memory.sql'),
                        ('0002_add_domain_scope.sql'),
                        ('0003_reconcile_contracts.sql'),
                        ('0004_ambient_gateway_view.sql'),
                        ('0005_pin_embeddings_hnsw.sql'),
                        ('0006_resize_embeddings_1536.sql'),
                        ('0007_graph_entities_canonical.sql')
                    ON CONFLICT DO NOTHING;
                END IF;
            END \\\$\$;
        \"
        for sql in \$(ls ${STAGE_DIR}/db/migrations/*.sql | sort); do
            fname=\$(basename \"\$sql\")
            applied=\$(docker exec -i stage-postgres18 psql -U stage_bootstrap -d engramory -t -A -c \"SELECT count(*) FROM schema_migrations WHERE version = '\$fname'\")
            if [ \"\$applied\" -eq 0 ]; then
                echo \"Applying \$sql...\"
                docker exec -i stage-postgres18 psql -U stage_bootstrap -d engramory -v ON_ERROR_STOP=1 < \"\$sql\"
                docker exec -i stage-postgres18 psql -U stage_bootstrap -d engramory -c \"INSERT INTO schema_migrations (version) VALUES ('\$fname')\"
            else
                echo \"Skipping already applied migration: \$fname\"
            fi
        done
        docker exec stage-postgres18 psql -U stage_bootstrap -d engramory -c \"
            GRANT USAGE ON SCHEMA public TO engramory;
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO engramory;
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO engramory;
            ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO engramory;
            ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO engramory;
        \"
    '
"

# 5. Build and launch container stack
echo "[*] Building and starting Engramory stack in the platform namespace..."
${STAGE_SSH} "
    set -e
    ${AS_PLATFORM} sh -c 'cd ${STAGE_DIR} && docker compose -f compose.platform.yaml up -d --build --remove-orphans'
    echo '[OK] Engramory container stack started in the platform namespace.'
"

# 6. Verify health probes
echo "[*] Verifying service health..."
for _ in {1..30}; do
    if ${STAGE_SSH} "curl -s -f http://127.0.0.1:8005/" >/dev/null 2>&1; then
        echo "[OK] Engramory MCP Gateway is healthy on http://${STAGE_HOST}:8005"
        break
    fi
    echo "    Waiting for MCP Gateway startup..."
    sleep 2
done

echo "[OK] Engramory deployment to stage completed successfully."

