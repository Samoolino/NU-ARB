-- Durable owner-to-engine-session mapping. Deliberately excludes live mode until a separately approved execution design exists.
CREATE TABLE IF NOT EXISTS control_api_engine_sessions (
    session_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES control_api_users(id) ON DELETE CASCADE,
    mode TEXT NOT NULL CHECK (mode IN ('paper', 'simulation')),
    phase TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    stopped_at TEXT,
    error_code TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS control_api_engine_sessions_owner_created_idx
    ON control_api_engine_sessions(owner_user_id, created_at DESC);
