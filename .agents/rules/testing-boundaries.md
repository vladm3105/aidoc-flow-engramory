---
paths:
  - "**/tests/**"
  - "**/*.test.*"
  - "**/*.spec.*"
---

# Testing boundaries

**Test through the public path.** Drive tests through the same surface real
consumers use: the UI via browser automation, or the public API endpoints.
Direct calls to underlying provider APIs or service internals that bypass
signing, authorization, session, or state management are debug-only — to verify
behavior, inspect payloads, or diagnose integration issues. Never as the
primary test method; they produce unrealistic results.

**Privileged backdoors are debug-only.** Admin, debug, or raw-state endpoints
that bypass auth, RBAC, or business logic inspect system state for debugging
failures only. Never use them for assertions, automated flows, or as a
substitute for the public path — tests that pass via a backdoor may fail in
production. Pattern: run the test through the proper path, then optionally use
the backdoor to debug failures.

**Plan↔test status sync.** When an implementation plan entry flips to done,
flip the corresponding test-mapping entries from pending to implemented in the
same change. A plan that says done while its tests say pending is a stale
contract — never hand off with the two out of sync.

**Entropy and mock secrets (GAP-03, CRIT-07).** Secret scanners (e.g. Gitleaks)
calculate Shannon entropy over test code. Never use pseudo-random hex or base64
strings >= 12 characters as test fixtures. Use zero-padded strings (e.g.,
`"0" * 64`) or labeled deterministic tokens (e.g., `dummy-auth-fixture-key`).

**Security linter parameter defaults (GAP-04).** In test helper functions and
fixtures, never assign hardcoded string defaults to parameters named `password`,
`secret`, or `token`. Use `None` as the default (e.g. `def mock_auth(secret: str | None = None):`)
to prevent static security linters (Bandit B107) from flagging hardcoded secrets.

**Suppression formatting (GAP-05).** If an intentional test credential must be
passed in fixture code, always use the double-comment format:
`# nosec B105 # reason: test fixture credential only`.
The anti-gaming parser requires the `# reason:` explanation clause to permit the
suppression.

