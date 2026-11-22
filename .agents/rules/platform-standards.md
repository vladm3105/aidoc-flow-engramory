---
paths:
  - "**/docker-compose*.yml"
  - "**/docker-compose*.yaml"
  - "**/compose.*.yaml"
  - "**/compose.*.yml"
  - "**/Dockerfile*"
  - "**/*.tf"
  - "**/*.hcl"
  - "**/otel*.yaml"
  - "**/otel*.yml"
  - "**/daemon.json"
---

# Platform standards (approved technology stack)

Platform decisions follow the approved technology stack
(`technology-stack` canon; local checkout at
`$TECHNOLOGY_STACK_ROOT`) — not local habit, not model memory.
The stack is the source of truth; this rule is the binding subset agents
apply without re-deriving it. On a conflict between a repo's local habit
and the stack, surface it — never silently follow the weaker. Cite the
stack section (`06 §9.2`) for platform choices in plans, reviews, and
approval items.

**Secrets — Two-Tier (06 §9.1–§9.2).** OCI Vault holds cold-start only;
OpenBao (`:8200` container / `:18200` stage host) holds runtime only; nothing
secret in code, image layers, plaintext env on disk, logs, spans, or agent
prompts. Runtime injection flows through `bao agent` into tmpfs `/dev/shm`
(`0600` mode, atomic rename) or via `OpenBaoClient` (`get_project_secrets`,
`inject_project_secrets`) using dedicated AppRoles/policies (`platform-nats`
auth-callout, `paperclip`, `b-local-privy` UID 1001, `auth/jwt-agents`); root
tokens exist only inside break-glass ceremonies (mint → act → revoke); policies
are default-deny.

**Storage and Backup/DR (04 §3.6, 20, 26).** SeaweedFS and MinIO are retired
across the platform. Use OCI Object Storage, Cloudflare R2, or local POSIX bind
mounts (`resolve_hybrid_storage` in `HYBRID MODE`). Follow Backup/DR v2.1
4-Tier (`4-3-2-1-0`) architecture (`platform-backup-dr`): ZFS snapshots + daily
Sanoid/Borg + ReaR bootable UEFI recovery ISO + Restic/rclone to OCI/R2 + B2a
air-gapped pull replica on `devd` (`100.88.0.5`).

**Cloud and environments (09 §3.20, 16).** OCI is primary; region follows
the tenancy home region. Dev/stage/prod stay disjoint (a stage credential
authenticates nowhere in prod). No multi-node clustering on dev/stage (no
"HA theater"); tooling is container-native; scripts use canonical
`SCRIPT_DIR`/`REPO_ROOT` resolution; stateful services get
`stop_grace_period: 30s`; new operational utilities support `--json`.

**IaC & Everything as Code (11, 19, 29, 09 §3.20).** Configuration Inventory
and Change Management as a code, Infrastructure as a code, and Documentation as
a code — use an everything-as-a-code approach as much as possible. All changes
on `stage` (and `corridor`) must be done via OpenTofu (`tofu/`) and declarative
configuration management (`manifest.tsv`, `deploy/path_rules.tsv`, `pyinfra`).
No manual/direct changes on containers or configs on `stage` (no ad-hoc SSH
config edits, `scp`, or manual container mutations). Manual changes are allowed
on `dev` (`devd`) only during development, testing, or debugging, and must be
codified into Git + OpenTofu/configuration management before promoting to
`stage`. Remote state uses locking (bucket); secret values stay out-of-band or
SOPS-encrypted tfvars — never plaintext in git; compartments, IAM, networks,
host manifests, `/etc` ledgers (`etckeeper`), inventory schemas, and BI
dashboards stay under declarative Git-tracked review.

**Identity, Security Gate, and Network (06 §9, 09 §3.4, 30, 32).** `platform-security`
consolidates the retired `platform-identity` and `platform-authz` repos into a
unified 6-Pillar Suite (`platform_security`, `platform_authz`, `platform_identity`,
`client`). Humans authenticate via Authentik OIDC/SAML (`28` Paperclip, `30`
Cloudflare Zero Trust OIDC, `31` OCI Console SAML 2.0, `32` BeeLocal Console
`:18083`). Agent tool calls pass through the Security Gate (`127.0.0.1:18095`
host / `10.250.0.1:8095` container / `/run/platform-security/gate.sock` UDS)
with Ed25519 `X-Agent-Warrant` (`paperclip_task_id`, `max_budget_usd`,
`IntentCapsule`, canary traps) and namespace repo isolation (`ns:platform` UID
1004, `ns:aidoc` UID 1002, `ns:b-local` UID 1001, `ns:security` UID 1005).
Services communicate over the NetBird WireGuard mesh (`100.88.0.0/16`,
`*.int.aiops-flow.io`) or legacy Tailscale (`100.64.0.0/10`, `*.ts.net`) with
TLS at the edge, short TTL, least privilege, and no standing tokens.

**Orchestration, Messaging, and LLM Spend Governance (28, 30, 31, 33).**
Asynchronous events flow over NATS JetStream (`:4222`/`:8222`, streams
`INBOX_AIOPS_PM`, `INBOX_PAPERCLIP`, `EVENTS_PAPERCLIP`, `DLQ_PAPERCLIP`,
`DESK_EVENTS` with CloudEvents 1.0 envelopes and NATS auth-callout via
`platform-security` / `platform_security.authz`). Paperclip v0.4.2 (`:13100`/`:3100`) enforces lease-epoch
fencing (`lease_epoch`, `X-Paperclip-Lease-Epoch`, HTTP 409
`PaperclipFencedOutError` via `PaperclipLeaseGuard`), per-task spend ledgers,
and Doc 06 S6/S7 human SoD gates (`approve_gate`). LiteLLM (`:4000`/`:4001`)
enforces `paperclip_spend_hook.py` (`PaperclipSpendGuard`, 90% turn buffer,
warrant revocation on budget overrun) and harness `SpendTripwire`. Temporal
(`:7233`/`:8233`, `ExecutePaperclipTaskWorkflow`, `DeskBridgeWorker`) enforces
mTLS fail-closed, `Decimal`-only money fields, and SHA/attempt-bound CI/SoD
signals.

**Never offer insecure options.** Do not present any of these as an
option — not as the recommendation, not as an "alternative", not as a
shortcut. Stack-cited: plaintext secrets anywhere; standing tokens in
env/Compose; `userpass` outside dev; root outside break-glass (06 §9.2);
unencrypted edge transports; open ingress (09 §3.20, 16 §4).
Secure-default corollaries, same force: public buckets;
`skip-verify`/insecure-TLS flags; running as root without need;
`:latest`/unpinned images and dependencies; disabling auth, MFA, or
audit.

If the user explicitly requests a non-compliant path: name the violated
stack rule, state the concrete risk, offer the compliant alternative —
and require explicit informed confirmation through `approval-gate`
before proceeding. Never implement it silently.
