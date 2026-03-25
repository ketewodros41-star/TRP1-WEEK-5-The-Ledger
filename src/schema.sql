-- src/schema.sql

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stream_id TEXT NOT NULL,
    stream_position BIGINT NOT NULL,
    global_position BIGINT GENERATED ALWAYS AS IDENTITY,
    event_type TEXT NOT NULL,
    event_version SMALLINT NOT NULL DEFAULT 1,
    payload JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_stream_id_position UNIQUE (stream_id, stream_position)
);

CREATE INDEX IF NOT EXISTS idx_events_stream_id_pos ON events (stream_id, stream_position);
CREATE INDEX IF NOT EXISTS idx_events_global_pos ON events (global_position);
CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type);
CREATE INDEX IF NOT EXISTS idx_events_recorded_at ON events (recorded_at);

CREATE TABLE IF NOT EXISTS event_streams (
    stream_id TEXT PRIMARY KEY,
    aggregate_type TEXT NOT NULL,
    current_version BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS projection_checkpoints (
    projection_name TEXT PRIMARY KEY,
    last_position BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID NOT NULL REFERENCES events(event_id),
    destination TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ,
    attempts SMALLINT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_outbox_unpublished ON outbox (published_at, created_at) WHERE published_at IS NULL;

CREATE TABLE IF NOT EXISTS read_application_summary (
    application_id TEXT PRIMARY KEY,
    applicant_id TEXT NOT NULL,
    requested_amount NUMERIC NOT NULL,
    current_state TEXT NOT NULL,
    latest_recommendation TEXT,
    confidence_score DOUBLE PRECISION,
    recommended_limit NUMERIC,
    approved_amount NUMERIC,
    compliance_verdict TEXT,
    last_updated TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS read_agent_performance (
    agent_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    total_analyses BIGINT NOT NULL DEFAULT 0,
    override_count BIGINT NOT NULL DEFAULT 0,
    override_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
    avg_confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (agent_id, model_version)
);

CREATE TABLE IF NOT EXISTS read_compliance_audit_logs (
    id BIGSERIAL PRIMARY KEY,
    application_id TEXT NOT NULL,
    event_id UUID NOT NULL,
    event_type TEXT NOT NULL,
    rule_id TEXT,
    status TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    recorded_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_compliance_audit_app_time ON read_compliance_audit_logs(application_id, recorded_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_read_compliance_audit_event_id ON read_compliance_audit_logs(event_id);

CREATE TABLE IF NOT EXISTS read_compliance_verdicts (
    application_id TEXT PRIMARY KEY,
    final_verdict TEXT NOT NULL,
    verified_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS read_compliance_audit_snapshots (
    snapshot_id BIGSERIAL PRIMARY KEY,
    built_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_event_count BIGINT NOT NULL
);
