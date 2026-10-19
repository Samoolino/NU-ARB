-- Support bounded, owner-scoped lookup of the latest durable engine session.
CREATE INDEX IF NOT EXISTS control_api_engine_sessions_owner_updated_idx
    ON control_api_engine_sessions(owner_user_id, updated_at DESC, created_at DESC, session_id ASC);
