---
id: doc-platform-docs-08-cross-platform-observability
title: Cross-Platform Infrastructure Wiring, OTLP Observability, Alert Matrix & Storage
kind: integration-guide
domain: platform-docs
namespace: ns:platform
status: active
autonomy_tier: green
owner: platform-team
consumers:
  - platform-observability-monitoring
  - platform-openbao
  - platform-litellm-laya
  - platform-paperclip
  - platform-chatwoot
  - aidoc-flow-support-desk
  - platform-stage-ops
tags:
  - observability
  - otlp
  - clickhouse
  - hyperdx
  - langfuse
  - alerts
  - oci-object-storage
  - cloudflare-r2
endpoints:
  host_loopback: "http://127.0.0.1:8005"
  container_gateway: "http://10.250.0.1:8000"
watched_paths:
  - "docs/doc-tools/08_CROSS_PLATFORM_INFRASTRUCTURE_AND_OBSERVABILITY.md"
last_verified: 2026-11-06
---

# Cross-Platform Infrastructure Wiring, OTLP Observability, Alert Matrix & Storage

## 1. Cross-Platform Ecosystem Integration Matrix

`platform-docs` integrates directly with every shared service in the platform topology (`09-authoritative-platform-technology-stack.md` v2.0) without introducing redundant databases, caches, or sidecars:

| Platform Repository | Integration Role with `platform-docs` | Protocol / Endpoint |
| :--- | :--- | :--- |
| **`aiops-harness`** | Invokes `platform-docs lint`, `check-drift`, and `export-llms` during `gate check`/`ci`, and executes warrant-gated runbook steps via `docs_slice_runbook`. | In-process Python CLI / Unix socket `/run/platform-docs/mcp.sock` |
| **`aidoc-context-tools`** | Shares the embedded **Kùzu** graph database (`DocumentNode`, `RunbookStepNode`, `AlertNode` joined to `ArtifactNode`) and exposes unified FastMCP tools. | Embedded Kùzu C++/Python bindings + FastMCP |
| **`aidoc-flow-engramory`** | Stores heading-prefixed semantic chunks (`vector(1536)`) and BM25 `tsvector` indexes in shared PostgreSQL 18 (`engramory` DB) for hybrid RRF retrieval. | PgBouncer (`10.250.0.1:6432`) + `engramory` CLI/API |
| **`platform-security`** | Enforces Authentik 2-Axis RBAC, disjoint namespaces (`ns:platform`, `ns:aidoc`, `ns:b-local`), Cedar policies, Ed25519 `X-Agent-Warrant`, Rule D14 scrubbing, and CaMeL fencing. | `/run/platform-security/gate.sock` & `10.250.0.1:8095` |
| **`platform-paperclip` & `aiops-pm`** | Receives `xp.platform.docs.drift.v1` and `stale.v1` events to schedule bounded `DOC2C`/`CODE2S` tasks (`$1.50` cap) and manages human `consent_id` approvals for `yellow` runbooks. | NATS JetStream (`10.250.0.1:4222`) & Paperclip API (`10.250.0.1:3100`) |
| **`aidoc-flow-support-desk` & `platform-chatwoot`** | Queries `docs_slice_runbook(alert_id=...)` during autonomous incident triage and routes `ALT-DOC-001`–`004` operational alerts to Account 3 (`AIOps Platform`). | FastMCP + HTTP (`10.250.0.1:8090` / `:3000`) |
| **`platform-litellm-laya`** | Generates `vector(1536)` embeddings for Engramory doc chunks and routes `DOC2C` agent completions with real-time task budget enforcement. | `http://10.250.0.1:4000` (LiteLLM proxy) |
| **`platform-openbao`** | Injects dynamic PostgreSQL credentials, NATS NKeys, and OCI S3 credentials to `/dev/shm/openbao-env.sh` via Path C `docs-bao-agent` sidecar (`kv/stage/platform-docs/app`). | `http://10.250.0.1:8200` (`auth/jwt-agents` & AppRole) |
| **`platform-edge-gateway`** | Publishes the Starlight + Scalar portal at `https://docs.int.aiops-flow.io` strictly over the NetBird WireGuard mesh (`corridor-proxy-stage`) with Authentik OIDC ForwardAuth. | Traefik v3 (`10.250.0.1:443`) |
| **`platform-data-contracts`** | Supplies Proto3 schemas, CloudEvents envelopes, and OpenAPI 3.1 specs rendered interactively by **Scalar** inside the `platform-docs` portal. | Git submodule / wheel artifact |

---

## 2. Outbound Telemetry & Observability (`platform-observability-monitoring`)

In compliance with `SPOKE-INTEGRATION.md`, `platform-docs` emits secret-redacted OTLP spans, metrics, and structured JSON logs to the central OTel Collector (`10.250.0.1:14318` HTTP / `:14317` gRPC) backed by ClickHouse (`hyperdx` and `langfuse` databases):

### 2.1 Key OTel Metrics & Spans Emitted

| Metric / Span Name | Type | Attributes (`service.name="platform-docs"`) | Purpose |
| :--- | :--- | :--- | :--- |
| `docs.lint.duration_ms` | Histogram | `repo`, `gate_tier` (`1`\|`2`\|`3`), `status` (`pass`\|`fail`), `rule_id` | Tracks Tier-1 `<1s` offline lint budget compliance across all spokes. |
| `docs.drift.detected_total` | Counter | `repo`, `doc_id`, `drift_kind` (`WATCHED_PATH`\|`TTL_STALE`\|`CLI_MISMATCH`) | Measures code-to-doc drift frequency per repository. |
| `docs.runbook.step_execution` | Span | `doc_id`, `step_id`, `tier`, `mode`, `exit_code`, `warrant_id`, `consent_id` | Full audit trail of every runbook probe or remediation step executed by an agent. |
| `docs.mcp.slice_latency_ms` | Histogram | `tool_name`, `query_kind` (`alert`\|`service`\|`hybrid`), `tokens_returned` | Verifies `<1ms` Kùzu graph slicing and token compression efficiency. |
| `docs.smoke.probe_status` | Gauge | `repo`, `doc_id`, `step_id` (`1` = pass, `0` = fail) | Tracks live staging health of `tier="green"` runbook probes. |

---

## 3. Production Alert Rules Matrix (`ALT-DOC-001` – `ALT-DOC-004`)

The following alert rules extend `ALERTS-AND-RUNBOOKS.md` in HyperDX and route via `platform-chatwoot` Account 3 (`AIOps Platform`) to `aidoc-flow-support-desk`:

| Alert ID | Alert Name | ClickHouse / HyperDX Condition | Severity | Automated Triage & Runbook Action |
| :--- | :--- | :--- | :--- | :--- |
| **`ALT-DOC-001`** | Unresolved Code-to-Doc Drift Backlog | `count(docs.drift.detected_total WHERE resolved=false) > 10` over 24h | **Warning** | `aiops-pm` prioritizes pending `DOC2C` tasks in `platform-paperclip` and dispatches `aiops-dev`. |
| **`ALT-DOC-002`** | Green Runbook Smoke Probe Failure | `count(otel_logs WHERE ServiceName='platform-docs' AND Body LIKE '%DOC-SMOKE-FAIL%') > 0` | **High** | `aidoc-flow-support-desk` slices the failing `doc_id#step_id` to determine whether the stage service is unhealthy or the runbook CLI syntax drifted. |
| **`ALT-DOC-003`** | Unauthorized Yellow/Red Runbook Attempt | `count(otel_logs WHERE ServiceName='platform-docs' AND Body LIKE '%CEDAR_DENY_RUNBOOK_STEP%') > 0` | **Critical** | Escalates immediately to Security Ops (`ns:security`) and evaluates `RevocationRegistry` on the offending warrant. |
| **`ALT-DOC-004`** | `platform-docs` Service or MCP Unhealthy | `http://10.250.0.1:8000/healthz != 200` for 2m | **High** | Triggers `probe-platform-docs-health` and `remediate-restart-platform-docs` in [`docs/07_PORTAL_API_CONTRACTS_AND_DEPLOYMENT_RUNBOOK.md`](07_PORTAL_API_CONTRACTS_AND_DEPLOYMENT_RUNBOOK.md#31-platform-docs-service--kùzu-index-health-check-alt-doc-004). |

---

## 4. Object Storage & 3-Tier Backup Strategy (OCI Object Storage + Cloudflare R2 + POSIX/ZFS)

In strict compliance with `25-approved-tooling-inventory.md` (`seaweedfs` retired) and `28-backup-and-disaster-recovery-strategy.md`:

1. **Online Stage/Prod Storage (OCI Object Storage — `stage-docs-bundles`):**
   Stores versioned Starlight + Scalar static portal builds, compiled `/llms-full.txt` bundles, and Kùzu property graph checkpoints (`*.kuzu.tar.zst`). Offline unit tests in `platform-docs` use an in-memory/filesystem mock (`LocalFsBackend`).
2. **Tier B1 Off-Site Encrypted Backup (Cloudflare R2):**
   Nightly `pgBackRest` backups of the shared PostgreSQL 18 `engramory` L0 knowledge base and Kùzu graph snapshots are replicated to Cloudflare R2 (`r2://aiops-stage-backups/platform-docs/`).
3. **Tier B2 Air-Gapped Offline Backup (Local POSIX/ZFS):**
   Pulled from Cloudflare R2 via `rclone sync` onto the air-gapped recovery host's ZFS dataset with immutable read-only snapshots.
