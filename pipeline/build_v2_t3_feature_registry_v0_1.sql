-- Resolve exact feature memberships from the locked predictor-matrix schema.

CREATE OR REPLACE TEMP TABLE schema_cols AS
SELECT name,duckdb_type,column_id
FROM parquet_schema('study_b_v2/restricted/V2_PREDICTOR_MATRIX_V0_1.parquet')
WHERE column_id>0;

CREATE OR REPLACE TEMP TABLE registry AS
WITH tagged AS (
  SELECT *,
    name IN ('sex','admission_source','icu_type') AS is_categorical,
    name IN ('landmark_h','age','preicu_los_hours','bmi','shock_index_latest',
             'oxygen_latest_level','oxygen_time_since_latest_h','urine_6h_ml','vaso_current')
      OR (name LIKE '%_latest' AND name NOT LIKE '%_offset'
          AND name NOT IN ('pao2_latest','paco2_latest','ph_latest','lactate_latest',
                           'pao2_liu_24h_latest')) AS m1_continuous,
    regexp_matches(name,'^(gcs|hr|map|rr|sbp|spo2|temperature)_(median|min|max|sd|iqr|slope|abnormal_prop|longest_abnormal_run)$')
      OR name IN ('oxygen_max_level','oxygen_escalation_count','urine_12h_ml',
                  'vaso_any_12h','vaso_max_agent_count')
      OR regexp_matches(name,'^(bicarbonate|bun|chloride|creatinine|glucose|hemoglobin|platelet|potassium|sodium|wbc)_(median|min|max|delta|slope)$')
      AS m2_increment,
    regexp_matches(name,'^(ph|pao2|paco2|lactate)_(latest|median|min|max|delta|slope)$') AS abg_feature
  FROM schema_cols
)
SELECT column_id,name AS feature,duckdb_type,
       CASE WHEN is_categorical THEN 'categorical' ELSE 'continuous' END AS feature_type,
       CAST(name IN ('gcs_latest','bmi','pao2_liu_24h_latest','rr_latest','preicu_los_hours') AS INTEGER) AS m0_liu_adapted,
       CAST(is_categorical OR m1_continuous AS INTEGER) AS m1_snapshot,
       CAST(is_categorical OR m1_continuous OR m2_increment AS INTEGER) AS m2_dynamic,
       CAST(is_categorical OR m1_continuous OR m2_increment AS INTEGER) AS m3_dynamic_en,
       CAST(is_categorical OR m1_continuous OR m2_increment OR abg_feature AS INTEGER) AS m2_abg_extension
FROM tagged ORDER BY column_id;

COPY (SELECT * FROM registry)
TO 'study_b_v2/01_specs/V2_EXACT_FEATURE_REGISTRY_V0_1.csv' (HEADER,DELIMITER ',');

COPY (
  SELECT 'M0_LIU_ADAPTED' AS model_id,SUM(m0_liu_adapted) AS n_features FROM registry
  UNION ALL SELECT 'M1_SNAPSHOT_CAT',SUM(m1_snapshot) FROM registry
  UNION ALL SELECT 'M2_DYNAMIC_CAT',SUM(m2_dynamic) FROM registry
  UNION ALL SELECT 'M3_DYNAMIC_EN',SUM(m3_dynamic_en) FROM registry
  UNION ALL SELECT 'M2_ABG_EXTENSION',SUM(m2_abg_extension) FROM registry
) TO 'study_b_v2/04_qc/V2_EXACT_FEATURE_COUNTS_V0_1.csv' (HEADER,DELIMITER ',');
