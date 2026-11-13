# Engramory Observability & Coroot Integration Guide

This document defines the observability architecture, telemetry pipelines, and Coroot APM integration for **Engramory** across local development, stage, and production environments.

Authoritative cross-repository specification:

- `aidoc-observability-monitoring/docs/ENGRAMORY-OBSERVABILITY-INTEGRATION.md`

---

## 1. Observability Architecture (3-Tier Model)

Engramory adheres to the standard platform three-tier observability model:

```text
                                  +---------------------------------------+
                                  |         AI Agents / Clients           |
                                  |  (Cursor, CLI, Framework, Workflows)  |
                                  +-------------------+-------------------+
                                                      |
                                                      | JSON-RPC (HTTP/SSE :8000)
                                                      v
     +---------------------------------------------------------------------------------------------------------+
     | Tier 1: Coroot Community APM (eBPF Kernel Inspection)                                                  |
     | - Automatic TCP tracing & RED metrics (Rate, Errors, Duration)                                          |
     | - PostgreSQL statement latency, connection pool saturation, locks                                       |
     | - Neo4j Bolt protocol latency & connection health                                                       |
     | - Host & Cgroup CPU/Memory/OOM tracking                                                                 |
     +---------------------------------------------------------------------------------------------------------+
                                                      |
                   +----------------------------------+----------------------------------+
                   |                                                                     |
                   v                                                                     v
+------------------------------------+                                +------------------------------------+
| Tier 2: OpenTelemetry Tracing     |                                | Tier 3: GenAI Tracing (Langfuse)   |
| (OTel Collector -> ClickHouse)     |                                | (LiteLLM Proxy Sidecar)            |
| - Distributed spans (MCP calls)    |                                | - Prompt / Completion token usage  |
| - Worker reflection cycles         |                                | - Distillation model latency/costs |
| - Unified stdout/stderr log stream |                                | - Per-tenant/project LLM telemetry |
+------------------------------------+                                +------------------------------------+
```

| Tier | Component | Technology | Primary Role |
|---|---|---|---|
| **1. Zero-Code APM** | Coroot Community 1.27.4 | eBPF (`coroot-node-agent`) | Automatic dependency graph, RED metrics, Postgres query times, cgroup health. |
| **2. Distributed Tracing** | HyperDX / ClickHouse | OpenTelemetry Collector Contrib | Detailed span waterfalls, distributed trace context, log aggregation. |
| **3. GenAI Tracing** | Langfuse 3.x | LiteLLM Integration | Prompt auditing, token tracking, embedding latencies, model costs. |

---

## 2. Container Standardization & Coroot Discovery

Coroot Community APM discovers and classifies services dynamically through eBPF probes attached to Linux kernel network sockets (`tcp_sendmsg`, `tcp_recvmsg`) and cgroups.

To ensure unambiguous service topology maps in the Coroot UI (`http://100.98.134.10:18085/`), all container names are standardized:

| Container Name | Service | Default Port | Coroot Inspection Capability |
|---|---|---|---|
| `engramory-mcp` | MCP Gateway (FastAPI / SSE) | `8000` | Inbound HTTP/SSE throughput, error rates (5xx), latency percentiles (p50/p95/p99). |
| `engramory-postgres` | Canonical Distilled Storage (`pgvector`) | `5432` | Top slow queries, table locks, connection pool exhaustion, index scans. |
| `engramory-neo4j` | Knowledge Graph Projection | `7687` (Bolt), `7474` (HTTP) | Cypher query latency, heap/pagecache utilization, Bolt connection lifetime. |
| `engramory-redis` | Ephemeral Caching & Locks | `6379` | Command latency, key hit rates, connection counts. |
| `engramory-worker` | Distillation & Reflection Daemon | N/A (background) | Background CPU/Memory cgroup consumption, database query throughput. |

---

## 3. Telemetry Overlay (`docker-compose.telemetry-net.yml`)

Engramory services can join the shared platform telemetry network using the compose overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.telemetry-net.yml up -d
```

### Overlay Configuration

```yaml
services:
  mcp:
    environment:
      - OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_EXPORTER_OTLP_ENDPOINT:-http://aidoc-otel-collector:4318}
      - OTEL_SERVICE_NAME=engramory-mcp
    networks:
      - default
      - telemetry

  worker:
    environment:
      - OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_EXPORTER_OTLP_ENDPOINT:-http://aidoc-otel-collector:4318}
      - OTEL_SERVICE_NAME=engramory-worker
    networks:
      - default
      - telemetry

networks:
  telemetry:
    external: true
    name: "${AIDOC_TELEMETRY_NETWORK:-aidoc-platform_aidoc_telemetry-net}"
```

---

## 4. Verification & Health Endpoints

### 4.1 Local Development Checks

Verify all components are responsive:

```bash
# 1. Check MCP server health (returns 200 OK)
curl -s http://localhost:8000/ | jq .
# Expected: {"status": "ok", "service": "engramory-mcp", "version": "0.1.0"}

# 2. Check PostgreSQL health
pg_isready -h localhost -p 5432 -U engramory

# 3. Check Neo4j HTTP endpoint
curl -s -I http://localhost:7474 | grep "HTTP"
```

### 4.2 Stage Telemetry Stack Status

The platform observability endpoints run on the Stage host (`100.98.134.10`):

- **Coroot APM**: `http://100.98.134.10:18085/`
- **ClickHouse HTTP**: `http://100.98.134.10:18123/ping`
- **OTel Collector Health**: `http://100.98.134.10:13133/`
- **Langfuse Web UI**: `http://100.98.134.10:13000/`

---

## 5. Golden Signals & Alerting Thresholds

| Metric | Target / SLO | Coroot / HyperDX Alert Trigger | Severity |
|---|---|---|---|
| **MCP Availability** | $\ge 99.9\%$ uptime | Ingress error rate $> 1.0\%$ for 3 min | Warning |
| **MCP Latency (p95)** | $\le 200\text{ ms}$ | Latency $> 500\text{ ms}$ for 5 min | Warning |
| **PostgreSQL Latency (p95)**| $\le 25\text{ ms}$ | Average query latency $> 100\text{ ms}$ | Warning |
| **PostgreSQL Pool Saturation** | $\le 80\%$ connections | Active connections $> 90\%$ max | Critical |
| **Neo4j Cypher Latency (p95)** | $\le 50\text{ ms}$ | Query latency $> 250\text{ ms}$ | Warning |
| **Cgroup OOM Kills** | 0 events | Container killed due to OOM (`exit 137`) | Critical |
