COPY (
  WITH m AS (
    SELECT * FROM read_parquet('study_b_v2/restricted/V2_MIMIC_ROLLING_OUTCOME_V0_1.parquet')
  ), e AS (
    SELECT * FROM read_parquet('study_b_v2/restricted/V2_EICU_ROLLING_OUTCOME_V0_1.parquet')
  ), mc AS (
    SELECT * FROM read_csv_auto('study_b_v2/04_qc/V2_MIMIC_ROLLING_OUTCOME_COUNTS_V0_1.csv')
  ), ec AS (
    SELECT * FROM read_csv_auto('study_b_v2/04_qc/V2_EICU_ROLLING_OUTCOME_COUNTS_V0_1.csv')
  )
  SELECT * FROM (
    SELECT 'mimic_unique_landmark_horizon' AS assertion,
      (SELECT COUNT(*)=COUNT(DISTINCT (subject_id,stay_id,landmark_h,horizon_h)) FROM m) AS passed,
      'no duplicate patient-landmark-horizon rows' AS detail
    UNION ALL SELECT 'eicu_unique_landmark_horizon',
      (SELECT COUNT(*)=COUNT(DISTINCT (icu_stay_key,landmark_h,horizon_h)) FROM e),
      'no duplicate stay-landmark-horizon rows'
    UNION ALL SELECT 'mimic_horizon_structure',
      (SELECT COUNT(*)=0 FROM m WHERE horizon_h NOT IN (12,24,72)
        OR (horizon_h=72 AND landmark_h<>24)),
      '12/24h rolling plus t24-to-t96 fixed only'
    UNION ALL SELECT 'eicu_horizon_structure',
      (SELECT COUNT(*)=0 FROM e WHERE horizon_h NOT IN (12,24,72)
        OR (horizon_h=72 AND landmark_h<>24)),
      '12/24h rolling plus t24-to-t96 fixed only'
    UNION ALL SELECT 'mimic_grade_mapping',
      (SELECT COUNT(*)=0 FROM m WHERE outcome_imv NOT IN (0,1,9)
        OR (evidence_grade='A' AND outcome_imv<>1)
        OR (evidence_grade='Unknown' AND outcome_imv<>9)
        OR (evidence_grade='None' AND outcome_imv<>0)),
      'Unknown remains 9'
    UNION ALL SELECT 'eicu_grade_mapping',
      (SELECT COUNT(*)=0 FROM e WHERE outcome_imv NOT IN (0,1,9)
        OR (evidence_grade IN ('A','B','C') AND outcome_imv<>1)
        OR (evidence_grade='Unknown' AND outcome_imv<>9)
        OR (evidence_grade='None' AND outcome_imv<>0)),
      'A+B+C=1; Unknown=9; None=0'
    UNION ALL SELECT 'mimic_at_risk_time_order',
      (SELECT COUNT(*)=0 FROM m WHERE icu_discharge_offset_h<=landmark_h
        OR (death_offset_h IS NOT NULL AND death_offset_h<=landmark_h)),
      'alive and in ICU after landmark'
    UNION ALL SELECT 'eicu_at_risk_time_order',
      (SELECT COUNT(*)=0 FROM e WHERE icu_discharge_offset_h<=landmark_h
        OR (death_offset_h IS NOT NULL AND death_offset_h<=landmark_h)),
      'alive and in ICU after landmark'
    UNION ALL SELECT 'mimic_competing_values',
      (SELECT COUNT(*)=0 FROM m WHERE competing_event_type NOT IN
        ('none','death','ICU discharge','both_same_time')),
      'allowed competing-event categories only'
    UNION ALL SELECT 'eicu_competing_values',
      (SELECT COUNT(*)=0 FROM e WHERE competing_event_type NOT IN
        ('none','death','ICU discharge','both_same_time')),
      'allowed competing-event categories only'
    UNION ALL SELECT 'mimic_counts_reconcile',
      (SELECT SUM(landmark_rows)=(SELECT COUNT(*) FROM m) FROM mc),
      'public aggregate counts equal restricted rows'
    UNION ALL SELECT 'eicu_counts_reconcile',
      (SELECT SUM(landmark_rows)=(SELECT COUNT(*) FROM e) FROM ec),
      'public aggregate counts equal restricted rows'
    UNION ALL SELECT 'mimic_t24_event_bound',
      (SELECT SUM(outcome_imv=1)<=899 FROM m WHERE landmark_h=24 AND horizon_h=24),
      'new risk set cannot exceed locked Study A strict event count'
    UNION ALL SELECT 'eicu_t24_event_bound',
      (SELECT SUM(outcome_imv=1)<=668 FROM e WHERE landmark_h=24 AND horizon_h=24),
      'new risk set cannot exceed locked Study A Route C event count'
  ) q ORDER BY assertion
) TO 'study_b_v2/04_qc/V2_T2_ROLLING_OUTCOME_ASSERTIONS_V0_1.csv'
  (HEADER,DELIMITER ',');
