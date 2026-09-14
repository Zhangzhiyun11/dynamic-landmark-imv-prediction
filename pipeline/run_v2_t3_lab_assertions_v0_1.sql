-- V2 T3 lab feature assertions. No outcome table is read.

CREATE OR REPLACE TEMP VIEW mimic_labs AS
SELECT * FROM read_parquet('study_b_v2/restricted/V2_MIMIC_LAB_FEATURES_LONG_V0_1.parquet');
CREATE OR REPLACE TEMP VIEW eicu_labs AS
SELECT * FROM read_parquet('study_b_v2/restricted/V2_EICU_LAB_FEATURES_LONG_V0_1.parquet');
CREATE OR REPLACE TEMP VIEW all_labs AS
SELECT * FROM mimic_labs UNION ALL BY NAME SELECT * FROM eicu_labs;

CREATE OR REPLACE TEMP TABLE assertions AS
WITH bad_schema AS (
  SELECT COUNT(*) AS n FROM (
    SELECT name FROM parquet_schema('study_b_v2/restricted/V2_MIMIC_LAB_FEATURES_LONG_V0_1.parquet')
    UNION ALL SELECT name FROM parquet_schema('study_b_v2/restricted/V2_EICU_LAB_FEATURES_LONG_V0_1.parquet')
  ) s WHERE lower(name) SIMILAR TO '%(outcome|event|label|death|mortality|unknown)%'
), mimic_coverage AS (
  SELECT variable,COUNT(*)::DOUBLE/233319 AS coverage FROM mimic_labs GROUP BY variable
), eicu_coverage AS (
  SELECT variable,COUNT(*)::DOUBLE/572055 AS coverage FROM eicu_labs GROUP BY variable
)
SELECT * FROM (
  SELECT 'mimic_key_unique' AS test_name,
    COUNT(*)=COUNT(DISTINCT (stay_key,landmark_h,variable)) AS passed,COUNT(*)||' rows' AS detail FROM mimic_labs
  UNION ALL SELECT 'eicu_key_unique',COUNT(*)=COUNT(DISTINCT (stay_key,landmark_h,variable)),COUNT(*)||' rows' FROM eicu_labs
  UNION ALL SELECT 'mimic_expected_variables',COUNT(DISTINCT variable)=14,
    string_agg(DISTINCT variable,',' ORDER BY variable) FROM mimic_labs
  UNION ALL SELECT 'eicu_expected_variables',COUNT(DISTINCT variable)=14,
    string_agg(DISTINCT variable,',' ORDER BY variable) FROM eicu_labs
  UNION ALL SELECT 'latest_offset_in_lookback',COUNT(*)=0,COUNT(*)||' violations' FROM all_labs
    WHERE latest_offset_h<=landmark_h-12 OR latest_offset_h>landmark_h
  UNION ALL SELECT 'delta_slope_pairing',COUNT(*)=0,COUNT(*)||' violations' FROM all_labs
    WHERE (measurement_count<2 AND (delta_value IS NOT NULL OR slope_value IS NOT NULL))
       OR (measurement_count>=2 AND (delta_value IS NULL OR slope_value IS NULL))
  UNION ALL SELECT 'measurement_count_positive',COUNT(*)=0,COUNT(*)||' violations' FROM all_labs
    WHERE measurement_count<1
  UNION ALL SELECT 'lab_artifacts_predictor_only',n=0,n||' prohibited schema names' FROM bad_schema
  UNION ALL SELECT 'mimic_routine_lab_coverage_gate',MIN(coverage)>=0.55,
    ROUND(100*MIN(coverage),2)||'% minimum' FROM mimic_coverage
    WHERE variable NOT IN ('ph','pao2','paco2','lactate')
  UNION ALL SELECT 'eicu_routine_lab_coverage_gate',MIN(coverage)>=0.45,
    ROUND(100*MIN(coverage),2)||'% minimum' FROM eicu_coverage
    WHERE variable NOT IN ('ph','pao2','paco2','lactate')
  UNION ALL SELECT 'mimic_pao2_extension_coverage_report',MIN(coverage)>=0.03,
    ROUND(100*MIN(coverage),2)||'%' FROM mimic_coverage WHERE variable='pao2'
  UNION ALL SELECT 'eicu_pao2_extension_coverage_report',MIN(coverage)>=0.05,
    ROUND(100*MIN(coverage),2)||'%' FROM eicu_coverage WHERE variable='pao2'
) ordered;

COPY (SELECT test_name AS assertion,passed,detail FROM assertions ORDER BY test_name)
TO 'study_b_v2/04_qc/V2_T3_LAB_ASSERTIONS_V0_1.csv' (HEADER,DELIMITER ',');

COPY (
  WITH key_n AS (
    SELECT 'MIMIC-IV v3.1' AS database,233319 AS n
    UNION ALL SELECT 'eICU-CRD v2.0',572055
  )
  SELECT f.database,f.variable,COUNT(*) AS rows_with_variable,
         COUNT(*)::DOUBLE/k.n AS coverage,
         AVG((f.slope_value IS NOT NULL)::INTEGER) AS slope_coverage_among_observed
  FROM all_labs f JOIN key_n k USING(database)
  GROUP BY f.database,f.variable,k.n ORDER BY f.database,f.variable
) TO 'study_b_v2/04_qc/V2_T3_LAB_COVERAGE_V0_1.csv' (HEADER,DELIMITER ',');

SELECT CASE WHEN bool_and(passed) THEN 'PASS' ELSE error('V2 T3 lab assertions failed') END
FROM assertions;
