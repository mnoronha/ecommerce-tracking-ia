-- Migration 071 — Media Change Log v2
--
-- Extends core_change_log (created in 039_core_m04_change_log.sql) to match the
-- canonical Media Change Contract: traceability links, workflow fields, outcome
-- fields, and expanded change_type / source enums for Hermes-detected changes.
--
-- Additive only: no rows modified, no existing columns altered.
-- Safe to re-run (IF NOT EXISTS / IF EXISTS guards throughout).

BEGIN;

-- ── 1. New columns ─────────────────────────────────────────────────────────────

ALTER TABLE public.core_change_log
  ADD COLUMN IF NOT EXISTS entity_id         text,
  ADD COLUMN IF NOT EXISTS description       text,
  ADD COLUMN IF NOT EXISTS requested_by      text,
  ADD COLUMN IF NOT EXISTS approved_by       text,
  ADD COLUMN IF NOT EXISTS executed_by       text,
  ADD COLUMN IF NOT EXISTS recommendation_id uuid,
  ADD COLUMN IF NOT EXISTS task_id           uuid,
  ADD COLUMN IF NOT EXISTS report_id         uuid,
  ADD COLUMN IF NOT EXISTS detected_at       timestamptz,
  ADD COLUMN IF NOT EXISTS expected_impact   jsonb,
  ADD COLUMN IF NOT EXISTS risk_level        text
                             CHECK (risk_level IS NULL
                               OR risk_level IN ('LOW','MEDIUM','HIGH')),
  ADD COLUMN IF NOT EXISTS outcome_status    text
                             CHECK (outcome_status IS NULL
                               OR outcome_status IN (
                                 'PENDING','MEASURING','CONFIRMED',
                                 'INCONCLUSIVE','POSITIVE','NEGATIVE',
                                 'NEUTRAL','BLOCKED_DATA_QUALITY'
                               )),
  ADD COLUMN IF NOT EXISTS outcome_summary   text,
  ADD COLUMN IF NOT EXISTS updated_at        timestamptz;

-- ── 2. Expand change_type CHECK ────────────────────────────────────────────────

ALTER TABLE public.core_change_log
  DROP CONSTRAINT IF EXISTS core_change_log_change_type_check;

ALTER TABLE public.core_change_log
  ADD CONSTRAINT core_change_log_change_type_check CHECK (change_type IN (
    -- original 13
    'BUDGET','TARGET_ROAS','TARGET_CPA','BID_STRATEGY',
    'CREATIVE_LAUNCH','CREATIVE_PAUSE',
    'CAMPAIGN_LAUNCH','CAMPAIGN_PAUSE',
    'AUDIENCE','LANDING_PAGE','OFFER','TRACKING','OTHER',
    -- new 13 (Hermes / platform-detected)
    'ADSET_STATUS_CHANGE','AD_STATUS_CHANGE',
    'KEYWORD_ADDED','KEYWORD_REMOVED','NEGATIVE_KEYWORD_ADDED',
    'MATCH_TYPE_CHANGE','PLACEMENT_CHANGE',
    'CREATIVE_ADDED','CREATIVE_REPLACED',
    'ASSET_CHANGE','PMAX_ASSET_GROUP_CHANGE',
    'SHOPPING_PRODUCT_SCOPE_CHANGE','CONVERSION_ACTION_CHANGE'
  ));

-- ── 3. Expand source CHECK ─────────────────────────────────────────────────────

ALTER TABLE public.core_change_log
  DROP CONSTRAINT IF EXISTS core_change_log_source_check;

ALTER TABLE public.core_change_log
  ADD CONSTRAINT core_change_log_source_check CHECK (source IN (
    -- original 3
    'AUTO_GOOGLE_ADS','AUTO_META','HUMAN',
    -- new 3
    'HERMES_RECOMMENDATION','AUTOMATION','PLATFORM_DETECTED'
  ));

-- ── 4. Indexes for new filter columns ─────────────────────────────────────────

CREATE INDEX IF NOT EXISTS core_change_log_task_id_idx
  ON public.core_change_log (task_id)
  WHERE task_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS core_change_log_recommendation_id_idx
  ON public.core_change_log (recommendation_id)
  WHERE recommendation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS core_change_log_source_idx
  ON public.core_change_log (client_id, source);

CREATE INDEX IF NOT EXISTS core_change_log_change_type_idx
  ON public.core_change_log (client_id, change_type);

COMMENT ON COLUMN public.core_change_log.entity_id IS
  'Platform-native ID of the changed entity (e.g. Google campaign_id).';
COMMENT ON COLUMN public.core_change_log.recommendation_id IS
  'FK to core_recommendations.id — the recommendation that triggered this change.';
COMMENT ON COLUMN public.core_change_log.task_id IS
  'FK to core_tasks.id — the task linked to this change.';
COMMENT ON COLUMN public.core_change_log.detected_at IS
  'When Hermes / platform first detected the change (vs occurred_at = when it happened).';
COMMENT ON COLUMN public.core_change_log.outcome_status IS
  'Outcome loop closure: PENDING until measured.';

COMMIT;
