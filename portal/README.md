---
id: doc-engramory-portal-readme
title: Astro Starlight + Scalar Human Supervision Portal Scaffold
kind: architecture
domain: platform-engramory
namespace: ns:platform
status: active
autonomy_tier: green
owner: platform-team
consumers:
  - platform-engramory
  - aiops-harness
  - aidoc-flow-support-desk
tags:
  - portal
  - starlight
  - scalar
  - mermaid
  - d14-redaction
endpoints:
  host_loopback: "http://127.0.0.1:8005"
  container_gateway: "http://10.250.0.1:8000"
watched_paths:
  - "portal/package.json"
  - "portal/astro.config.mjs"
  - "src/engramory/core/portal_builder.py"
triggers:
  cli_commands:
    - "engramory docs build-portal"
  mcp_tools:
    - "docs_build_portal"
last_verified: 2026-11-09
---

# Astro Starlight + Scalar Human Supervision Portal (`portal/`)

## 1. Purpose & Built-In Architecture

The `portal/` directory provides the 100% MIT open-source static web portal scaffold for `platform-engramory`, built on **Astro Starlight** (`@astrojs/starlight`), **Scalar** (`@scalar/api-reference`, `@scalar/astro`), and **Mermaid v11** (`mermaid`).

`engramory docs build-portal` (`stage_portal_bundle` in `src/engramory/core/portal_builder.py`) stages governed repository `docs/*.md` files into `portal/src/content/docs/reference/`, enforcing workspace path confinement (`resolve_confined_repo_path`) and Rule D14 secret/Unicode scrubbing (`_scrub_manifest_text`) prior to static HTML generation.

## 2. Staging & Dry-Run Verification Commands

Run the read-only dry-run portal bundle check via the `engramory` CLI:

```bash {id="probe-portal-bundle-dry-run" tier="green" mode="read-only" expect_exit=0 timeout_sec=15}
uv run engramory docs build-portal --target . --dry-run --json
```
