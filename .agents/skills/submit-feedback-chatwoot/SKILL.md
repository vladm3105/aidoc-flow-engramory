---
name: submit-feedback-chatwoot
description: >-
  Use when a bug, defect, documentation error, or improvement idea must be captured via the unified Chatwoot API. Submits structured feedback directly to Chatwoot for automated sanitization and downstream GitHub issue creation via Support Desk, eliminating dual-channel maintenance.
---

# Submit feedback via Chatwoot (Single Standard)

An actionable finding must survive the session that found it. To prevent
channel fragmentation and dual-channel maintenance, all defects, bug reports,
and improvement ideas enter through a **single standard ingress plane: the
Chatwoot API**.

The Support Desk (`aidoc-flow-support-desk`) acts as the automated gateway: it
sanitizes secrets fail-closed, authorizes the target repository, prevents
duplicates, derives deterministic correlation identifiers (`UUIDv5 request_id`),
and files the canonical issue in GitHub Issues and GitHub Projects v2.

Requires `curl` and `jq` plus a valid Chatwoot API access token (`CHATWOOT_API_TOKEN`).

## 1. Establish account, inbox, and target

Authoritative settings for the platform Chatwoot deployment are defined in
`/home/ya/devd/platform-stage-ops` (specifically `inventory/stage.md`,
`scripts/cloudflare-platform-stage.py`, and `stacks/platform-chatwoot/compose.yaml`).

### Endpoint Resolution

- **Internal Stage Mesh (preferred for local/host agents and CI runners):**
  `http://10.250.0.1:3000` (routed via `platform-tenant-chatwoot.socket` to the rootless `chatwoot` container namespace, UID 1006, port 13030).
- **Public Stage Domain (external access):**
  `https://chatwoot-stage.aiops-flow.io` (managed by Cloudflare tunnel in `platform-stage-ops/scripts/cloudflare-platform-stage.py`).
- **Stage Host Runner (mesh access):**
  `ssh ya@debian` or `ssh ya@100.98.134.10` over Tailscale mesh (direct access to tenant socket `10.250.0.1:3000`).

### Tenant Accounts & Inboxes (PLAT-025 / ADR-02)

| Account ID | Slug | Primary Audience & Target Scope | Inboxes & Ingress Routing |
|---|---|---|---|
| **1** | **`aidoc-projects`** | Autonomous agent feedback, spoke microservices, cross-project defects, and developer intake | `1`: Support Desk API Gateway (`/webhooks/chatwoot/support-desk`)<br>`2`: Platform API Gateway (`/webhooks/chatwoot/support-desk`)<br>`4`: AIDOC Framework API Gateway (`/webhooks/chatwoot/framework`)<br>`6`: Trading Nexus API Gateway (`/webhooks/chatwoot/trading-nexus`) |
| **3** | **`aiops-platform`** | Infrastructure metrics, unified OTel Collector, HyperDX alerts, Prometheus threshold breaches | `3`: Operations Assistant API Gateway (`/webhooks/chatwoot/operations-assistant`)<br>`7`: Platform Unified Alerts & Incidents (`/webhooks/chatwoot/operations-assistant`) |
| **5** | **`b-local`** | BeeLocal customer-facing support, mobile app, web widget, merchant inquiries | `5`: BeeLocal API Gateway (`/webhooks/chatwoot/b-local-privy`) |

### Environment Configuration & Secret Resolution

Resolve the following variables from the environment or the OpenBao stage store (`stage/aidoc-flow-support-desk/tokens` or Chatwoot SuperAdmin user profile):

```bash
# Default to internal stage mesh endpoint; fallback to public stage URL if off-mesh:
CHATWOOT_BASE_URL="${CHATWOOT_BASE_URL:-http://10.250.0.1:3000}"
CHATWOOT_API_TOKEN="${CHATWOOT_API_TOKEN:?CHATWOOT_API_TOKEN must be set}"
CHATWOOT_ACCOUNT_ID="${CHATWOOT_ACCOUNT_ID:-1}"
```

Identify the **target repository** that owns the fix (e.g.
`vladm3105/aiops-harness`, `vladm3105/platform-chatwoot`, `vladm3105/platform-stage-ops`).
Every submission must declare `Target: <owner/repo>` so the Support Desk routes
the resulting GitHub issue accurately.

## 2. Promotion bar

Capture when any of these is true:

- another person or session could act on it;
- it reproduces at `file:line` and has a plausible fix shape;
- it is user-visible or blocks a consumer.

Do not file speculation, a problem already fixed by the current change, or a
duplicate. A local workaround does not remove the need to capture the owning
defect.

## 3. Classify the origin

Every finding carries one of two origin classes, decided by a single
falsifiable test — **name what broke, or it is a review finding**:

- **`real-use`** — a project hit this while doing its own work. Something
  broke, blocked, or shipped wrong, and you can name it: the run, the PR, the
  command, the consumer.
- **`review`** — a review, audit, scan, self-check, or reading pass went
  looking and found it. Nothing was blocked.

Rules governing origin:

- **The class is evidence, not a severity rating.** "It would break X" is
  `review`. A `review` finding can be critical, correct, and urgent and is
  still `review`. Do not promote one by arguing about its impact.
- **A repro you built to prove your own hypothesis is still `review`.**
  `real-use` requires the break to have happened in the project's own work,
  before and independent of the investigation that found it.
- **`real-use` outranks every `review` finding in the same queue.**
- **Name both the source project and the target repository.** The source is
  where the symptom surfaced; the target is who owns the fix.

## 4. Search before filing

Search existing Chatwoot conversations to prevent creating duplicate threads:

```bash
curl -sS -f \
  -H "api_access_token: $CHATWOOT_API_TOKEN" \
  "$CHATWOOT_BASE_URL/api/v1/accounts/$CHATWOOT_ACCOUNT_ID/conversations?q=distinctive+terms" \
  | jq -r '.data.payload[] | {id: .id, status: .status, title: .messages[0].content[:60]}'
```

- **Open matching conversation found:** Append additional evidence as a new
  message to that existing conversation (`CONVERSATION_ID`).
- **No match found:** Create a new conversation thread in the matching inbox.

## 5. Body contract

The message payload carries the structured technical analysis. The quality of
this report directly determines whether `operations-assistant` can resolve the
issue autonomously or requires human escalation.

The canonical schema (see full reference and example in `templates/feedback-template.md`):

```markdown
## Source
Origin: real-use | review
Project: <the repository or consumer that generated this>
Target: <owning repository to receive the fix, e.g. vladm3105/aiops-harness>
Trigger: <the run, PR, commit SHA, command, or session where it surfaced>
What it blocked: <concrete impact, or literal "nothing — found by looking">

## Summary
One falsifiable statement of the defect and impact.

## Expected vs Actual Behavior
- Expected: <intended contract or specification>
- Actual: <observed failure, timeout, or incorrect state>

## Reproduction
Exact command, input, environment (OS, runtime version, commit SHA), and `file:line` evidence.

## Blast radius
What was inspected or executed, what is affected, and what is not.

## Why diagnosis was difficult
The misleading signal, silent failure, race condition, or interaction that hid the cause.

## Fix shape
A bounded implementation direction, including constraints and regression test shape.

## What is not broken
Nearby behavior verified as unaffected (regression boundaries).
```

### Information Quality Rules

- **Move existing analysis without contracting it.** Do not replace evidence
  with a pointer to chat, scratch, a local path, or an inaccessible artifact.
  Paste the uncontracted traceback and exact reproduction commands.
- **`Target:` is mandatory.** Unlike single-repo tracking, the Chatwoot ingress
  routes across the entire ecosystem. If `Target:` is omitted or ambiguous,
  triage cannot dispatch the GitHub issue to the owning repository.
- **Redact before transmission.** Tokens, keys, cookies, auth headers, and
  connection strings must never enter a draft literally — replace each with
  `<redacted>` (e.g. `Authorization: Bearer <redacted>`), then re-read the
  full draft for secret shapes before publishing. When a finding's value
  depends on exact bytes, name the shape instead of pasting them.

## 6. Safe Chatwoot API publication

### Step A: Submitting to an Existing Conversation

When following up on an existing issue or adding repro steps to an open
conversation:

```bash
CONVERSATION_ID=1420
PAYLOAD=$(mktemp)
trap 'rm -f "$PAYLOAD"' EXIT

cat > "$PAYLOAD" <<'EOF'
{
  "content": "## Source\nOrigin: real-use\nProject: trading_nexus\nTarget: vladm3105/aiops-harness\nTrigger: run-419\nWhat it blocked: websocket streaming\n\n## Summary\nWebSocket heartbeat timeout drops connection under network jitter.\n\n## Reproduction\nsrc/ws.py:142 - timeout after 5000ms ACK missing.\n\n## Blast radius\nOnly streaming feeds affected.\n\n## Why diagnosis was difficult\nSilent disconnection without error callback.\n\n## Fix shape\nAdd 2x grace window to heartbeat ACK handler.\n\n## What is not broken\nInitial handshake and frame serialization.",
  "message_type": "incoming"
}
EOF

RESPONSE=$(curl -sS -f -X POST \
  "$CHATWOOT_BASE_URL/api/v1/accounts/$CHATWOOT_ACCOUNT_ID/conversations/$CONVERSATION_ID/messages" \
  -H "Content-Type: application/json" \
  -H "api_access_token: $CHATWOOT_API_TOKEN" \
  -d @"$PAYLOAD")

MSG_ID=$(echo "$RESPONSE" | jq -r '.id // empty')
[ -n "$MSG_ID" ] || { echo "Chatwoot message creation failed" >&2; exit 1; }
```

### Step B: Creating a New Inbound Ticket Conversation

Chatwoot's API requires associating a conversation with an existing `contact_id`. Ingestion executes in three deterministic steps:

1. **Resolve or Create Contact:** Ensure a sender contact identity exists for the reporting agent or service:

```bash
CONTACT_EMAIL="agent-muse@aiops-flow.io"
CONTACT_ID=$(curl -sS -f \
  -H "api_access_token: $CHATWOOT_API_TOKEN" \
  "$CHATWOOT_BASE_URL/api/v1/accounts/$CHATWOOT_ACCOUNT_ID/contacts/search?q=$CONTACT_EMAIL" \
  | jq -r '.payload[0].id // empty')

if [ -z "$CONTACT_ID" ]; then
  CONTACT_ID=$(curl -sS -f -X POST \
    "$CHATWOOT_BASE_URL/api/v1/accounts/$CHATWOOT_ACCOUNT_ID/contacts" \
    -H "Content-Type: application/json" \
    -H "api_access_token: $CHATWOOT_API_TOKEN" \
    -d "{\"name\": \"Agent Muse\", \"email\": \"$CONTACT_EMAIL\"}" \
    | jq -r '.payload.contact.id // empty')
fi
[ -n "$CONTACT_ID" ] || { echo "Failed to resolve contact ID" >&2; exit 1; }
```

1. **Open Conversation in Target Inbox:**

```bash
INBOX_ID=1  # Account 1: 1 (support-desk), 2 (platform), 4 (framework), 6 (trading)
            # Account 3: 3 (operations-assistant), 7 (incidents)
            # Account 5: 5 (b-local)

CONVO_RESP=$(curl -sS -f -X POST \
  "$CHATWOOT_BASE_URL/api/v1/accounts/$CHATWOOT_ACCOUNT_ID/conversations" \
  -H "Content-Type: application/json" \
  -H "api_access_token: $CHATWOOT_API_TOKEN" \
  -d "{
    \"inbox_id\": $INBOX_ID,
    \"contact_id\": $CONTACT_ID,
    \"custom_attributes\": {
      \"target_repo\": \"vladm3105/aiops-harness\",
      \"origin\": \"real-use\"
    }
  }")

CONVO_ID=$(echo "$CONVO_RESP" | jq -r '.id // empty')
[ -n "$CONVO_ID" ] || { echo "Chatwoot conversation creation failed" >&2; exit 1; }
echo "Created conversation ID: $CONVO_ID"
```

1. **Post Inbound Technical Finding:**

```bash
PAYLOAD=$(mktemp)
trap 'rm -f "$PAYLOAD"' EXIT

cat > "$PAYLOAD" <<'EOF'
{
  "content": "## Source\nOrigin: real-use\nProject: trading_nexus\nTarget: vladm3105/aiops-harness\nTrigger: run-419\nWhat it blocked: websocket streaming\n\n## Summary\nWebSocket heartbeat timeout drops connection under network jitter.\n\n## Reproduction\nsrc/ws.py:142 - timeout after 5000ms ACK missing.\n\n## Blast radius\nOnly streaming feeds affected.\n\n## Why diagnosis was difficult\nSilent disconnection without error callback.\n\n## Fix shape\nAdd 2x grace window to heartbeat ACK handler.\n\n## What is not broken\nInitial handshake and frame serialization.",
  "message_type": "incoming"
}
EOF

MSG_RESP=$(curl -sS -f -X POST \
  "$CHATWOOT_BASE_URL/api/v1/accounts/$CHATWOOT_ACCOUNT_ID/conversations/$CONVO_ID/messages" \
  -H "Content-Type: application/json" \
  -H "api_access_token: $CHATWOOT_API_TOKEN" \
  -d @"$PAYLOAD")

MSG_ID=$(echo "$MSG_RESP" | jq -r '.id // empty')
[ -n "$MSG_ID" ] || { echo "Chatwoot message dispatch failed" >&2; exit 1; }
echo "Dispatched message ID: $MSG_ID"
```

### Step C: Verify Support Desk Confirmation

The Support Desk Agent Bot intercepts the webhook from Chatwoot, sanitizes secrets, performs intake validation, and posts an immediate confirmation card into the thread. Verify the acknowledgment:

```bash
sleep 2
curl -sS -f \
  -H "api_access_token: $CHATWOOT_API_TOKEN" \
  "$CHATWOOT_BASE_URL/api/v1/accounts/$CHATWOOT_ACCOUNT_ID/conversations/$CONVO_ID/messages" \
  | jq -r '.payload[] | select(.sender.name=="Vl" or .sender.type=="agent_bot") | .content'
```

Look for the Support Desk Ticket Receipt:

```markdown
🎫 **Support Desk Ticket Received:** Your request has been accepted for triage.

- **Tracking ID:** `<short_uuid>`
- **Target:** `vladm3105/<target_repo>`
Our automated triage engine is evaluating this request.
```

## 7. Status inquiries

Once a conversation is created, anyone or any agent can poll the status directly
in the thread without needing GitHub access:

```bash
# Post a status query into the thread:
curl -sS -f -X POST \
  "$CHATWOOT_BASE_URL/api/v1/accounts/$CHATWOOT_ACCOUNT_ID/conversations/$CONVO_ID/messages" \
  -H "Content-Type: application/json" \
  -H "api_access_token: $CHATWOOT_API_TOKEN" \
  -d '{"content": "/status", "message_type": "incoming"}'

# Read back the live status card generated by Support Desk:
sleep 2
curl -sS -f \
  -H "api_access_token: $CHATWOOT_API_TOKEN" \
  "$CHATWOOT_BASE_URL/api/v1/accounts/$CHATWOOT_ACCOUNT_ID/conversations/$CONVO_ID/messages" \
  | jq -r '.payload[-1].content'
```

The Support Desk responds with the live status state:

- If triage is still in progress:

  ```markdown
  ℹ️ **Support Desk Status Note:** No tracked engineering defect or issue has been filed for this conversation yet. If you are reporting a defect, please provide error logs or reproduction details.
  ```

- Once triaged and filed on GitHub:

  ```markdown
  ℹ️ **Support Desk Live Status Update**

  - **Target Repository:** `vladm3105/<target_repo>`
  - **Issue:** [#<number> <title>](<github_issue_url>)
  - **Current State:** 🟢 `OPEN` / ✅ `CLOSED`
  - **Autonomy Tier:** `TIER:GREEN`
  - **Labels:** `bug`
  ```

## 8. Report

Return the origin class, the source project, the target repository, the
Chatwoot Account ID, the Conversation ID, and the confirmation from the Support
Desk. If the Chatwoot token was missing or unreachable, return the complete
formatted draft and state what credentials or endpoints are required.
