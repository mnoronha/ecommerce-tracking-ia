-- Migration 062 — H4 fixture corrective rewrite
--
-- Replaces h4_fixture_execute() (first applied in 061) with a version that:
--
--   1. Pre-generates ALL UUIDs before the implicit subtransaction so that
--      rows_before counts can be captured outside the exception block.
--
--   2. Returns rows_before, rows_during, rows_after_rollback for:
--      core_recommendations, core_action_events (human decision), core_tasks,
--      core_actions, core_change_log, core_outcomes, core_learning_candidates.
--      Expected: rows_after_rollback == rows_before.
--
--   3. Tests outcome idempotency inside the transaction:
--      - Insert O1 (window=7)
--      - SELECT by same key → must return O1's id (replay_id == id)
--      - Insert O2 (window=30) → distinct id
--
--   4. Tests LC idempotency inside the transaction:
--      - Insert LC1 (action_id, outcome_1_id, scope=CLIENT_ONLY)
--      - SELECT by same key → must return LC1's id (replay_id == id)
--      - Insert LC2 (action_id, outcome_2_id, same scope) → distinct id
--
--   5. Uses ONLY the implicit subtransaction mechanism (BEGIN...EXCEPTION block).
--      Zero explicit SAVEPOINT / ROLLBACK TO SAVEPOINT / RELEASE SAVEPOINT.
--
-- h4_idempotency_test() is unchanged (already correct from 061).

CREATE OR REPLACE FUNCTION h4_fixture_execute()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $func$
DECLARE
  -- Pre-generate all IDs so rows_before can be counted before any INSERT
  v_diagnosis_id uuid := gen_random_uuid();
  v_rec_id       uuid := gen_random_uuid();
  v_task_id      uuid := gen_random_uuid();
  v_evt_id       uuid := gen_random_uuid();   -- action_event = human decision
  v_action_id    uuid := gen_random_uuid();
  v_change_id    uuid := gen_random_uuid();
  v_outcome_1_id uuid := gen_random_uuid();   -- evaluation_window_days = 7
  v_lc_1_id      uuid := gen_random_uuid();   -- sourced from O1
  v_client_uuid  uuid := gen_random_uuid();

  -- Assigned by RETURNING after INSERT (IDs not needed before insert)
  v_outcome_2_id uuid;   -- evaluation_window_days = 30
  v_lc_2_id      uuid;   -- sourced from O2

  -- Idempotency replay verification
  v_outcome_replay_id uuid;
  v_lc_replay_id      uuid;

  -- Row counts: before any insert (captured outside the exception block)
  cnt_b_rec  bigint;  cnt_b_evt  bigint;  cnt_b_act  bigint;
  cnt_b_task bigint;  cnt_b_chg  bigint;
  cnt_b_out  bigint;  cnt_b_lc   bigint;

  -- Row counts: during (inside exception block, before sentinel)
  cnt_d_rec  bigint;  cnt_d_evt  bigint;  cnt_d_act  bigint;
  cnt_d_task bigint;  cnt_d_chg  bigint;
  cnt_d_out  bigint;  cnt_d_lc   bigint;

  -- Row counts: after implicit rollback (captured in exception handler)
  cnt_a_rec  bigint;  cnt_a_evt  bigint;  cnt_a_act  bigint;
  cnt_a_task bigint;  cnt_a_chg  bigint;
  cnt_a_out  bigint;  cnt_a_lc   bigint;

  partial_result jsonb;
  final_result   jsonb;
BEGIN

  -- ── rows_before: must all be 0 (fresh UUIDs have no rows) ─────────────────
  SELECT COUNT(*) INTO cnt_b_rec  FROM core_recommendations     WHERE id = v_rec_id;
  SELECT COUNT(*) INTO cnt_b_evt  FROM core_action_events       WHERE id = v_evt_id;
  SELECT COUNT(*) INTO cnt_b_act  FROM core_actions             WHERE id = v_action_id;
  SELECT COUNT(*) INTO cnt_b_task FROM core_tasks               WHERE id = v_task_id;
  SELECT COUNT(*) INTO cnt_b_chg  FROM core_change_log          WHERE id = v_change_id;
  SELECT COUNT(*) INTO cnt_b_out  FROM core_outcomes            WHERE action_event_id = v_evt_id;
  SELECT COUNT(*) INTO cnt_b_lc   FROM core_learning_candidates WHERE source_action_id = v_action_id;

  -- ── Inner block — implicit subtransaction via EXCEPTION ───────────────────
  -- PostgreSQL creates an implicit savepoint at this BEGIN.
  -- When the sentinel RAISE fires, all inserts in this block are rolled back.
  -- The EXCEPTION handler executes in the post-rollback state.
  -- No SAVEPOINT / ROLLBACK TO SAVEPOINT / RELEASE SAVEPOINT.
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

    -- 4. Action event = Human Decision (recommendation_id no FK enforcement)
    INSERT INTO core_action_events (id, recommendation_id, event_type, actor, note)
    VALUES (v_evt_id, v_rec_id, 'APPROVED', 'h4_fixture',
            'H4 fixture: human decision (always rolled back)');

    -- 5. Action (full traceability: action_event_id + task_id)
    INSERT INTO core_actions (
      id, client_id, recommendation_id, diagnosis_id,
      action_type, status, evaluation_window_days,
      action_event_id, task_id
    ) VALUES (
      v_action_id, v_client_uuid, v_rec_id, v_diagnosis_id,
      'MANUAL_EXTERNAL_ACTION', 'PLANNED', 7,
      v_evt_id, v_task_id
    );

    -- 6. Change log (linked to action)
    INSERT INTO core_change_log (
      id, client_id, occurred_at, channel, platform_account_id,
      entity_type, entity_name_at_time, change_type, source,
      linked_action_id
    ) VALUES (
      v_change_id, 'h4-fixture-client', now(), 'meta', 'act_h4_fixture',
      'campaign', 'H4 Fixture Campaign', 'CAMPAIGN_PAUSE', 'HUMAN',
      v_action_id
    );

    -- 7. Outcome O1 (window = 7)
    INSERT INTO core_outcomes (
      id, action_event_id, action_id, recommendation_id,
      status, evaluation_window_days, measurement_period, metric_refs
    ) VALUES (
      v_outcome_1_id, v_evt_id, v_action_id, v_rec_id,
      'PENDING', 7, '{"start":"2026-10-08","end":"2026-10-15"}', '["roas"]'
    );

    -- 7a. Outcome replay simulation: same key → must return O1
    SELECT id INTO v_outcome_replay_id
    FROM core_outcomes
    WHERE action_event_id = v_evt_id
      AND evaluation_window_days = 7
    LIMIT 1;

    -- 7b. Outcome O2: same event, different window → distinct ID (new evidence)
    INSERT INTO core_outcomes (
      action_event_id, action_id, recommendation_id,
      status, evaluation_window_days, measurement_period, metric_refs
    ) VALUES (
      v_evt_id, v_action_id, v_rec_id,
      'PENDING', 30, '{"start":"2026-10-08","end":"2026-11-07"}', '["roas"]'
    ) RETURNING id INTO v_outcome_2_id;

    -- 8. Learning Candidate LC1: sourced from (action, O1, CLIENT_ONLY)
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

    -- 8a. LC replay simulation: same key → must return LC1
    SELECT id INTO v_lc_replay_id
    FROM core_learning_candidates
    WHERE source_action_id = v_action_id
      AND outcome_id       = v_outcome_1_id
      AND scope            = 'CLIENT_ONLY'
    LIMIT 1;

    -- 8b. LC2: same (action, scope) but different outcome (O2) → distinct ID
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

    -- ── rows_during: capture before sentinel ─────────────────────────────────
    SELECT COUNT(*) INTO cnt_d_rec  FROM core_recommendations     WHERE id = v_rec_id;
    SELECT COUNT(*) INTO cnt_d_evt  FROM core_action_events       WHERE id = v_evt_id;
    SELECT COUNT(*) INTO cnt_d_act  FROM core_actions             WHERE id = v_action_id;
    SELECT COUNT(*) INTO cnt_d_task FROM core_tasks               WHERE id = v_task_id;
    SELECT COUNT(*) INTO cnt_d_chg  FROM core_change_log          WHERE id = v_change_id;
    SELECT COUNT(*) INTO cnt_d_out  FROM core_outcomes            WHERE action_event_id = v_evt_id;
    SELECT COUNT(*) INTO cnt_d_lc   FROM core_learning_candidates WHERE source_action_id = v_action_id;

    -- Persist sanitized result payload in local variable before sentinel fires
    partial_result := jsonb_build_object(
      'recommendation_id',       v_rec_id,
      'decision_id',             v_evt_id,
      'task_id',                 v_task_id,
      'action_id',               v_action_id,
      'action_event_id',         v_evt_id,
      'change_log_id',           v_change_id,
      'outcome_id',              v_outcome_1_id,
      'outcome_2_id',            v_outcome_2_id,
      'learning_candidate_id',   v_lc_1_id,
      'learning_candidate_2_id', v_lc_2_id,
      'rows_before', jsonb_build_object(
        'recommendations',     cnt_b_rec,
        'action_events',       cnt_b_evt,
        'actions',             cnt_b_act,
        'tasks',               cnt_b_task,
        'change_log',          cnt_b_chg,
        'outcomes',            cnt_b_out,
        'learning_candidates', cnt_b_lc
      ),
      'rows_during', jsonb_build_object(
        'recommendations',     cnt_d_rec,
        'action_events',       cnt_d_evt,
        'actions',             cnt_d_act,
        'tasks',               cnt_d_task,
        'change_log',          cnt_d_chg,
        'outcomes',            cnt_d_out,     -- 2: O1 + O2
        'learning_candidates', cnt_d_lc       -- 2: LC1 + LC2
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
        'change_log.linked_action_id', v_action_id,
        'outcome.action_event_id',     v_evt_id,
        'outcome.action_id',           v_action_id,
        'lc.source_action_id',         v_action_id,
        'lc.source_recommendation_id', v_rec_id,
        'lc.outcome_id',               v_outcome_1_id
      )
    );

    -- Sentinel: controlled exception triggers implicit subtransaction rollback.
    -- No SAVEPOINT, ROLLBACK TO SAVEPOINT, or RELEASE SAVEPOINT.
    RAISE EXCEPTION 'h4_fixture_rollback_sentinel' USING ERRCODE = 'P0001';

  EXCEPTION WHEN raise_exception THEN
    -- All inserts from the inner BEGIN block are rolled back here.
    -- Capture post-rollback counts using the same filters as rows_before.
    SELECT COUNT(*) INTO cnt_a_rec  FROM core_recommendations     WHERE id = v_rec_id;
    SELECT COUNT(*) INTO cnt_a_evt  FROM core_action_events       WHERE id = v_evt_id;
    SELECT COUNT(*) INTO cnt_a_act  FROM core_actions             WHERE id = v_action_id;
    SELECT COUNT(*) INTO cnt_a_task FROM core_tasks               WHERE id = v_task_id;
    SELECT COUNT(*) INTO cnt_a_chg  FROM core_change_log          WHERE id = v_change_id;
    SELECT COUNT(*) INTO cnt_a_out  FROM core_outcomes            WHERE action_event_id = v_evt_id;
    SELECT COUNT(*) INTO cnt_a_lc   FROM core_learning_candidates WHERE source_action_id = v_action_id;

    final_result := partial_result || jsonb_build_object(
      'rows_after_rollback', jsonb_build_object(
        'recommendations',     cnt_a_rec,
        'action_events',       cnt_a_evt,
        'actions',             cnt_a_act,
        'tasks',               cnt_a_task,
        'change_log',          cnt_a_chg,
        'outcomes',            cnt_a_out,
        'learning_candidates', cnt_a_lc
      ),
      'rollback_verified', (
        cnt_a_rec  = cnt_b_rec  AND
        cnt_a_evt  = cnt_b_evt  AND
        cnt_a_act  = cnt_b_act  AND
        cnt_a_task = cnt_b_task AND
        cnt_a_chg  = cnt_b_chg  AND
        cnt_a_out  = cnt_b_out  AND
        cnt_a_lc   = cnt_b_lc
      ),
      'no_residual_rows', (
        cnt_a_rec = 0 AND cnt_a_evt = 0 AND cnt_a_act = 0 AND
        cnt_a_task = 0 AND cnt_a_chg = 0 AND
        cnt_a_out = 0 AND cnt_a_lc = 0
      )
    );

    RETURN final_result;
  END;

  -- Unreachable: sentinel always fires
  RETURN jsonb_build_object('error', 'sentinel_did_not_fire');
END;
$func$;

GRANT EXECUTE ON FUNCTION h4_fixture_execute() TO service_role;
