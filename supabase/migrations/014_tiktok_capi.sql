-- Migration 014: TikTok Events API support
-- Run via Supabase SQL Editor

-- TikTok credentials per client
ALTER TABLE clients
  ADD COLUMN IF NOT EXISTS tiktok_pixel_id     TEXT,
  ADD COLUMN IF NOT EXISTS tiktok_access_token TEXT;

-- TikTok click ID on visitors (captured from ?ttclid= URL param by tracker.js)
ALTER TABLE visitors
  ADD COLUMN IF NOT EXISTS ttclid TEXT;

CREATE INDEX IF NOT EXISTS visitors_ttclid_idx
  ON visitors (ttclid)
  WHERE ttclid IS NOT NULL;

COMMENT ON COLUMN clients.tiktok_pixel_id     IS 'TikTok Pixel Code (e.g. C3XXXXXXXXXXXX)';
COMMENT ON COLUMN clients.tiktok_access_token IS 'TikTok Events API access token';
COMMENT ON COLUMN visitors.ttclid             IS 'TikTok click ID from ?ttclid= URL param (90-day cookie)';
