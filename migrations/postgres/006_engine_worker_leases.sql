-- Durable worker ownership with fencing tokens for crash recovery and stale-worker detection.
CREATE TABLE IF NOT EXISTS control_api_engine_worker_leases (
    session_id TEXT PRIMARY KEY REFERENCES control_api_engine_sessions(session_id) ON DELETE CASCADE,
    worker_id TEXT NOT NULL,
    lease_expires_at BIGINT NOT NULL CHECK (lease_expires_at >= 0),
    fencing_token BIGINT NOT NULL CHECK (fencing_token >= 1)
);
