-- T3 fold inputs. Outcome-linked counts are used only for deterministic
-- stratified grouping; no prediction model or performance metric is computed.

COPY (
  SELECT hospital_id,
         COUNT(DISTINCT patient_key) AS patients,
         COUNT(*) AS landmarks,
         SUM(outcome_imv=1) AS event_rows,
         SUM(outcome_imv=9) AS unknown_rows,
         COUNT(DISTINCT CASE WHEN outcome_imv=1 THEN icu_stay_key END) AS event_stays
  FROM read_parquet('study_b_v2/restricted/V2_EICU_ROLLING_OUTCOME_V0_1.parquet')
  WHERE horizon_h=24
  GROUP BY hospital_id ORDER BY hospital_id
) TO 'study_b_v2/restricted/V2_EICU_HOSPITAL_FOLD_INPUT_V0_1.csv'
  (HEADER,DELIMITER ',');

COPY (
  SELECT subject_id,anchor_year_group,
         COUNT(*) AS landmarks,
         SUM(outcome_imv=1) AS event_rows,
         SUM(outcome_imv=9) AS unknown_rows,
         MAX(CAST(outcome_imv=1 AS INTEGER)) AS event_stay
  FROM read_parquet('study_b_v2/restricted/V2_MIMIC_ROLLING_OUTCOME_V0_1.parquet')
  WHERE horizon_h=24
  GROUP BY subject_id,anchor_year_group ORDER BY subject_id
) TO 'study_b_v2/restricted/V2_MIMIC_PATIENT_FOLD_INPUT_V0_1.csv'
  (HEADER,DELIMITER ',');
