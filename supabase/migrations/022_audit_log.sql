-- Trilha de auditoria: registra INSERT/UPDATE/DELETE em tabelas de configuração
-- (clients, goals, budgets, alert_rules) via trigger no Postgres, então captura
-- mudanças venham do backend OU do frontend (que grava direto no Supabase).
-- Segredos são redigidos (***) para nunca cair no log.

CREATE TABLE IF NOT EXISTS audit_log (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  table_name  text NOT NULL,
  op          text NOT NULL,          -- INSERT | UPDATE | DELETE
  row_id      text,
  actor       uuid,                   -- auth.uid() quando vem do frontend logado; null no backend/cron
  old_data    jsonb,
  new_data    jsonb,
  changed_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_log_table_time ON audit_log (table_name, changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_row        ON audit_log (table_name, row_id);

-- Trancado: só service_role (ignora RLS) lê. Sem política = sem acesso anon/authenticated.
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION audit_log_capture() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_actor uuid;
  redact  text[] := ARRAY[
    'ga4_api_secret','google_ads_refresh_token','meta_access_token','nuvemshop_access_token',
    'pinterest_access_token','shopify_access_token','shopify_webhook_secret','tiktok_access_token',
    'tracking_cname_secret','webhook_secret','woo_consumer_key','woo_consumer_secret','woo_webhook_secret'
  ];
  old_j jsonb;
  new_j jsonb;
  k text;
BEGIN
  BEGIN v_actor := auth.uid(); EXCEPTION WHEN OTHERS THEN v_actor := NULL; END;
  IF TG_OP <> 'INSERT' THEN old_j := to_jsonb(OLD); END IF;
  IF TG_OP <> 'DELETE' THEN new_j := to_jsonb(NEW); END IF;
  FOREACH k IN ARRAY redact LOOP
    IF old_j ? k THEN old_j := jsonb_set(old_j, ARRAY[k], '"***"'); END IF;
    IF new_j ? k THEN new_j := jsonb_set(new_j, ARRAY[k], '"***"'); END IF;
  END LOOP;
  BEGIN
    INSERT INTO audit_log(table_name, op, row_id, actor, old_data, new_data)
    VALUES (TG_TABLE_NAME, TG_OP, COALESCE(new_j->>'id', old_j->>'id'), v_actor, old_j, new_j);
  EXCEPTION WHEN OTHERS THEN
    NULL;  -- auditoria nunca pode derrubar a escrita real
  END;
  RETURN COALESCE(NEW, OLD);
END;
$$;

DROP TRIGGER IF EXISTS audit_clients     ON clients;
DROP TRIGGER IF EXISTS audit_goals       ON goals;
DROP TRIGGER IF EXISTS audit_budgets     ON budgets;
DROP TRIGGER IF EXISTS audit_alert_rules ON alert_rules;

CREATE TRIGGER audit_clients     AFTER INSERT OR UPDATE OR DELETE ON clients     FOR EACH ROW EXECUTE FUNCTION audit_log_capture();
CREATE TRIGGER audit_goals       AFTER INSERT OR UPDATE OR DELETE ON goals       FOR EACH ROW EXECUTE FUNCTION audit_log_capture();
CREATE TRIGGER audit_budgets     AFTER INSERT OR UPDATE OR DELETE ON budgets     FOR EACH ROW EXECUTE FUNCTION audit_log_capture();
CREATE TRIGGER audit_alert_rules AFTER INSERT OR UPDATE OR DELETE ON alert_rules FOR EACH ROW EXECUTE FUNCTION audit_log_capture();
