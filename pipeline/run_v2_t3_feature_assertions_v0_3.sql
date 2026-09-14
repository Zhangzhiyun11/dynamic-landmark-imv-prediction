-- V2 T3 predictor-only feature assertions. No outcome table is attached or read.

CREATE OR REPLACE TEMP VIEW mimic_keys AS
SELECT CAST(stay_id AS VARCHAR) AS stay_key,landmark_h
FROM read_parquet('study_b_v2/restricted/V2_MIMIC_ROLLING_COHORT_KEYS_V0_1.parquet');

CREATE OR REPLACE TEMP VIEW eicu_keys AS
SELECT CAST(icu_stay_key AS VARCHAR) AS stay_key,landmark_h
FROM read_parquet('study_b_v2/restricted/V2_EICU_ROLLING_COHORT_KEYS_V0_1.parquet');

CREATE OR REPLACE TEMP VIEW mimic_features AS
SELECT * FROM read_parquet('study_b_v2/restricted/V2_MIMIC_TIER_A_FEATURES_LONG_V0_2.parquet');

CREATE OR REPLACE TEMP VIEW eicu_features AS
SELECT * FROM read_parquet('study_b_v2/restricted/V2_EICU_TIER_A_FEATURES_LONG_V0_3.parquet');

CREATE OR REPLACE TEMP VIEW static_context AS
SELECT * FROM read_parquet('study_b_v2/restricted/V2_STATIC_CONTEXT_V0_2.parquet');

CREATE OR REPLACE TEMP TABLE assertions AS
WITH
mimic_extra AS (
  SELECT COUNT(*) AS n FROM mimic_features f
  ANTI JOIN mimic_keys k USING(stay_key,landmark_h)
), eicu_extra AS (
  SELECT COUNT(*) AS n FROM eicu_features f
  ANTI JOIN eicu_keys k USING(stay_key,landmark_h)
),
mimic_coverage AS (
  SELECT variable,COUNT(*)::DOUBLE/(SELECT COUNT(*) FROM mimic_keys) AS coverage
  FROM mimic_features GROUP BY variable
), eicu_coverage AS (
  SELECT variable,COUNT(*)::DOUBLE/(SELECT COUNT(*) FROM eicu_keys) AS coverage
  FROM eicu_features GROUP BY variable
),
feature_schema_bad AS (
  SELECT COUNT(*) AS n FROM (
    SELECT name FROM parquet_schema('study_b_v2/restricted/V2_MIMIC_TIER_A_FEATURES_LONG_V0_2.parquet')
    UNION ALL
    SELECT name FROM parquet_schema('study_b_v2/restricted/V2_EICU_TIER_A_FEATURES_LONG_V0_3.parquet')
  ) s
  WHERE lower(name) SIMILAR TO '%(outcome|event|label|death|mortality|unknown)%'
), static_schema_bad AS (
  SELECT COUNT(*) AS n
  FROM parquet_schema('study_b_v2/restricted/V2_STATIC_CONTEXT_V0_2.parquet')
  WHERE lower(name) SIMILAR TO '%(outcome|event|label|death|mortality|unknown)%'
)
SELECT * FROM (
  SELECT 'mimic_feature_key_unique' AS test_name,
    COUNT(*)=COUNT(DISTINCT (stay_key,landmark_h,variable)) passed,
    COUNT(*)||' rows' detail FROM mimic_features
  UNION ALL SELECT 'eicu_feature_key_unique',
    COUNT(*)=COUNT(DISTINCT (stay_key,landmark_h,variable)),COUNT(*)||' rows' FROM eicu_features
  UNION ALL SELECT 'mimic_expected_variables',
    COUNT(DISTINCT variable)=7 AND MIN(variable) IN ('gcs','hr','map','rr','sbp','spo2','temperature'),
    string_agg(DISTINCT variable,',' ORDER BY variable) FROM mimic_features
  UNION ALL SELECT 'eicu_expected_variables',
    COUNT(DISTINCT variable)=7 AND MIN(variable) IN ('gcs','hr','map','rr','sbp','spo2','temperature'),
    string_agg(DISTINCT variable,',' ORDER BY variable) FROM eicu_features
  UNION ALL SELECT 'mimic_no_extra_keys',n=0,n||' unmatched rows' FROM mimic_extra
  UNION ALL SELECT 'eicu_no_extra_keys',n=0,n||' unmatched rows' FROM eicu_extra
  UNION ALL SELECT 'latest_offset_in_lookback',COUNT(*)=0,COUNT(*)||' violations'
    FROM (SELECT * FROM mimic_features UNION ALL SELECT * FROM eicu_features)
    WHERE NOT (latest_offset_h>landmark_h-12 AND latest_offset_h<=landmark_h)
  UNION ALL SELECT 'bin_count_valid',COUNT(*)=0,COUNT(*)||' violations'
    FROM (SELECT * FROM mimic_features UNION ALL SELECT * FROM eicu_features)
    WHERE nonempty_bin_count NOT BETWEEN 1 AND 6
  UNION ALL SELECT 'measurement_count_valid',COUNT(*)=0,COUNT(*)||' violations'
    FROM (SELECT * FROM mimic_features UNION ALL SELECT * FROM eicu_features)
    WHERE measurement_count<nonempty_bin_count
  UNION ALL SELECT 'slope_minimum_bins',COUNT(*)=0,COUNT(*)||' violations'
    FROM (SELECT * FROM mimic_features UNION ALL SELECT * FROM eicu_features)
    WHERE (nonempty_bin_count<4 AND theilsen_slope IS NOT NULL)
       OR (nonempty_bin_count>=4 AND theilsen_slope IS NULL)
  UNION ALL SELECT 'abnormal_summary_bounds',COUNT(*)=0,COUNT(*)||' violations'
    FROM (SELECT * FROM mimic_features UNION ALL SELECT * FROM eicu_features)
    WHERE abnormal_prop NOT BETWEEN 0 AND 1
       OR longest_abnormal_run NOT BETWEEN 0 AND nonempty_bin_count
  UNION ALL SELECT 'feature_artifacts_predictor_only',n=0,n||' prohibited schema names' FROM feature_schema_bad
  UNION ALL SELECT 'static_context_predictor_only',n=0,n||' prohibited schema names' FROM static_schema_bad
  UNION ALL SELECT 'mimic_core_coverage',
    MIN(coverage)>=0.95,ROUND(100*MIN(coverage),2)||'% minimum' FROM mimic_coverage
  UNION ALL SELECT 'eicu_hr_spo2_bp_coverage',
    MIN(coverage)>=0.95,ROUND(100*MIN(coverage),2)||'% minimum'
    FROM eicu_coverage WHERE variable IN ('hr','spo2','sbp','map')
  UNION ALL SELECT 'eicu_rr_coverage',
    MIN(coverage)>=0.85,ROUND(100*MIN(coverage),2)||'% minimum'
    FROM eicu_coverage WHERE variable='rr'
  UNION ALL SELECT 'eicu_temperature_coverage',
    MIN(coverage)>=0.85,ROUND(100*MIN(coverage),2)||'% minimum'
    FROM eicu_coverage WHERE variable='temperature'
  UNION ALL SELECT 'eicu_gcs_coverage',
    MIN(coverage)>=0.60,ROUND(100*MIN(coverage),2)||'% minimum'
    FROM eicu_coverage WHERE variable='gcs'
  UNION ALL SELECT 'static_context_key_unique',
    COUNT(*)=COUNT(DISTINCT (database,stay_key,landmark_h)),COUNT(*)||' rows' FROM static_context
  UNION ALL SELECT 'static_context_total_rows',
    COUNT(*)=805374,COUNT(*)||' rows' FROM static_context
) ordered;

COPY (
  SELECT test_name AS assertion,passed,detail FROM assertions ORDER BY test_name
) TO 'study_b_v2/04_qc/V2_T3_FEATURE_ASSERTIONS_V0_3.csv'
  (HEADER,DELIMITER ',');

SELECT CASE WHEN bool_and(passed) THEN 'PASS' ELSE error('V2 T3 feature assertions failed') END
FROM assertions;
