-- 081_delivery_settings_timing.sql
-- Add timing columns to core_report_delivery_settings.
-- These fields exist in the API schema (canonical_reports.py DeliverySettingsIn)
-- but were missing from the original migration 079.
-- Safe ADD COLUMN IF NOT EXISTS; existing rows get the defaults.

ALTER TABLE core_report_delivery_settings
  ADD COLUMN IF NOT EXISTS weekly_day_of_week  smallint NOT NULL DEFAULT 1 CHECK (weekly_day_of_week  BETWEEN 0 AND 6),
  ADD COLUMN IF NOT EXISTS weekly_hour         smallint NOT NULL DEFAULT 8  CHECK (weekly_hour         BETWEEN 0 AND 23),
  ADD COLUMN IF NOT EXISTS monthly_day_of_month smallint NOT NULL DEFAULT 1 CHECK (monthly_day_of_month BETWEEN 1 AND 28),
  ADD COLUMN IF NOT EXISTS monthly_hour        smallint NOT NULL DEFAULT 8  CHECK (monthly_hour        BETWEEN 0 AND 23),
  ADD COLUMN IF NOT EXISTS report_timezone     text     NOT NULL DEFAULT 'America/Sao_Paulo';
