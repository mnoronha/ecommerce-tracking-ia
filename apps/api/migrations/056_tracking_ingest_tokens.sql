-- Migration 056 — Tracking Ingest Tokens (Phase 7)
-- Per-client scoped tokens for browser snippet / server-side ingestion auth.
-- Raw token is NEVER stored — only SHA-256 hex digest.
-- These tokens grant write access to tracking ingestion ONLY; no Agency API reads or writes.
CREATE TABLE IF NOT EXISTS tracking_ingest_tokens (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID        NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
    token_hash      TEXT        NOT NULL UNIQUE,
    label           TEXT,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS tracking_ingest_tokens_client_idx
    ON tracking_ingest_tokens (client_id) WHERE (is_active = TRUE);
