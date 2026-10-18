-- Control API-compatible schema. Kept separate from the earlier generic
-- identity-based schema so API identifiers and PBKDF2 byte fields remain lossless.
CREATE TABLE IF NOT EXISTS control_api_users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    salt BYTEA NOT NULL,
    password_hash BYTEA NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS control_api_users_email_ci_idx
    ON control_api_users(lower(email));
CREATE TABLE IF NOT EXISTS control_api_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES control_api_users(id) ON DELETE CASCADE,
    expires_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS control_api_sessions_user_expiry_idx
    ON control_api_sessions(user_id, expires_at);
CREATE TABLE IF NOT EXISTS control_api_exchange_credentials (
    user_id TEXT NOT NULL REFERENCES control_api_users(id) ON DELETE CASCADE,
    exchange_id TEXT NOT NULL,
    encrypted_credentials BYTEA NOT NULL,
    auth_mode TEXT NOT NULL,
    state TEXT NOT NULL,
    last_verified TEXT,
    verification_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(user_id, exchange_id)
);
