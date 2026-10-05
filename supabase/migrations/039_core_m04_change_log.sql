-- M04: core_change_log — log de mudanças em plataformas, append-only
-- Rollback: DROP TABLE IF EXISTS public.core_change_log;
BEGIN;

CREATE TABLE IF NOT EXISTS public.core_change_log (
  id                   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id            text        NOT NULL
                         REFERENCES public.clients(client_id)
                         ON DELETE RESTRICT,
  occurred_at          timestamptz NOT NULL,
  channel              text        NOT NULL,
  platform_account_id  text        NOT NULL,
  entity_type          text        NOT NULL,
  entity_name_at_time  text        NOT NULL,
  change_type          text        NOT NULL
                         CHECK (change_type IN (
                           'BUDGET','TARGET_ROAS','TARGET_CPA','BID_STRATEGY',
                           'CREATIVE_LAUNCH','CREATIVE_PAUSE',
                           'CAMPAIGN_LAUNCH','CAMPAIGN_PAUSE',
                           'AUDIENCE','LANDING_PAGE','OFFER','TRACKING','OTHER'
                         )),
  confidence           text        NOT NULL DEFAULT 'CONFIRMED'
                         CHECK (confidence IN ('CONFIRMED','PROBABLE','UNKNOWN')),
  source               text        NOT NULL
                         CHECK (source IN ('AUTO_GOOGLE_ADS','AUTO_META','HUMAN')),
  campaign_id          text,
  adset_or_adgroup_id  text,
  ad_id                text,
  before_state         jsonb,
  after_state          jsonb,
  reason               text,
  reported_by          text,
  linked_action_id     uuid,
  external_change_id   text,
  match_status         text
                         CHECK (match_status IS NULL OR match_status IN (
                           'MATCHED','AUTO_PENDING_CONTEXT','HUMAN_UNCONFIRMED'
                         )),
  matched_change_id    uuid,
  created_at           timestamptz NOT NULL DEFAULT now()
);

-- RLS_BOOTSTRAP
ALTER TABLE public.core_change_log ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_change_log FROM anon, authenticated;
GRANT SELECT, INSERT ON public.core_change_log TO service_role;

CREATE INDEX IF NOT EXISTS core_change_log_client_occurred_idx
  ON public.core_change_log (client_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS core_change_log_external_idx
  ON public.core_change_log (external_change_id)
  WHERE external_change_id IS NOT NULL;

COMMENT ON TABLE public.core_change_log IS
  'Agency API ChangeOut. Hermes escreve. Append-only. RLS_BOOTSTRAP.';

COMMIT;
