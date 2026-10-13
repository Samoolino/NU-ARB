-- PostgreSQL schema for project/agent management only. Does not store exchange credentials or trade data.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TABLE IF NOT EXISTS agent_projects (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug TEXT NOT NULL UNIQUE CHECK (length(slug) BETWEEN 2 AND 80),
  name TEXT NOT NULL CHECK (length(name) BETWEEN 2 AND 160),
  description TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_definitions (
  id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 2 AND 100),
  project_id UUID NOT NULL REFERENCES agent_projects(id) ON DELETE CASCADE,
  current_revision INTEGER NOT NULL DEFAULT 0 CHECK (current_revision >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_definition_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id TEXT NOT NULL REFERENCES agent_definitions(id) ON DELETE CASCADE,
  revision INTEGER NOT NULL CHECK (revision > 0),
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  instructions TEXT NOT NULL,
  capabilities JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(capabilities) = 'array'),
  status TEXT NOT NULL CHECK (status IN ('draft','pending_review','approved','rejected','published')),
  proposer_id TEXT NOT NULL,
  reviewer_id TEXT,
  reviewed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(agent_id, revision),
  CHECK (reviewer_id IS NULL OR reviewer_id <> proposer_id)
);
CREATE TABLE IF NOT EXISTS agent_engagements (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES agent_projects(id) ON DELETE CASCADE,
  title TEXT NOT NULL CHECK (length(title) BETWEEN 3 AND 160),
  objective TEXT NOT NULL CHECK (length(objective) BETWEEN 10 AND 4000),
  category TEXT NOT NULL CHECK (category IN ('development','documentation','research','collaboration','review')),
  priority TEXT NOT NULL CHECK (priority IN ('low','normal','high','urgent')),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','blocked','completed','cancelled')),
  owner_id TEXT,
  due_at TIMESTAMPTZ,
  acceptance_criteria JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(acceptance_criteria) = 'array'),
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_engagements_project_status_idx ON agent_engagements(project_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS agent_engagements_owner_idx ON agent_engagements(owner_id, status);
CREATE TABLE IF NOT EXISTS agent_approval_requests (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_version_id UUID NOT NULL REFERENCES agent_definition_versions(id) ON DELETE CASCADE,
  requested_by TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','cancelled')),
  decided_by TEXT,
  comment TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ,
  CHECK (decided_by IS NULL OR decided_by <> requested_by)
);
CREATE TABLE IF NOT EXISTS agent_audit_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES agent_projects(id) ON DELETE SET NULL,
  actor_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK (length(action) BETWEEN 2 AND 100),
  entity_type TEXT NOT NULL,
  entity_id TEXT,
  request_id TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_audit_project_time_idx ON agent_audit_events(project_id, created_at DESC);
-- Grant the runtime role INSERT/SELECT on audit events only; do not grant UPDATE/DELETE.
