-- M17: core_balance_snapshots
-- Balance snapshots for prepaid Google Ads and Meta ad accounts.
-- Written by balance_monitor hourly; read by the Agency API.
-- Primary alert rule: estimated_days_remaining (balance ÷ avg_daily_spend).
-- Absolute threshold is fallback only when no spend reference exists.

CREATE TABLE IF NOT EXISTS core_balance_snapshots (
    id                     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id              text        NOT NULL,
    platform               text        NOT NULL
                                       CHECK (platform IN ('google', 'meta')),
    snapshot_at            timestamptz NOT NULL DEFAULT now(),
    billing_model          text,       -- 'prepaid' | 'postpaid' | NULL
    collection_status      text,       -- 'PASS' | 'BLOCKED' | 'PERMISSION_DENIED' | 'NOT_SUPPORTED'
    balance                numeric,    -- major currency units; NULL = NOT_COLLECTED
    balance_status         text        NOT NULL
                                       CHECK (balance_status IN (
                                         'OK','LOW','CRITICAL','EXHAUSTED',
                                         'NO_DATA','MISSING','PERMISSION_DENIED','NOT_SUPPORTED'
                                       )),
    avg_daily_spend        numeric,    -- avg spend in major units (3-day preferred, 7-day fallback)
    spend_avg_window_days  integer,    -- window queried (3 or 7)
    spend_data_days        integer,    -- actual days with spend > 0 found in window
    estimated_days_remaining numeric,  -- balance / avg_daily_spend; NULL when no spend data
    threshold_low          numeric,    -- from client config at time of snapshot
    threshold_critical     numeric,    -- threshold_low * 0.5
    currency               text,
    raw                    jsonb,      -- full API response (no tokens)
    error                  text,
    created_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_core_balance_client_platform
    ON core_balance_snapshots (client_id, platform, snapshot_at DESC);

ALTER TABLE core_balance_snapshots ENABLE ROW LEVEL SECURITY;
