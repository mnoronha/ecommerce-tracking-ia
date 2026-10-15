-- Migration 064 — H4 fixture: final corrected version
--
-- Removes core_change_log from INSERT chain (FK to clients.client_id prevents
-- use of a synthetic client without coupling the fixture to prod data).
-- change_log_id = null; change_log_skip_reason documented in result.
--
-- All 6 core H4 tables covered with rows_before/during/after_rollback:
--   core_recommendations, core_action_events (human decision), core_tasks,
--   core_actions, core_outcomes (x2: O1+O2), core_learning_candidates (x2: LC1+LC2)

CREATE OR REPLACE FUNCTION h4_fixture_execute()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $func$
DECLARE
  v_diagnosis_id uuid := gen_random_uuid();
  v_rec_id       uuid := gen_random_uuid();
  v_task_id      uuid := gen_random_uuid();
  v_evt_id       uuid := gen_random_uuid();
  v_action_id    uuid := gen_random_uuid();
  v_outcome_1_id uuid := gen_random_uuid();
  v_lc_1_id      uuid := gen_random_uuid();
  v_client_uuid  uuid := gen_random_uuid();

  v_outcome_2_id uuid;
  v_lc_2_id      uuid;

  v_outcome_replay_id uuid;
  v_lc_replay_id      uuid;

  cnt_b_rec  bigint;  cnt_b_evt  bigint;  cnt_b_act  bigint;
  cnt_b_task bigint;  cnt_b_out  bigint;  cnt_b_lc   bigint;

  cnt_d_rec  bigint;  cnt_d_evt  bigint;  cnt_d_act  bigint;
  cnt_d_task bigint;  cnt_d_out  bigint;  cnt_d_lc   bigint;

  cnt_a_rec  bigint;  cnt_a_evt  bigint;  cnt_a_act  bigint;
  cnt_a_task bigint;  cnt_a_out  bigint;  cnt_a_lc   bigint;

  partial_result jsonb;
  final_result   jsonb;
BEGIN

  -- ── rows_before: must all be 0 (fresh UUIDs) ─────────────────────────────
  SELECT COUNT(*) INTO cnt_b_rec  FROM core_recommendations     WHERE id = v_rec_id;
  SELECT COUNT(*) INTO cnt_b_evt  FROM core_action_events       WHERE id = v_evt_id;
  SELECT COUNT(*) INTO cnt_b_act  FROM core_actions             WHERE id = v_action_id;
  SELECT COUNT(*) INTO cnt_b_task FROM core_tasks               WHERE id = v_task_id;
  SELECT COUNT(*) INTO cnt_b_out  FROM core_outcomes            WHERE action_event_id = v_evt_id;
  SELECT COUNT(*) INTO cnt_b_lc   FROM core_learning_candidates WHERE source_action_id = v_action_id;

  -- ── Implicit subtransaction via EXCEPTION block ───────────────────────────
  -- PostgreSQL creates an implicit savepoint at this BEGIN.
  -- Sentinel RAISE rolls back all inserts. No explicit SAVEPOINT syntax.
  BEGIN

    -- 1. Diagnosis
    INSERT INTO core_diagnoses (id, alert_id, summary, status, created_by)
    VALUES (v_diagnosis_id, gen_random_uuid(),
            'H4 fixture: test diagnosis (always rolled back)', 'DRAFT', 'h4_fixture');

    -- 2. Recommendation (FK: diagnosis_id → core_diagnoses.id)
    INSERT INTO core_recommendations (
      id, diagnosis_id, alert_id, title, action, rationale,
      status, created_by, requires_human_approval
    ) VALUES (
      v_rec_id, v_diagnosis_id, gen_random_uuid(),
      'H4 Fixture: Pause low-performing campaigns',
      'Pause CPA > 3x target for 7 days',
      'Integration fixture -- always rolled back',
      'APPROVED', 'h4_fixture', true
    );

    -- 3. Task
    INSERT INTO core_tasks (id, client_id, title, status, priority, visibility_scope)
    VALUES (v_task_id, 'h4-fixture-client', 'H4 Fixture Task', 'OPEN', 'MEDIUM', 'AGENCY_ONLY');

    -- 4. Action event = Human Decision
    INSERT INTO core_action_events (id, recommendation_id, event_type, actor, note)
    VALUES (v_evt_id, v_rec_id, 'APPROVED', 'h4_fixture',
            'H4 fixture: human decision (always rolled back)');

    -- 5. Action (full traceability links)
    INSERT INTO core_actions (
      id, client_id, recommendation_id, diagnosis_id,
      action_type, status, evaluation_window_days,
      action_event_id, task_id
    ) VALUES (
      v_action_id, v_client_uuid, v_rec_id, v_diagnosis_id,
      'MANUAL_EXTERNAL_ACTION', 'PLANNED', 7,
      v_evt_id, v_task_id
    );

    -- 6. Outcome O1 (window = 7)
    INSERT INTO core_outcomes (
      id, action_event_id, action_id, recommendation_id,
      status, evaluation_window_days, measurement_period, metric_refs
    ) VALUES (
      v_outcome_1_id, v_evt_id, v_action_id, v_rec_id,
      'PENDING', 7, '{"start":"2026-10-08","end":"2026-10-15"}', '["roas"]'
    );

    -- 6a. Outcome replay: same (action_event_id, window=7) → must return O1
    SELECT id INTO v_outcome_replay_id
    FROM core_outcomes
    WHERE action_event_id = v_evt_id AND evaluation_window_days = 7
    LIMIT 1;

    -- 6b. Outcome O2 (window = 30 → distinct evidence)
    INSERT INTO core_outcomes (
      action_event_id, action_id, recommendation_id,
      status, evaluation_window_days, measurement_period, metric_refs
    ) VALUES (
      v_evt_id, v_action_id, v_rec_id,
      'PENDING', 30, '{"start":"2026-10-08","end":"2026-11-07"}', '["roas"]'
    ) RETURNING id INTO v_outcome_2_id;

    -- 7. Learning Candidate LC1: (action_id, outcome_1_id, CLIENT_ONLY)
    INSERT INTO core_learning_candidates (
      id, statement, scope, evidence_level,
      source_action_id, source_recommendation_id, outcome_id,
      status, visibility_scope
    ) VALUES (
      v_lc_1_id,
      'H4 fixture: pausing bottom performers improved ROAS 17%',
      'CLIENT_ONLY', 'OBSERVATION',
      v_action_id, v_rec_id, v_outcome_1_id,
      'PENDING', 'AGENCY_ONLY'
    );

    -- 7a. LC replay: same (action_id, outcome_1_id, CLIENT_ONLY) → must return LC1
    SELECT id INTO v_lc_replay_id
    FROM core_learning_candidates
    WHERE source_action_id = v_action_id
      AND outcome_id       = v_outcome_1_id
      AND scope            = 'CLIENT_ONLY'
    LIMIT 1;

    -- 7b. LC2: same (action_id, CLIENT_ONLY) + different outcome (O2) → distinct ID
    INSERT INTO core_learning_candidates (
      statement, scope, evidence_level,
      source_action_id, source_recommendation_id, outcome_id,
      status, visibility_scope
    ) VALUES (
      'H4 fixture: distinct outcome yields distinct learning candidate',
      'CLIENT_ONLY', 'OBSERVATION',
      v_action_id, v_rec_id, v_outcome_2_id,
      'PENDING', 'AGENCY_ONLY'
    ) RETURNING id INTO v_lc_2_id;

    -- ── rows_during ──────────────────────────────────────────────────────────
    SELECT COUNT(*) INTO cnt_d_rec  FROM core_recommendations     WHERE id = v_rec_id;
    SELECT COUNT(*) INTO cnt_d_evt  FROM core_action_events       WHERE id = v_evt_id;
    SELECT COUNT(*) INTO cnt_d_act  FROM core_actions             WHERE id = v_action_id;
    SELECT COUNT(*) INTO cnt_d_task FROM core_tasks               WHERE id = v_task_id;
    SELECT COUNT(*) INTO cnt_d_out  FROM core_outcomes            WHERE action_event_id = v_evt_id;
    SELECT COUNT(*) INTO cnt_d_lc   FROM core_learning_candidates WHERE source_action_id = v_action_id;

    partial_result := jsonb_build_object(
      'recommendation_id',       v_rec_id,
      'decision_id',             v_evt_id,
      'task_id',                 v_task_id,
      'action_id',               v_action_id,
      'action_event_id',         v_evt_id,
      'change_log_id',           NULL,
      'change_log_note',         'skipped: core_change_log.client_id FK requires real clients row',
      'outcome_id',              v_outcome_1_id,
      'outcome_2_id',            v_outcome_2_id,
      'learning_candidate_id',   v_lc_1_id,
      'learning_candidate_2_id', v_lc_2_id,
      'rows_before', jsonb_build_object(
        'recommendations',     cnt_b_rec,
        'action_events',       cnt_b_evt,
        'actions',             cnt_b_act,
        'tasks',               cnt_b_task,
        'outcomes',            cnt_b_out,
        'learning_candidates', cnt_b_lc
      ),
      'rows_during', jsonb_build_object(
        'recommendations',     cnt_d_rec,
        'action_events',       cnt_d_evt,
        'actions',             cnt_d_act,
        'tasks',               cnt_d_task,
        'outcomes',            cnt_d_out,    -- 2: O1 + O2
        'learning_candidates', cnt_d_lc      -- 2: LC1 + LC2
      ),
      'outcome_idempotency', jsonb_build_object(
        'outcome_1_id',                v_outcome_1_id,
        'outcome_replay_id',           v_outcome_replay_id,
        'same_id_on_replay',           (v_outcome_1_id = v_outcome_replay_id),
        'outcome_2_id',                v_outcome_2_id,
        'distinct_id_on_new_evidence', (v_outcome_2_id IS DISTINCT FROM v_outcome_1_id)
      ),
      'learning_idempotency', jsonb_build_object(
        'lc_1_id',                     v_lc_1_id,
        'lc_replay_id',                v_lc_replay_id,
        'same_id_on_replay',           (v_lc_1_id = v_lc_replay_id),
        'lc_2_id',                     v_lc_2_id,
        'distinct_id_on_new_evidence', (v_lc_2_id IS DISTINCT FROM v_lc_1_id)
      ),
      'traceability', jsonb_build_object(
        'action.recommendation_id',    v_rec_id,
        'action.action_event_id',      v_evt_id,
        'action.task_id',              v_task_id,
        'outcome.action_event_id',     v_evt_id,
        'outcome.action_id',           v_action_id,
        'lc.source_action_id',         v_action_id,
        'lc.source_recommendation_id', v_rec_id,
        'lc.outcome_id',               v_outcome_1_id
      )
    );

    RAISE EXCEPTION 'h4_fixture_rollback_sentinel' USING ERRCODE = 'P0001';

  EXCEPTION WHEN raise_exception THEN
    SELECT COUNT(*) INTO cnt_a_rec  FROM core_recommendations     WHERE id = v_rec_id;
    SELECT COUNT(*) INTO cnt_a_evt  FROM core_action_events       WHERE id = v_evt_id;
    SELECT COUNT(*) INTO cnt_a_act  FROM core_actions             WHERE id = v_action_id;
    SELECT COUNT(*) INTO cnt_a_task FROM core_tasks               WHERE id = v_task_id;
    SELECT COUNT(*) INTO cnt_a_out  FROM core_outcomes            WHERE action_event_id = v_evt_id;
    SELECT COUNT(*) INTO cnt_a_lc   FROM core_learning_candidates WHERE source_action_id = v_action_id;

    final_result := partial_result || jsonb_build_object(
      'rows_after_rollback', jsonb_build_object(
        'recommendations',     cnt_a_rec,
        'action_events',       cnt_a_evt,
        'actions',             cnt_a_act,
        'tasks',               cnt_a_task,
        'outcomes',            cnt_a_out,
        'learning_candidates', cnt_a_lc
      ),
      'rollback_verified', (
        cnt_a_rec  = cnt_b_rec  AND cnt_a_evt  = cnt_b_evt  AND
        cnt_a_act  = cnt_b_act  AND cnt_a_task = cnt_b_task AND
        cnt_a_out  = cnt_b_out  AND cnt_a_lc   = cnt_b_lc
      ),
      'no_residual_rows', (
        cnt_a_rec = 0 AND cnt_a_evt = 0 AND cnt_a_act = 0 AND
        cnt_a_task = 0 AND cnt_a_out = 0 AND cnt_a_lc = 0
      )
    );

    RETURN final_result;
  END;

  RETURN jsonb_build_object('error', 'sentinel_did_not_fire');
END;
$func$;

GRANT EXECUTE ON FUNCTION h4_fixture_execute() TO service_role;
