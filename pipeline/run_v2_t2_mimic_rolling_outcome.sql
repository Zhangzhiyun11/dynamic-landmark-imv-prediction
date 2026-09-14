-- V2 T2 MIMIC rolling cohort/outcome. No predictors are exported here.

ATTACH '../database/mimiciv-3.1.duckdb' AS raw (READ_ONLY);
ATTACH '.restricted_data/mimic_t1/t1_mimic.duckdb' AS old (READ_ONLY);
SET preserve_insertion_order=false;
SET threads=4;

CREATE OR REPLACE TEMP TABLE v2_mimic_candidate_landmarks AS
SELECT c.*,p.anchor_year_group,gs.landmark_h,
       c.icu_intime + gs.landmark_h * INTERVAL '1 hour' AS landmark_time_v2
FROM old.mimic_t1.cohort c
JOIN raw.mimiciv_hosp.patients p USING(subject_id)
CROSS JOIN generate_series(24,72,4) gs(landmark_h)
WHERE c.icu_outtime > c.icu_intime + gs.landmark_h * INTERVAL '1 hour'
  AND (c.deathtime IS NULL OR c.deathtime > c.icu_intime + gs.landmark_h * INTERVAL '1 hour');

CREATE OR REPLACE TEMP TABLE v2_mimic_landmarks AS
SELECT l.*
FROM v2_mimic_candidate_landmarks l
WHERE NOT EXISTS (
    SELECT 1 FROM old.mimic_t1.imv_broad_evidence b
    WHERE b.stay_id=l.stay_id
      AND b.evidence_time BETWEEN l.icu_intime AND l.landmark_time_v2
)
AND NOT EXISTS (
    SELECT 1 FROM old.mimic_t1.imv_strict_intervals v
    WHERE v.stay_id=l.stay_id
      AND v.starttime <= l.landmark_time_v2
      AND v.endtime >= l.icu_intime
)
AND NOT EXISTS (
    SELECT 1 FROM raw.mimiciv_derived.ventilation v
    WHERE v.stay_id=l.stay_id
      AND v.ventilation_status='Tracheostomy'
      AND v.starttime <= l.landmark_time_v2
      AND v.endtime >= l.icu_intime
)
AND EXISTS (
    SELECT 1 FROM raw.mimiciv_derived.vitalsign x
    WHERE x.stay_id=l.stay_id
      AND x.charttime BETWEEN l.landmark_time_v2 - INTERVAL '12 hours'
                          AND l.landmark_time_v2
      AND COALESCE(x.resp_rate,x.spo2,x.heart_rate,x.sbp,x.mbp,x.temperature) IS NOT NULL
);

CREATE OR REPLACE TEMP TABLE v2_mimic_windows AS
SELECT l.*,w.horizon_h,
       l.landmark_time_v2 + w.horizon_h * INTERVAL '1 hour' AS horizon_end_time
FROM v2_mimic_landmarks l
CROSS JOIN (VALUES (12),(24)) w(horizon_h)
UNION ALL
SELECT l.*,72 AS horizon_h,
       l.landmark_time_v2 + INTERVAL '72 hours' AS horizon_end_time
FROM v2_mimic_landmarks l
WHERE l.landmark_h=24;

CREATE OR REPLACE TEMP TABLE v2_mimic_rolling_outcome AS
WITH evidence AS (
  SELECT
    w.subject_id,w.hadm_id,w.stay_id,w.anchor_year_group,
    w.icu_intime,w.icu_outtime,w.deathtime,
    w.landmark_h,w.landmark_time_v2,w.horizon_h,w.horizon_end_time,
    (SELECT MIN(v.starttime) FROM old.mimic_t1.imv_strict_intervals v
      WHERE v.stay_id=w.stay_id
        AND v.starttime>w.landmark_time_v2
        AND v.starttime<=w.horizon_end_time) AS first_strict_start,
    (SELECT MIN(b.evidence_time) FROM old.mimic_t1.imv_broad_evidence b
      WHERE b.stay_id=w.stay_id
        AND b.evidence_time>w.landmark_time_v2
        AND b.evidence_time<=w.horizon_end_time) AS first_broad_evidence
  FROM v2_mimic_windows w
)
SELECT
  'MIMIC-IV v3.1' AS database,
  subject_id,hadm_id,stay_id,anchor_year_group,
  landmark_h,horizon_h,
  'MIMIC_STRICT_WITH_BROAD_UNKNOWN' AS phenotype,
  CASE WHEN first_strict_start IS NOT NULL THEN 'A'
       WHEN first_broad_evidence IS NOT NULL THEN 'Unknown'
       ELSE 'None' END AS evidence_grade,
  CASE WHEN first_strict_start IS NOT NULL THEN 1
       WHEN first_broad_evidence IS NOT NULL THEN 9
       ELSE 0 END AS outcome_imv,
  DATE_DIFF('second',icu_intime,first_strict_start)/3600.0 AS first_strict_start_offset_h,
  DATE_DIFF('second',icu_intime,first_broad_evidence)/3600.0 AS first_broad_evidence_offset_h,
  CASE
    WHEN deathtime>landmark_time_v2 AND deathtime<=horizon_end_time
     AND icu_outtime>landmark_time_v2 AND icu_outtime<=horizon_end_time
     AND deathtime=icu_outtime
     AND (first_strict_start IS NULL OR deathtime<first_strict_start)
      THEN 'both_same_time'
    WHEN deathtime>landmark_time_v2 AND deathtime<=horizon_end_time
     AND (first_strict_start IS NULL OR deathtime<first_strict_start)
      THEN 'death'
    WHEN icu_outtime>landmark_time_v2 AND icu_outtime<=horizon_end_time
     AND (first_strict_start IS NULL OR icu_outtime<first_strict_start)
      THEN 'ICU discharge'
    ELSE 'none' END AS competing_event_type,
  DATE_DIFF('second',icu_intime,icu_outtime)/3600.0 AS icu_discharge_offset_h,
  DATE_DIFF('second',icu_intime,deathtime)/3600.0 AS death_offset_h
FROM evidence;

COPY (
  SELECT * FROM v2_mimic_rolling_outcome
  ORDER BY subject_id,stay_id,landmark_h,horizon_h
) TO 'study_b_v2/restricted/V2_MIMIC_ROLLING_OUTCOME_V0_1.parquet'
  (FORMAT PARQUET,COMPRESSION ZSTD);

COPY (
  SELECT 'MIMIC-IV v3.1' AS database,subject_id,hadm_id,stay_id,
         anchor_year_group,landmark_h
  FROM v2_mimic_landmarks
  ORDER BY subject_id,stay_id,landmark_h
) TO 'study_b_v2/restricted/V2_MIMIC_ROLLING_COHORT_KEYS_V0_1.parquet'
  (FORMAT PARQUET,COMPRESSION ZSTD);

COPY (
  SELECT landmark_h,horizon_h,evidence_grade,COUNT(*) AS landmark_rows
  FROM v2_mimic_rolling_outcome
  GROUP BY landmark_h,horizon_h,evidence_grade
  ORDER BY landmark_h,horizon_h,evidence_grade
) TO 'study_b_v2/04_qc/V2_MIMIC_ROLLING_OUTCOME_COUNTS_V0_1.csv'
  (HEADER,DELIMITER ',');
