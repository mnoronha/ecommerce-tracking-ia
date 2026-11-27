-- Migration 087: Isolated audit table for internal test email deliveries (PRD §16.3 / R02).
--
-- Strictly separated from commercial delivery receipts (`core_canonical_report_records`
-- `delivery:*`) and commercial delivery settings (`core_report_delivery_settings`).
-- Enforces DB-level defense-in-depth invariants:
--   - event_type = 'TEST_DELIVERY'
--   - recipient_allowlist_email = 'contato@norolabs.com.br'
--   - cta_authorized = false
--   - atomic idempotency via UNIQUE (report_id, report_version, idempotency_key)

CREATE TABLE IF NOT EXISTS public.core_canonical_report_test_deliveries (
    id                        uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type                text        NOT NULL DEFAULT 'TEST_DELIVERY'
                                          CHECK (event_type = 'TEST_DELIVERY'),
    idempotency_key           text        NOT NULL,
    report_id                 text        NOT NULL,
    report_version            integer     NOT NULL CHECK (report_version >= 1),
    artifact_hash             text        NOT NULL,
    report_status_at_send     text        NOT NULL
                                          CHECK (report_status_at_send IN ('DRAFT', 'READY_FOR_REVIEW', 'APPROVED', 'PUBLISHED')),
    client_id                 text        NOT NULL,
    report_type               text        NOT NULL CHECK (report_type IN ('weekly', 'monthly')),
    channel                   text        NOT NULL DEFAULT 'EMAIL' CHECK (channel = 'EMAIL'),
    recipient_allowlist_email text        NOT NULL DEFAULT 'contato@norolabs.com.br'
                                          CHECK (recipient_allowlist_email = 'contato@norolabs.com.br'),
    recipient_hash            text        NOT NULL,
    subject                   text        NOT NULL,
    cta_authorized            boolean     NOT NULL DEFAULT false CHECK (cta_authorized = false),
    actor                     text        NOT NULL,
    status                    text        NOT NULL
                                          CHECK (status IN ('PENDING', 'SENT', 'FAILED', 'RENDER_FAILED')),
    provider_message_id       text,
    error_code                text,
    created_at                timestamptz NOT NULL DEFAULT now(),
    sent_at                   timestamptz,
    CONSTRAINT uq_canonical_report_test_del_idempotency
        UNIQUE (report_id, report_version, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_test_del_report_version_created
    ON public.core_canonical_report_test_deliveries (report_id, report_version, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_test_del_actor_created
    ON public.core_canonical_report_test_deliveries (actor, created_at DESC);

ALTER TABLE public.core_canonical_report_test_deliveries ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.core_canonical_report_test_deliveries FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.core_canonical_report_test_deliveries TO service_role;
