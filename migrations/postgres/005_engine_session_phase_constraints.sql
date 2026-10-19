-- Constrain durable engine phases to the lifecycle vocabulary used by ControlPlaneStore.
ALTER TABLE control_api_engine_sessions
    ADD CONSTRAINT control_api_engine_sessions_phase_check
    CHECK (phase IN ('CREATED', 'STARTING', 'PREFLIGHTING', 'RUNNING', 'STOPPING', 'STOPPED', 'FAILED', 'PREFLIGHT_FAILED'));
