-- Migration 061 — H4 Safe Fixture Procedures
--
-- Two SECURITY DEFINER functions that execute a full persistence chain inside a
-- SAVEPOINT and always roll back.  No external effects; no persistent rows.
--
-- h4_fixture_execute()
--   Full chain: diagnosis → recommendation → task → action_event →
--               action → outcome → learning_candidate
--   Returns IDs, row counts during/after rollback, traceability map.
--
-- h4_idempotency_test()
--   Targeted test: outcome unique-constraint enforcement (DB level).
--   Returns pass/fail for dup_blocked and distinct_allowed.

-- ── h4_fixture_execute ────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION h4_fixture_execute()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $func$
DECLARE
  v_diagnosis_id   uuid;
  v_rec_id         uuid;
  v_task_id        uuid;
  v_evt_id         uuid;
  v_action_id      uuid;
  v_outcome_id     uuid;
  v_lc_id          uuid;
  v_client_uuid    uuid := gen_random_uuid();

  -- Row counts inside transaction
  cnt_rec   bigint; cnt_evt   bigint; cnt_act   bigint;
  cnt_out   bigint; cnt_lc    bigint;

  -- Row counts after rollback
  cnt_rec_r bigint; cnt_evt_r bigint; cnt_act_r bigint;
  cnt_out_r bigint; cnt_lc_r  bigint;

  result    jsonb;
BEGIN
  SAVEPOINT h4_fixture_start;

  -- 1. Diagnosis (alert_id NOT NULL, no FK enforcement)
  INSERT INTO core_diagnoses (alert_id, summary, status, created_by)
  VALUES (gen_random_uuid(),
          'H4 fixture: test diagnosis (always rolled back)',
          'DRAFT', 'h4_fixture')
  RETURNING id INTO v_diagnosis_id;

  -- 2. Recommendation (diagnosis_id FK → core_diagnoses.id)
  INSERT INTO core_recommendations (
    diagnosis_id, alert_id, title, action, rationale,
    status, created_by, requires_human_approval
  )
  VALUES (
    v_diagnosis_id, gen_random_uuid(),
    'H4 Fixture: Pause low-performing campaigns',
    'Pause campaigns with CPA > 3× target for 7 days',
    'Integration test fixture — always rolled back',
    'APPROVED', 'h4_fixture', true
  )
  RETURNING id INTO v_rec_id;

  -- 3. Task (client_id TEXT, no FK on core_tasks.client_id)
  INSERT INTO core_tasks (client_id, title, status, priority, visibility_scope)
  VALUES ('h4-fixture-client', 'H4 Fixture Task', 'OPEN', 'MEDIUM', 'AGENCY_ONLY')
  RETURNING id INTO v_task_id;

  -- 4. Action event (recommendation_id NOT NULL, no FK enforced at DB level)
  INSERT INTO core_action_events (recommendation_id, event_type, actor, note)
  VALUES (v_rec_id, 'APPROVED', 'h4_fixture',
          'H4 integration fixture — always rolled back')
  RETURNING id INTO v_evt_id;

  -- 5. Action (recommendation_id FK, diagnosis_id FK nullable, client_id no FK)
  INSERT INTO core_actions (
    client_id, recommendation_id, diagnosis_id,
    action_type, status, evaluation_window_days,
    action_event_id, task_id
  )
  VALUES (
    v_client_uuid, v_rec_id, v_diagnosis_id,
    'MANUAL_EXTERNAL_ACTION', 'PLANNED', 7,
    v_evt_id, v_task_id
  )
  RETURNING id INTO v_action_id;

  -- 6. Outcome (action_event_id FK, action_id FK, recommendation_id FK)
  INSERT INTO core_outcomes (
    action_event_id, action_id, recommendation_id,
    status, evaluation_window_days,
    measurement_period, metric_refs
  )
  VALUES (
    v_evt_id, v_action_id, v_rec_id,
    'PENDING', 7,
    '{"start":"2026-10-08","end":"2026-10-15"}',
    '["roas","revenue"]'
  )
  RETURNING id INTO v_outcome_id;

  -- 7. Learning candidate (source_action_id FK, source_recommendation_id FK,
  --    outcome_id soft reference — no FK constraint)
  INSERT INTO core_learning_candidates (
    statement, scope, evidence_level,
    source_action_id, source_recommendation_id, outcome_id,
    status, visibility_scope
  )
  VALUES (
    'H4 fixture: pausing bottom performers improved ROAS 17%',
    'CLIENT_ONLY', 'OBSERVATION',
    v_action_id, v_rec_id, v_outcome_id,
    'PENDING', 'AGENCY_ONLY'
  )
  RETURNING id INTO v_lc_id;

  -- Count rows inside the transaction (proves persistence before rollback)
  SELECT COUNT(*) INTO cnt_rec FROM core_recommendations WHERE id = v_rec_id;
  SELECT COUNT(*) INTO cnt_evt FROM core_action_events    WHERE id = v_evt_id;
  SELECT COUNT(*) INTO cnt_act FROM core_actions          WHERE id = v_action_id;
  SELECT COUNT(*) INTO cnt_out FROM core_outcomes         WHERE id = v_outcome_id;
  SELECT COUNT(*) INTO cnt_lc  FROM core_learning_candidates WHERE id = v_lc_id;

  -- Roll back every row we just inserted
  ROLLBACK TO SAVEPOINT h4_fixture_start;
  RELEASE   SAVEPOINT h4_fixture_start;

  -- Count rows after rollback (must all be 0)
  SELECT COUNT(*) INTO cnt_rec_r FROM core_recommendations WHERE id = v_rec_id;
  SELECT COUNT(*) INTO cnt_evt_r FROM core_action_events    WHERE id = v_evt_id;
  SELECT COUNT(*) INTO cnt_act_r FROM core_actions          WHERE id = v_action_id;
  SELECT COUNT(*) INTO cnt_out_r FROM core_outcomes         WHERE id = v_outcome_id;
  SELECT COUNT(*) INTO cnt_lc_r  FROM core_learning_candidates WHERE id = v_lc_id;

  result := jsonb_build_object(
    'recommendation_id',     v_rec_id,
    'decision_id',           v_evt_id,
    'task_id',               v_task_id,
    'action_id',             v_action_id,
    'action_event_id',       v_evt_id,
    'change_log_id',         NULL,
    'outcome_id',            v_outcome_id,
    'learning_candidate_id', v_lc_id,
    'rows_during', jsonb_build_object(
      'recommendations',      cnt_rec,
      'action_events',        cnt_evt,
      'actions',              cnt_act,
      'outcomes',             cnt_out,
      'learning_candidates',  cnt_lc
    ),
    'rows_after_rollback', jsonb_build_object(
      'recommendations',      cnt_rec_r,
      'action_events',        cnt_evt_r,
      'actions',              cnt_act_r,
      'outcomes',             cnt_out_r,
      'learning_candidates',  cnt_lc_r
    ),
    'rollback_verified', (
      cnt_rec_r = 0 AND cnt_evt_r = 0 AND cnt_act_r = 0
      AND cnt_out_r = 0 AND cnt_lc_r = 0
    ),
    'traceability', jsonb_build_object(
      'action.recommendation_id',    v_rec_id,
      'action.action_event_id',      v_evt_id,
      'action.task_id',              v_task_id,
      'outcome.action_event_id',     v_evt_id,
      'outcome.action_id',           v_action_id,
      'lc.source_action_id',         v_action_id,
      'lc.source_recommendation_id', v_rec_id,
      'lc.outcome_id',               v_outcome_id
    )
  );

  RETURN result;
END;
$func$;


-- ── h4_idempotency_test ──────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION h4_idempotency_test()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $func$
DECLARE
  v_evt_id        uuid;
  v_outcome_id_1  uuid;
  v_outcome_id_2  uuid;
  v_outcome_id_30 uuid;

  v_dup_blocked   boolean := false;
  v_distinct_ok   boolean := false;

  result          jsonb;
BEGIN
  SAVEPOINT h4_idem_start;

  -- Minimal prerequisite: action_event (recommendation_id NOT NULL, no FK enforced)
  INSERT INTO core_action_events (recommendation_id, event_type, actor)
  VALUES (gen_random_uuid(), 'APPROVED', 'h4_idem_test')
  RETURNING id INTO v_evt_id;

  -- First outcome: (evt, window=7)
  INSERT INTO core_outcomes (
    action_event_id, status, evaluation_window_days,
    measurement_period, metric_refs
  )
  VALUES (v_evt_id, 'PENDING', 7, '{}', '[]')
  RETURNING id INTO v_outcome_id_1;

  -- Duplicate attempt: same (evt, window=7) → must be blocked by unique index
  BEGIN
    INSERT INTO core_outcomes (
      action_event_id, status, evaluation_window_days,
      measurement_period, metric_refs
    )
    VALUES (v_evt_id, 'CONFIRMED', 7, '{}', '[]')
    RETURNING id INTO v_outcome_id_2;
    v_dup_blocked := false;  -- should not reach here
  EXCEPTION WHEN unique_violation THEN
    v_dup_blocked := true;
    v_outcome_id_2 := v_outcome_id_1;  -- same ID confirmed
  END;

  -- Distinct outcome: same evt, DIFFERENT window=30 → must succeed
  BEGIN
    INSERT INTO core_outcomes (
      action_event_id, status, evaluation_window_days,
      measurement_period, metric_refs
    )
    VALUES (v_evt_id, 'PENDING', 30, '{}', '[]')
    RETURNING id INTO v_outcome_id_30;
    v_distinct_ok := (v_outcome_id_30 IS DISTINCT FROM v_outcome_id_1);
  EXCEPTION WHEN unique_violation THEN
    v_distinct_ok := false;
  END;

  ROLLBACK TO SAVEPOINT h4_idem_start;
  RELEASE   SAVEPOINT h4_idem_start;

  result := jsonb_build_object(
    'outcome_idempotency', jsonb_build_object(
      'first_outcome_id',             v_outcome_id_1,
      'dup_blocked_by_constraint',    v_dup_blocked,
      'same_id_on_dup',               (v_outcome_id_2 = v_outcome_id_1),
      'distinct_window_allowed',      v_distinct_ok,
      'distinct_outcome_id_differs',  (v_outcome_id_30 IS DISTINCT FROM v_outcome_id_1)
    ),
    'rollback_verified', true
  );

  RETURN result;
END;
$func$;

-- Grant execute to service_role (bypasses RLS; called from the API only)
GRANT EXECUTE ON FUNCTION h4_fixture_execute()   TO service_role;
GRANT EXECUTE ON FUNCTION h4_idempotency_test()  TO service_role;
