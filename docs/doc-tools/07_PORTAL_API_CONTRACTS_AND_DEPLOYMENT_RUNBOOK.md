---
id: doc-platform-docs-07-portal-runbook
title: Multi-Repo Starlight & Scalar Portal, Deployment Architecture & Operations Runbook
kind: runbook
domain: platform-docs
namespace: ns:platform
status: active
autonomy_tier: yellow
owner: platform-team
consumers:
  - aiops-harness
  - aidoc-flow-support-desk
  - platform-stage-ops
  - aiops-dev
  - aiops-sec
tags:
  - starlight
  - scalar
  - mermaid
  - deployment-runbook
  - openbao-sidecar
  - rootless-docker
triggers:
  alerts:
    - ALT-DOC-001
    - ALT-DOC-002
    - ALT-DOC-003
    - ALT-DOC-004
  cli_commands:
    - "platform-docs health"
    - "platform-docs lint"
    - "platform-docs export-llms"
    - "platform-docs build-portal"
  mcp_tools:
    - "docs_slice_runbook"
    - "docs_validate_file"
    - "docs_check_drift"
    - "docs_export_llms"
endpoints:
  host_loopback: "http://127.0.0.1:8005"
  container_gateway: "http://10.250.0.1:8000"
  internal_fqdn: "https://docs.int.aiops-flow.io"
  mcp_socket: "/run/platform-docs/mcp.sock"
watched_paths:
  - "compose.platform.yaml"
  - "docs/doc-tools/07_PORTAL_API_CONTRACTS_AND_DEPLOYMENT_RUNBOOK.md"
last_verified: 2026-11-06
---

# Multi-Repo Starlight & Scalar Portal, Deployment Architecture & Operations Runbook

## 1. 100% Open-Source Portal & API Console Architecture (`Starlight` + `Scalar`)

While autonomous agents consume documentation primarily via **FastMCP (`docs_*`)**, **Embedded Kùzu**, **Engramory**, and **`/llms.txt`**, human engineers and architects require a fast, unified, searchable developer portal (matching Google's internal `g3doc` and Anthropic's Mintlify experience without proprietary SaaS or open-core paywalls).

`platform-docs` builds the unified portal using two **100% MIT-licensed** components running on the platform's approved Node.js 24 LTS (`pnpm`) + Python 3.15 (`uv`) toolchain:

1. **Astro Starlight (`@astrojs/starlight`, MIT License):**
   - Aggregates `docs/*.md`, `README.md`, `AGENTS.md`, and compiled Tier-3 SDD projections (`sdd/*`) across all 35+ repositories into a hierarchical sidebar organized by namespace (`ns:platform`, `ns:aidoc`, `ns:b-local`).
   - Emits zero-JS static HTML by default alongside raw `.md` routes (`https://docs.int.aiops-flow.io/<repo>/docs/<file>.md`) and aggregated `/llms.txt` / `/llms-full.txt` manifests.
   - Renders native **Mermaid v11** architecture diagrams, GitHub-style alerts (`[!NOTE]`, `[!IMPORTANT]`, `[!WARNING]`), and displays visual badges for each runbook step's `tier` (`🟢 Green`, `🟡 Yellow`, `🔴 Red`) and `last_verified` date.
2. **Scalar (`@scalar/api-reference`, MIT License):**
   - Embedded at `/contracts/<service>` inside the portal.
   - Renders interactive OpenAPI 3.1 specifications, JSONSchema contracts, FastMCP tool catalogs, and Protobuf HTTP transcoding specs compiled from `platform-data-contracts`.

---

## 2. Rootless Stage Deployment Topology (`ns:platform`, `UID 1004`)

`platform-docs` deploys on the shared stage host in the `ns:platform` compartment (`UID 1004:1004`), isolated behind `platform-edge-gateway` (Traefik v3 + Authentik OIDC ForwardAuth over NetBird mesh) with secrets injected to tmpfs (`/dev/shm/openbao-env.sh`) via the Path C OpenBao Agent sidecar (`docs-bao-agent`).

```yaml
# Reference compose.yaml excerpt for platform-docs stage service
services:
  platform-docs:
    image: platform-docs:stage-local # Built locally from digest-pinned debian:trixie-slim base
    user: "1004:1004"
    read_only: true
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    ports:
      - "127.0.0.1:8005:8000"
      - "10.250.0.1:8000:8000"
    volumes:
      - docs-shm:/dev/shm:ro
      - docs-run:/run/platform-docs:rw
      - docs-kuzu-data:/var/lib/platform-docs/kuzu:rw
    environment:
      - PLATFORM_DOCS_BIND=0.0.0.0:8000
      - PLATFORM_DOCS_KUZU_PATH=/var/lib/platform-docs/kuzu
      - PLATFORM_SECURITY_GATE_URL=http://10.250.0.1:8095
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://10.250.0.1:14318
    networks:
      - platform-mesh
```

---

## 3. Executable Operational Runbooks for `platform-docs`

Every procedure below follows the `platform-docs` chunk-independence and annotated code-block specification (`{id, tier, mode, expect_exit}`) so `aiops-harness` and `aidoc-flow-support-desk` can execute them deterministically.

### 3.1 `platform-docs` Service & Kùzu Index Health Check (`ALT-DOC-004`)

When diagnosing `ALT-DOC-004` (*Platform Docs Service Unhealthy or MCP Socket Down*) on `platform-docs`, run the read-only health and graph status probe against the stage loopback endpoint (`127.0.0.1:8005`):

```bash {id="probe-platform-docs-health" tier="green" mode="read-only" expect_exit=0 timeout_sec=10 service="platform-docs"}
xh --check-status GET http://127.0.0.1:8005/healthz
```

Verify that the embedded Kùzu runbook graph and `/llms.txt` index are loaded and report zero schema errors:

```bash {id="probe-platform-docs-graph-status" tier="green" mode="read-only" expect_exit=0 timeout_sec=15 service="platform-docs"}
uv run platform-docs status --endpoint http://127.0.0.1:8005 --json
```

### 3.2 Run Tier-1 Offline Lint & Registry Verification Across a Spoke Repository

When validating documentation changes in any `platform-*`, `aidoc-*`, or `aiops-*` repository before commit, execute the offline Tier-1 gate (`< 1s`):

```bash {id="probe-spoke-docs-lint" tier="green" mode="read-only" expect_exit=0 timeout_sec=10 service="platform-docs"}
uv run platform-docs lint --target . --verify-registry --json
```

### 3.3 Compile `/llms.txt`, `/llms-full.txt` & Sync Embedded Kùzu Graph

When documentation or SDD specs (`sdd/*`) are updated in a repository and `/llms.txt` or the Kùzu runbook graph needs regeneration (`ALT-DOC-001` / `DOC-DRIFT-001`), execute the export and graph sync command (requires `yellow` warrant when writing to shared stage storage):

```bash {id="remediate-export-llms-and-kuzu" tier="yellow" mode="mutating" expect_exit=0 timeout_sec=60 requires_warrant="true" service="platform-docs"}
uv run platform-docs export-llms --target . --write --json && \
  uv run platform-docs sync-graph --target . --json
```

### 3.4 Rebuild Multi-Repo Starlight & Scalar Portal Bundle

When promoting updated documentation bundles across all repositories to the stage portal (`https://docs.int.aiops-flow.io` / `stage-docs-bundles` OCI bucket), execute the multi-repo portal build and atomic swap:

```bash {id="remediate-rebuild-portal-bundle" tier="yellow" mode="mutating" expect_exit=0 timeout_sec=180 requires_warrant="true" service="platform-docs"}
uv run platform-docs build-portal --workspace $WORKSPACE_ROOT --upload-oci stage-docs-bundles --json
```

### 3.5 Restart `platform-docs` Container & OpenBao Sidecar Recovery

If `probe-platform-docs-health` reports HTTP 5xx or stale OpenBao database/NATS credentials in `/dev/shm/openbao-env.sh`, restart the `docs-bao-agent` sidecar and `platform-docs` container on the stage host:

```bash {id="remediate-restart-platform-docs" tier="yellow" mode="mutating" expect_exit=0 timeout_sec=45 requires_warrant="true" service="platform-docs"}
docker compose -f compose.platform.yaml restart docs-bao-agent platform-docs && \
  xh --check-status GET http://127.0.0.1:8005/healthz
```
