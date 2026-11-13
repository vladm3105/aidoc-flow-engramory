# Engineering Feedback & Defect Report Template

> **Purpose:** Single-standard structured defect and improvement payload for ingestion via Chatwoot API.
> When submitted, `aidoc-flow-support-desk` transforms this report directly into an actionable GitHub Issue and GitHub Projects v2 card for autonomous resolution by `operations-assistant`.

---

## Source

Origin: real-use | review
Project: <consumer-or-source-repo-where-symptom-surfaced>
Target: <owning-repo-where-fix-must-land, e.g. vladm3105/aiops-harness>
Trigger: <the run-id, PR, commit-sha, or test command where it surfaced>
What it blocked: <concrete impact on work, or literal "nothing — found by looking">

## Summary

<One crisp, falsifiable statement defining the exact defect, symptom, and impact. Do not speculate.>

## Expected vs Actual Behavior

- **Expected:** <What the contract, specification, or user interaction required>
- **Actual:** <What actually occurred, failed, or timed out instead>

## Reproduction

- **Environment:** <OS, runtime version (e.g. Python 3.12, Node 20), branch, commit SHA>
- **Location:** <Exact file:line citation where the error originates, e.g. src/transport/ws.py:142>
- **Exact Command / Input:**

```bash
<exact command line or API invocation to trigger the defect>
```

- **Error Trace / Log:**

```text
<uncontracted stack trace or error log; secrets replaced with <redacted>>
```

## Blast Radius & Scope

- **Inspected / Executed:** <What code paths, endpoints, or modules were touched>
- **Affected:** <What components, consumers, or downstream services are impacted>
- **Not Affected:** <What components or adjacent paths are verified safe>

## Why Diagnosis Was Difficult

<The misleading signal, silent failure, race condition, or cross-service interaction that masked the root cause>

## Fix Shape & Implementation Constraints

- **Proposed Direction:** <Concrete implementation shape; cite functions, classes, or config keys>
- **Constraints:** <Architectural invariants, performance limits, backward compatibility needs>
- **Regression Test Shape:** <How the fix should be verified (e.g. unit test covering timeout scenario)>

## What Is Not Broken

<Nearby functionality, endpoints, or tests verified as healthy and unaffected>

---

# Reference Example (Target Quality Benchmark)

```markdown
## Source
Origin: real-use
Project: trading_nexus
Target: vladm3105/aiops-harness
Trigger: integration-test-run #419 (commit 8f3b2a1)
What it blocked: Live order-routing websocket streaming pipeline

## Summary
WebSocket client silently disconnects and stops streaming market updates when server heartbeat ACK is delayed beyond 5000ms under high load.

## Expected vs Actual Behavior
- **Expected:** Client should tolerate transient network jitter with a grace buffer before disconnecting, and trigger `on_reconnect()` handler.
- **Actual:** Client immediately tears down the socket upon single heartbeat timeout and suppresses the error callback, freezing consumer queues.

## Reproduction
- **Environment:** Linux x86_64, Python 3.12.5, branch: feat/streaming-v2, commit: 8f3b2a1
- **Location:** `src/transport/ws.py:142` in `_handle_heartbeat()`
- **Exact Command / Input:**
```bash
pytest tests/integration/test_ws_stream.py -k test_high_volume_jitter
```

- **Error Trace / Log:**

```text
2026-10-31 11:20:01 [ERROR] ws.transport: Heartbeat ACK missing after 5000ms
Traceback (most recent call last):
  File "src/transport/ws.py", line 142, in _handle_heartbeat
    raise ConnectionTimeoutError("Heartbeat deadline exceeded")
transport.exceptions.ConnectionTimeoutError: Heartbeat deadline exceeded
```

## Blast Radius & Scope

- **Inspected / Executed:** Verified WebSocket framing in `src/transport/ws.py` and reconnect loop in `src/transport/reconnect.py`.
- **Affected:** All streaming market-data consumers using `WsTransportClient`.
- **Not Affected:** Synchronous REST fallback endpoints (`src/transport/http.py`) and authentication middleware.

## Why Diagnosis Was Difficult

The exception occurred inside an unhandled background `asyncio` task, causing the socket to silently close while the parent connection pool reported status `CONNECTED`.

## Fix Shape & Implementation Constraints

- **Proposed Direction:**
  1. Add a configurable `heartbeat_grace_period` (default 2x interval) in `WsConfig`.
  2. Ensure `on_error` and `on_reconnect` callbacks fire when deadline expires.
- **Constraints:** Must not increase memory allocation per socket; must remain compatible with RFC 6455 framing.
- **Regression Test Shape:** Unit test in `tests/test_ws.py` mocking a 6000ms delayed heartbeat ACK and verifying reconnection attempt.

## What Is Not Broken

TLS handshake, initial token authentication, and frame serialization/deserialization all pass verified unit tests.

```
