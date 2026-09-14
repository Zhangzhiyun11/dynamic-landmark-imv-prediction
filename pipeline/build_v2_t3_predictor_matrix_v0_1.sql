-- Build outcome-free all-model predictor matrix and frozen outer-fold labels.

CREATE OR REPLACE TEMP VIEW tier_a_long AS
SELECT * FROM read_parquet('study_b_v2/restricted/V2_MIMIC_TIER_A_FEATURES_LONG_V0_2.parquet')
UNION ALL BY NAME
SELECT * FROM read_parquet('study_b_v2/restricted/V2_EICU_TIER_A_FEATURES_LONG_V0_3.parquet');

CREATE OR REPLACE TEMP TABLE tier_a_wide AS
PIVOT tier_a_long ON variable USING
  first(latest_value) AS latest,
  first(latest_offset_h) AS latest_offset,
  first(median_value) AS median,
  first(min_value) AS min,
  first(max_value) AS max,
  first(sd_value) AS sd,
  first(iqr_value) AS iqr,
  first(theilsen_slope) AS slope,
  first(abnormal_prop) AS abnormal_prop,
  first(longest_abnormal_run) AS longest_abnormal_run
GROUP BY database,stay_key,landmark_h;

CREATE OR REPLACE TEMP VIEW lab_long AS
SELECT * FROM read_parquet('study_b_v2/restricted/V2_MIMIC_LAB_FEATURES_LONG_V0_1.parquet')
UNION ALL BY NAME
SELECT * FROM read_parquet('study_b_v2/restricted/V2_EICU_LAB_FEATURES_LONG_V0_1.parquet');

CREATE OR REPLACE TEMP TABLE lab_wide AS
PIVOT lab_long ON variable USING
  first(latest_value) AS latest,
  first(latest_offset_h) AS latest_offset,
  first(median_value) AS median,
  first(min_value) AS min,
  first(max_value) AS max,
  first(delta_value) AS delta,
  first(slope_value) AS slope
GROUP BY database,stay_key,landmark_h;

CREATE OR REPLACE TEMP VIEW tier_b AS
SELECT * FROM read_parquet('study_b_v2/restricted/V2_MIMIC_TIER_B_FEATURES_V0_1.parquet')
UNION ALL BY NAME
SELECT * FROM read_parquet('study_b_v2/restricted/V2_EICU_TIER_B_FEATURES_V0_1.parquet');

CREATE OR REPLACE TEMP TABLE fold_map AS
SELECT 'MIMIC-IV v3.1' AS database,CAST(subject_id AS VARCHAR) AS patient_key,
       NULL::INTEGER AS hospital_id,outer_fold,temporal_role
FROM read_csv_auto('study_b_v2/restricted/V2_MIMIC_PATIENT_OUTER_FOLDS_V0_1.csv',header=true)
UNION ALL
SELECT 'eICU-CRD v2.0',NULL,CAST(hospital_id AS INTEGER),outer_fold,NULL
FROM read_csv_auto('study_b_v2/restricted/V2_EICU_HOSPITAL_OUTER_FOLDS_V0_1.csv',header=true);

COPY (
  SELECT s.*,COALESCE(fm.outer_fold,fe.outer_fold) AS outer_fold,
         fm.temporal_role,
         a.* EXCLUDE(database,stay_key,landmark_h),
         CASE WHEN a.hr_latest>0 AND a.sbp_latest>0
              THEN a.hr_latest/a.sbp_latest END AS shock_index_latest,
         b.* EXCLUDE(database,stay_key,landmark_h),
         l.* EXCLUDE(database,stay_key,landmark_h),
         p.pao2_liu_24h_latest,p.pao2_liu_24h_latest_offset_h
  FROM read_parquet('study_b_v2/restricted/V2_STATIC_CONTEXT_V0_2.parquet') s
  LEFT JOIN tier_a_wide a USING(database,stay_key,landmark_h)
  LEFT JOIN tier_b b USING(database,stay_key,landmark_h)
  LEFT JOIN lab_wide l USING(database,stay_key,landmark_h)
  LEFT JOIN read_parquet('study_b_v2/restricted/V2_LIU_PAO2_24H_V0_1.parquet') p
    USING(database,stay_key,landmark_h)
  LEFT JOIN fold_map fm ON s.database=fm.database AND s.patient_key=fm.patient_key
  LEFT JOIN fold_map fe ON s.database=fe.database AND s.hospital_id=fe.hospital_id
  ORDER BY s.database,s.stay_key,s.landmark_h
) TO 'study_b_v2/restricted/V2_PREDICTOR_MATRIX_V0_1.parquet'
  (FORMAT PARQUET,COMPRESSION ZSTD);

COPY (
  SELECT column_id,name,duckdb_type,repetition_type
  FROM parquet_schema('study_b_v2/restricted/V2_PREDICTOR_MATRIX_V0_1.parquet')
  WHERE column_id>0 ORDER BY column_id
) TO 'study_b_v2/04_qc/V2_PREDICTOR_MATRIX_SCHEMA_V0_1.csv' (HEADER,DELIMITER ',');
