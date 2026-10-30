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
(`technology_stack`; conventional checkout
`/home/ya/devd/technology_stack`) — not local habit, not model memory.
The stack is the source of truth; this rule is the binding subset agents
apply without re-deriving it. On a conflict between a repo's local habit
and the stack, surface it — never silently follow the weaker. Cite the
stack section (`06 §9.2`) for platform choices in plans, reviews, and
approval items.

**Secrets — Two-Tier (06 §9.1–§9.2).** OCI Vault holds cold-start only;
OpenBao holds runtime only; nothing secret in code, image layers,
plaintext env on disk, logs, spans, or agent prompts. Runtime injection
flows through `bao agent` (RAM-only sinks); root tokens exist only inside
break-glass ceremonies (mint → act → revoke); policies are default-deny.

**Cloud and environments (09 §3.20, 16).** OCI is primary; region follows
the tenancy home region. Dev/stage/prod stay disjoint (a stage credential
authenticates nowhere in prod). No multi-node clustering on dev/stage (no
"HA theater"); tooling is container-native; scripts use canonical
`SCRIPT_DIR`/`REPO_ROOT` resolution; stateful services get
`stop_grace_period: 30s`; new operational utilities support `--json`.

**IaC (11, 09 §3.20).** OpenTofu; remote state with locking (bucket);
secret values out-of-band or SOPS-encrypted tfvars — never plaintext in
git; compartments, IAM, and networks under declarative review.

**Identity and network (06 §9, 09 §3.4).** Humans authenticate via OIDC
(Authentik); services over the Tailscale mesh; TLS at the edge; short
TTL, least privilege, no standing tokens.

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
