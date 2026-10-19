-- Versioned, checksummed checkpoints are written only with a current worker fence.
CREATE TABLE IF NOT EXISTS control_api_engine_checkpoints (
    session_id TEXT PRIMARY KEY REFERENCES control_api_engine_sessions(session_id) ON DELETE CASCADE,
    checkpoint_version BIGINT NOT NULL CHECK (checkpoint_version >= 1),
    checkpoint_json TEXT NOT NULL,
    checkpoint_sha256 TEXT NOT NULL CHECK (length(checkpoint_sha256) = 64),
    updated_at TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    fencing_token BIGINT NOT NULL CHECK (fencing_token >= 1)
);
