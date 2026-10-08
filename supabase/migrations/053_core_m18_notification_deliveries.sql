-- M18: core_notification_deliveries
-- Persistent delivery state for operational alert notifications (Telegram, etc.).
-- Survives restarts/redeploys — used by balance_notifier for idempotent dedup.
--
-- Dedup rule: if a 'sent' row exists for (alert_id, channel), do not resend.
-- Severity escalation is handled by the alert engine (LOW→CRITICAL = new alert_id).
-- Failed deliveries (status='failed') are retried on the next scheduler tick.

CREATE TABLE IF NOT EXISTS core_notification_deliveries (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id            uuid        NOT NULL,           -- references alerts.id (no FK to allow alert cleanup)
    channel             text        NOT NULL
                                    CHECK (channel IN ('telegram', 'slack', 'email')),
    status              text        NOT NULL
                                    CHECK (status IN ('sent', 'failed')),
    alert_type_at_send  text,       -- alert type at time of send (audit)
    severity_at_send    text,       -- severity at time of send (audit)
    provider_response   jsonb,      -- e.g. {"message_id": 123, "chat_id": "-100..."}
    error               text,       -- populated only when status='failed'
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- Fast lookup: "has alert X been successfully delivered on channel Y?"
CREATE INDEX IF NOT EXISTS idx_notif_del_alert_channel_status
    ON core_notification_deliveries (alert_id, channel, status, created_at DESC);

ALTER TABLE core_notification_deliveries ENABLE ROW LEVEL SECURITY;
-- No public access needed; backend uses service_role key directly.
