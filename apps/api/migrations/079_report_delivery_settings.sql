-- 079_report_delivery_settings.sql
-- Delivery settings per client: send eligibility flags and recipient list.
-- Recipients are agency/internal emails (not customer PII) stored in plaintext.
-- human_approval_required is always TRUE: this table only tracks eligibility,
-- not auto-send. The actual send always requires a human transition to PUBLISHED.

CREATE TABLE IF NOT EXISTS core_report_delivery_settings (
  id                      uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id               text        NOT NULL,
  weekly_enabled          boolean     NOT NULL DEFAULT false,
  monthly_enabled         boolean     NOT NULL DEFAULT false,
  human_approval_required boolean     NOT NULL DEFAULT true,
  recipients              jsonb       NOT NULL DEFAULT '[]'::jsonb,
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS core_report_delivery_settings_client_id_key
  ON core_report_delivery_settings (client_id);

ALTER TABLE core_report_delivery_settings ENABLE ROW LEVEL SECURITY;
