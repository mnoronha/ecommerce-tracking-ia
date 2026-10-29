-- 078 — core_change_digests
-- Derived layer over core_change_log: relevant changes per (client, platform, period).
-- Core is canonical; this table caches the classified + grouped digest for Hermes + Reports.

CREATE TABLE IF NOT EXISTS core_change_digests (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id           TEXT        NOT NULL,
    platform            TEXT        NOT NULL,           -- 'meta' | 'google'
    period_start        DATE        NOT NULL,
    period_end          DATE        NOT NULL,
    engine_version      TEXT        NOT NULL DEFAULT '1',
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_raw_count     INTEGER     NOT NULL DEFAULT 0,
    relevant_count      INTEGER     NOT NULL DEFAULT 0,
    noise_count         INTEGER     NOT NULL DEFAULT 0,
    changes             JSONB       NOT NULL DEFAULT '[]',
    summary             JSONB       NOT NULL DEFAULT '{}',
    limitations         JSONB       NOT NULL DEFAULT '[]',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (client_id, platform, period_start, period_end)
);

CREATE INDEX IF NOT EXISTS idx_change_digests_client_period
    ON core_change_digests (client_id, period_start DESC, period_end DESC);

CREATE INDEX IF NOT EXISTS idx_change_digests_platform
    ON core_change_digests (platform, period_start DESC);

ALTER TABLE core_change_digests ENABLE ROW LEVEL SECURITY;
