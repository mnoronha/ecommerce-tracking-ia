-- Migration 057 — Phase 7: visitor_cookie_id is optional for server-side ingest events
-- TrackingEventIn.visitor_cookie_id is Optional[str]; server-side sends have no browser cookie.
ALTER TABLE tracking_events ALTER COLUMN visitor_cookie_id DROP NOT NULL;
