-- Migration 017: fix device_type check constraint on tracking_events
-- The constraint was added manually without 'bot', causing 400 errors
-- when the pixel detects crawler user-agents.

ALTER TABLE public.tracking_events
  DROP CONSTRAINT IF EXISTS tracking_events_device_type_check;

ALTER TABLE public.tracking_events
  ADD CONSTRAINT tracking_events_device_type_check
  CHECK (device_type IN ('desktop', 'mobile', 'tablet', 'bot', 'unknown'));
