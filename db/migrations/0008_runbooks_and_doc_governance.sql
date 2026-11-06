-- 0008_runbooks_and_doc_governance.sql
-- Canonical L0 Documentation Metadata & Executable Runbook Steps (PLAN-013 / ADR-08).
-- Stores deterministic YAML frontmatter, autonomy_tier, watched_paths, trigger_alerts,
-- and annotated {id, tier, mode, expect_exit, timeout_sec} executable code blocks.

CREATE TABLE IF NOT EXISTS kb_documents (
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    doc_id          TEXT NOT NULL,
    project_id      TEXT,
    domain_id       TEXT,
    scope           TEXT NOT NULL DEFAULT 'project'
                        CHECK (scope IN ('agent', 'project', 'domain', 'space')),
    repo_path       TEXT NOT NULL,
    title           TEXT NOT NULL,
    kind            TEXT NOT NULL,
    namespace       TEXT NOT NULL DEFAULT 'ns:platform',
    status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'draft', 'deprecated', 'archived')),
    autonomy_tier   TEXT NOT NULL DEFAULT 'green'
                        CHECK (autonomy_tier IN ('green', 'yellow', 'red')),
    frontmatter     JSONB NOT NULL DEFAULT '{}'::jsonb,
    watched_paths   TEXT[] NOT NULL DEFAULT '{}',
    trigger_alerts  TEXT[] NOT NULL DEFAULT '{}',
    last_verified   DATE NOT NULL DEFAULT CURRENT_DATE,
    review_ttl_days INTEGER NOT NULL DEFAULT 90,
    git_commit_sha  TEXT NOT NULL DEFAULT 'local-head',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, doc_id)
);

DROP TRIGGER IF EXISTS trg_kb_documents_updated_at ON kb_documents;
CREATE TRIGGER trg_kb_documents_updated_at
    BEFORE UPDATE ON kb_documents
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX IF NOT EXISTS idx_kb_documents_alerts
    ON kb_documents USING GIN (trigger_alerts);
CREATE INDEX IF NOT EXISTS idx_kb_documents_watched
    ON kb_documents USING GIN (watched_paths);
CREATE INDEX IF NOT EXISTS idx_kb_documents_tenant_project
    ON kb_documents (tenant_id, project_id, status);

CREATE TABLE IF NOT EXISTS kb_runbook_steps (
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    step_uid        TEXT NOT NULL,
    doc_id          TEXT NOT NULL,
    step_order      INTEGER NOT NULL,
    step_id         TEXT NOT NULL,
    heading_path    TEXT NOT NULL,
    tier            TEXT NOT NULL
                        CHECK (tier IN ('green', 'yellow', 'red')),
    mode            TEXT NOT NULL
                        CHECK (mode IN ('read-only', 'mutating')),
    expect_exit     INTEGER NOT NULL DEFAULT 0,
    timeout_sec     INTEGER NOT NULL DEFAULT 30,
    command_body    TEXT NOT NULL,
    prose_context   TEXT NOT NULL,
    rollback_step   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, step_uid),
    FOREIGN KEY (tenant_id, doc_id)
        REFERENCES kb_documents (tenant_id, doc_id) ON DELETE CASCADE,
    CONSTRAINT chk_green_readonly
        CHECK (tier != 'green' OR mode = 'read-only')
);

CREATE INDEX IF NOT EXISTS idx_kb_runbook_steps_doc_order
    ON kb_runbook_steps (tenant_id, doc_id, step_order);
CREATE INDEX IF NOT EXISTS idx_kb_runbook_steps_tier
    ON kb_runbook_steps (tenant_id, tier);
