---
id: doc-platform-docs-02-schema
title: Document Schema, Deterministic Frontmatter, Executable Runbooks & llms.txt Standard
kind: specification
domain: platform-docs
namespace: ns:platform
status: active
autonomy_tier: green
owner: platform-team
consumers:
  - aiops-harness
  - aidoc-context-tools
  - aidoc-flow-engramory
  - aidoc-flow-support-desk
  - aiops-dev
  - platform-security
tags:
  - frontmatter
  - schema
  - executable-runbooks
  - chunk-independence
  - llms-txt
endpoints:
  host_loopback: "http://127.0.0.1:8005"
  container_gateway: "http://10.250.0.1:8000"
watched_paths:
  - "docs/doc-tools/02_DOCUMENT_SCHEMA_FRONTMATTER_AND_EXECUTABLE_RUNBOOKS.md"
last_verified: 2026-11-06
---

# Document Schema, Deterministic Frontmatter, Executable Runbooks & `llms.txt` Standard

## 1. Canonical YAML Frontmatter Specification (`DocumentFrontmatter`)

Every Markdown file in `docs/*.md`, `README.md`, and `AGENTS.md` across all `platform-*`, `aidoc-*`, and `aiops-*` repositories MUST begin with a deterministic YAML frontmatter block delimited by `---`.

Legacy prose metadata blocks (such as `> **Document ID:** ...` or inline `**Status:** ...`) are deprecated and automatically migrated by `platform-docs migrate-frontmatter`.

### 1.1 Complete YAML Frontmatter Reference Example

```yaml
---
id: runbook-openbao-operations
title: OpenBao Target-State Operations, Health Checks & Break-Glass Runbook
kind: runbook # overview | index | architecture | specification | runbook | playbook | integration-guide | alert-catalog | agent-manifest | adr
domain: platform-openbao
namespace: ns:platform # ns:platform | ns:aidoc | ns:b-local
status: active # active | migrating | draft | deprecated
autonomy_tier: yellow # green | yellow | red (highest autonomy tier required by any step in the document)
owner: security-platform-team
consumers:
  - aiops-harness
  - aidoc-flow-support-desk
  - aiops-sec
tags:
  - openbao
  - secrets
  - ocikms
  - break-glass
triggers:
  alerts:
    - ALT-BAO-SEALED
    - ALT-SEC-001
  cli_commands:
    - "aiops-harness secrets health"
    - "aiops-harness secrets get-key"
  mcp_tools:
    - "aiops_secrets_get_key"
endpoints:
  host_loopback: "http://127.0.0.1:18200"
  container_gateway: "http://10.250.0.1:8200"
dependencies:
  - platform-security
  - platform-observability-monitoring
watched_paths:
  - "compose.yaml"
  - "infra/tofu/openbao/**"
  - "policies/*.hcl"
last_verified: 2026-11-06
review_ttl_days: 90
---
```

### 1.2 Pydantic v2 Validator Model (`platform_docs.models.frontmatter`)

`platform-docs` enforces the frontmatter contract using Pydantic v2 (`ConfigDict(extra="forbid")`):

```python
from datetime import date
from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field


class DocumentKind(StrEnum):
    OVERVIEW = "overview"
    INDEX = "index"
    ARCHITECTURE = "architecture"
    SPECIFICATION = "specification"
    RUNBOOK = "runbook"
    PLAYBOOK = "playbook"
    INTEGRATION_GUIDE = "integration-guide"
    ALERT_CATALOG = "alert-catalog"
    AGENT_MANIFEST = "agent-manifest"
    ADR = "adr"


class NamespaceScope(StrEnum):
    PLATFORM = "ns:platform"
    AIDOC = "ns:aidoc"
    B_LOCAL = "ns:b-local"


class DocumentStatus(StrEnum):
    ACTIVE = "active"
    MIGRATING = "migrating"
    DRAFT = "draft"
    DEPRECATED = "deprecated"


class AutonomyTier(StrEnum):
    GREEN = "green"    # Safe for autonomous read-only / diagnostic execution
    YELLOW = "yellow"  # Mutating operational action; requires warrant + human consent_id
    RED = "red"        # Destructive / break-glass ceremony; human operator execution only


class TriggerTriggers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alerts: list[str] = Field(default_factory=list, description="Alert IDs (e.g. ALT-PC-001)")
    cli_commands: list[str] = Field(default_factory=list, description="Bound CLI invocations")
    mcp_tools: list[str] = Field(default_factory=list, description="Bound FastMCP tool names")
    nats_subjects: list[str] = Field(default_factory=list, description="Bound NATS subjects")


class ServiceEndpoints(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host_loopback: str | None = Field(default=None, pattern=r"^https?://127\.0\.0\.1:\d+.*$")
    container_gateway: str | None = Field(default=None, pattern=r"^https?://10\.250\.0\.1:\d+.*$")
    internal_fqdn: str | None = Field(default=None, pattern=r"^https://[a-z0-9.-]+\.aiops-flow\.io.*$")
    mcp_socket: str | None = Field(default=None, pattern=r"^/run/[a-z0-9_-]+/[a-z0-9_.-]+\.sock$")


class DocumentFrontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^[a-z0-9]+(-[a-z0-9]+)+$")
    title: str = Field(..., min_length=10, max_length=160)
    kind: DocumentKind
    domain: str = Field(..., pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    namespace: NamespaceScope
    status: DocumentStatus
    autonomy_tier: AutonomyTier = AutonomyTier.GREEN
    owner: str = Field(..., min_length=3)
    consumers: list[str] = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)
    triggers: TriggerTriggers = Field(default_factory=TriggerTriggers)
    endpoints: ServiceEndpoints = Field(default_factory=ServiceEndpoints)
    dependencies: list[str] = Field(default_factory=list)
    watched_paths: list[str] = Field(..., min_length=1)
    last_verified: date
    review_ttl_days: int = Field(default=90, ge=7, le=365)
```

---

## 2. Executable Runbook Code-Block AST Specification

In documents with `kind: runbook` or `kind: playbook`, executable shell/CLI snippets use CommonMark info-string key-value attributes so `platform-docs` and `aiops-harness` can parse them into typed `RunbookStep` AST nodes while remaining 100% valid Markdown in GitHub/Gitea.

### 2.1 Code-Block Attribute Grammar

````markdown
```bash {id="probe-openbao-status" tier="green" mode="read-only" expect_exit=0 timeout_sec=10 service="platform-openbao"}
uv run aiops-harness secrets health --json
```
````

| Attribute | Required | Allowed Values | Default | Semantics for Autonomous Agents |
| :--- | :--- | :--- | :--- | :--- |
| `id` | **Yes** (for `runbook`/`playbook`) | `^[a-z0-9]+(-[a-z0-9]+)+$` | — | Globally unique step identifier within the document (`<doc_id>#<step_id>`). |
| `tier` | **Yes** | `green`, `yellow`, `red` | — | **`green`**: Autonomous execution allowed. **`yellow`**: Requires Ed25519 `X-Agent-Warrant` + Paperclip/Chatwoot human `consent_id`. **`red`**: Break-glass; agent execution hard-blocked by `platform_security.authz` (`S6 AGENT_FORBIDDEN`). |
| `mode` | **Yes** | `read-only`, `mutating` | — | **`read-only`**: Side-effect-free diagnostic probe (eligible for `uts` smoke testing). **`mutating`**: Modifies state, containers, secrets, or databases. |
| `expect_exit` | No | Integer (`0`–`255`) | `0` | Expected POSIX exit code confirming step success. |
| `timeout_sec` | No | Integer (`1`–`600`) | `30` | Hard execution timeout enforced by `aiops-harness` inside the gVisor/activity runner. |
| `requires_warrant` | No | `true`, `false` | `true` for `yellow`/`red` | Forces `platform_security.authz` warrant verification before execution. |
| `rollback_step` | No | `<step_id>` | `null` | References the inverse SAGA step ID in the same runbook if this mutating step fails. |

### 2.2 Invariant Coupling Between `tier` and `mode`

`platform-docs lint` enforces two hard safety invariants on every code block:

1. **`INV-STEP-01` (`green` Implies `read-only`):** A block with `tier="green"` MUST declare `mode="read-only"`. Any block declaring `mode="mutating"` with `tier="green"` is rejected by `lefthook` (`DOC-SEC-001`).
2. **`INV-STEP-02` (Forbidden Destructive Commands in `green`):** Even if a block is labeled `tier="green" mode="read-only"`, `platform-docs lint` parses the shell AST (via `bashlex` / `ast-grep`) and fails closed if the command contains mutating verbs (`docker restart`, `docker rm`, `tofu apply`, `kubectl delete`, `rm -rf`, `psql -c "DROP|DELETE|UPDATE|ALTER"`, `bao operator generate-root`).

---

## 3. Strict Chunk-Level Independence Rules

When `aidoc-flow-engramory` or `aidoc-context-tools` retrieves a single `##` or `###` section from a runbook, the surrounding document is not in the prompt. Therefore, every `##` and `###` section MUST obey four structural rules:

1. **Explicit Entity Naming (Zero Dangling Pronouns across Headings):**
   - *Forbidden:* `"## Restart Recovery\nWhen this happens, run the command above to restart it."`
   - *Required:* `"## 2. OpenBao Container Restart Recovery (ALT-BAO-SEALED)\nWhen probe-openbao-status reports Sealed=true on platform-openbao, execute remediate-bao-restart:"`
2. **Heading-Path Context Prefixing during Compilation:**
   When `platform-docs chunk` splits a file along `##` and `###` boundaries, it automatically prefixes every chunk with its canonical breadcrumb:

   ```text
   [Document: runbook-openbao-operations | Domain: platform-openbao | Namespace: ns:platform | Tier: yellow]
   [Path: OpenBao Target-State Operations > 2. OpenBao Container Restart Recovery (ALT-BAO-SEALED)]
   ```

3. **Deterministic Section Size Ceiling (`<= 450 words` / `<= 1,800 tokens`):**
   No single heading block (excluding fenced code blocks) may exceed 450 words without introducing a child subheading (`###` or `####`).
4. **Self-Contained Prerequisites & Environment Variables:**
   If a command block relies on an environment variable defined at the top of the document (e.g., `$STAGE_SSH` or `BAO_ADDR`), the frontmatter or code block comment must explicitly state or bind it so an isolated chunk never executes with unbound shell variables (`set -euo pipefail`).

---

## 4. Standardized `/llms.txt` and `/llms-full.txt` Specification

`platform-docs export-llms` deterministically generates two machine manifests at the root of every repository and at the root of the central stage portal (`https://docs.int.aiops-flow.io/llms.txt`):

### 4.1 `/llms.txt` Structure (Compact Agent Orientation Index, `< 4,000 tokens`)

```markdown
# <repo-name> (<domain> | <namespace>)

> <One-line summary from README.md frontmatter title>

- **Autonomy Tier Ceiling:** `<autonomy_tier>`
- **Host Loopback Endpoint:** `<endpoints.host_loopback>`
- **Container Gateway Endpoint:** `<endpoints.container_gateway>`
- **FastMCP Socket / Tools:** `<endpoints.mcp_socket>`

## 1. Agent Governance & Invariants
- [AGENTS.md](AGENTS.md): Mandatory invariants, CI protocol, and quality gates (`id: doc-...-agents`)

## 2. Active Operational Runbooks & Playbooks
- [docs/05_DEPLOYMENT_AND_OPERATIONS_RUNBOOK.md](docs/05_DEPLOYMENT_AND_OPERATIONS_RUNBOOK.md): <title> (`id: ...`, `tier: yellow`, `alerts: [ALT-...]`)

## 3. Architecture & Integration Specifications
- [docs/01_SYSTEM_ARCHITECTURE_AND_TOPOLOGY.md](docs/01_SYSTEM_ARCHITECTURE_AND_TOPOLOGY.md): <title> (`id: ...`, `tier: green`)

## 4. Active SDD Specification Chain (`sdd/*`)
- `SPEC`: [sdd/06_SPEC/...](sdd/06_SPEC/...)
- `ADR`: [sdd/05_ADR/...](sdd/05_ADR/...)
```

### 4.2 `/llms-full.txt` Structure (Bulk Context Compilation)

- Concatenates all `status: active` documents in deterministic topological order (`AGENTS.md` $\rightarrow$ `docs/00_INDEX.md` $\rightarrow$ `kind: architecture` $\rightarrow$ `kind: specification` $\rightarrow$ `kind: runbook` $\rightarrow$ Tier-3 compiled `sdd/*` projections).
- Excludes all documents with `status: deprecated` or `status: draft`.
- Passes `platform_security.audit.redact_for_d14()` fail-closed prior to writing to disk.
