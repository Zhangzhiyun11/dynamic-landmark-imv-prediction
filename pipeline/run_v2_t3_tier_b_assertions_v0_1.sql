-- V2 T3 Tier B predictor-only assertions. No outcome table is read.

CREATE OR REPLACE TEMP VIEW mimic_b AS
SELECT * FROM read_parquet('study_b_v2/restricted/V2_MIMIC_TIER_B_FEATURES_V0_1.parquet');
CREATE OR REPLACE TEMP VIEW eicu_b AS
SELECT * FROM read_parquet('study_b_v2/restricted/V2_EICU_TIER_B_FEATURES_V0_1.parquet');
CREATE OR REPLACE TEMP VIEW all_b AS
SELECT * FROM mimic_b UNION ALL BY NAME SELECT * FROM eicu_b;

CREATE OR REPLACE TEMP TABLE assertions AS
WITH bad_schema AS (
  SELECT COUNT(*) AS n FROM (
    SELECT name FROM parquet_schema('study_b_v2/restricted/V2_MIMIC_TIER_B_FEATURES_V0_1.parquet')
    UNION ALL
    SELECT name FROM parquet_schema('study_b_v2/restricted/V2_EICU_TIER_B_FEATURES_V0_1.parquet')
  ) s WHERE lower(name) SIMILAR TO '%(outcome|event|label|death|mortality|unknown)%'
)
SELECT * FROM (
  SELECT 'mimic_row_count' AS test_name,COUNT(*)=233319 AS passed,COUNT(*)||' rows' AS detail FROM mimic_b
  UNION ALL SELECT 'eicu_row_count',COUNT(*)=572055,COUNT(*)||' rows' FROM eicu_b
  UNION ALL SELECT 'mimic_key_unique',COUNT(*)=COUNT(DISTINCT (stay_key,landmark_h)),COUNT(*)||' rows' FROM mimic_b
  UNION ALL SELECT 'eicu_key_unique',COUNT(*)=COUNT(DISTINCT (stay_key,landmark_h)),COUNT(*)||' rows' FROM eicu_b
  UNION ALL SELECT 'oxygen_level_range',COUNT(*)=0,COUNT(*)||' violations' FROM all_b
    WHERE oxygen_latest_level NOT BETWEEN 0 AND 4 OR oxygen_max_level NOT BETWEEN 0 AND 4
  UNION ALL SELECT 'oxygen_level_order',COUNT(*)=0,COUNT(*)||' violations' FROM all_b
    WHERE oxygen_max_level<oxygen_latest_level
  UNION ALL SELECT 'oxygen_escalation_bounds',COUNT(*)=0,COUNT(*)||' violations' FROM all_b
    WHERE oxygen_escalation_count<0
       OR oxygen_escalation_count>GREATEST(oxygen_documentation_count-1,0)
  UNION ALL SELECT 'oxygen_time_bounds',COUNT(*)=0,COUNT(*)||' violations' FROM all_b
    WHERE oxygen_latest_offset_h IS NOT NULL AND
      (oxygen_latest_offset_h<=landmark_h-12 OR oxygen_latest_offset_h>landmark_h
       OR oxygen_time_since_latest_h<0 OR oxygen_time_since_latest_h>=12)
  UNION ALL SELECT 'urine_nonnegative',COUNT(*)=0,COUNT(*)||' violations' FROM all_b
    WHERE urine_6h_ml<0 OR urine_12h_ml<0 OR urine_6h_count<0 OR urine_12h_count<0
  UNION ALL SELECT 'urine_nested_window',COUNT(*)=0,COUNT(*)||' violations' FROM all_b
    WHERE urine_6h_ml>urine_12h_ml OR urine_6h_count>urine_12h_count
  UNION ALL SELECT 'urine_time_bounds',COUNT(*)=0,COUNT(*)||' violations' FROM all_b
    WHERE urine_latest_offset_h IS NOT NULL AND
      (urine_latest_offset_h<=landmark_h-12 OR urine_latest_offset_h>landmark_h)
  UNION ALL SELECT 'vaso_binary_and_agent_bounds',COUNT(*)=0,COUNT(*)||' violations' FROM all_b
    WHERE vaso_any_12h NOT IN (0,1) OR vaso_current NOT IN (0,1)
       OR vaso_current>vaso_any_12h OR vaso_max_agent_count NOT BETWEEN 0 AND 5
  UNION ALL SELECT 'interface_flag_binary',COUNT(*)=0,COUNT(*)||' violations' FROM all_b
    WHERE oxygen_interface_supported NOT IN (0,1)
       OR urine_interface_supported NOT IN (0,1)
       OR vaso_interface_supported NOT IN (0,1)
  UNION ALL SELECT 'eicu_unsupported_vaso_is_unknown',COUNT(*)=0,COUNT(*)||' violations' FROM eicu_b
    WHERE vaso_interface_supported=0 AND
      (vaso_any_12h IS NOT NULL OR vaso_current IS NOT NULL OR vaso_max_agent_count IS NOT NULL)
  UNION ALL SELECT 'eicu_supported_vaso_is_known',COUNT(*)=0,COUNT(*)||' violations' FROM eicu_b
    WHERE vaso_interface_supported=1 AND
      (vaso_any_12h IS NULL OR vaso_current IS NULL OR vaso_max_agent_count IS NULL)
  UNION ALL SELECT 'tier_b_predictor_only',n=0,n||' prohibited schema names' FROM bad_schema
  UNION ALL SELECT 'mimic_oxygen_documentation_gate',AVG((oxygen_latest_level IS NOT NULL)::INT)>=0.45,
    ROUND(100*AVG((oxygen_latest_level IS NOT NULL)::INT),2)||'%' FROM mimic_b
  UNION ALL SELECT 'eicu_supported_oxygen_documentation_gate',
    AVG((oxygen_latest_level IS NOT NULL)::INT)>=0.20,
    ROUND(100*AVG((oxygen_latest_level IS NOT NULL)::INT),2)||'%'
    FROM eicu_b WHERE oxygen_interface_supported=1
  UNION ALL SELECT 'mimic_urine_documentation_gate',AVG((urine_12h_ml IS NOT NULL)::INT)>=0.85,
    ROUND(100*AVG((urine_12h_ml IS NOT NULL)::INT),2)||'%' FROM mimic_b
  UNION ALL SELECT 'eicu_urine_documentation_gate',AVG((urine_12h_ml IS NOT NULL)::INT)>=0.55,
    ROUND(100*AVG((urine_12h_ml IS NOT NULL)::INT),2)||'%' FROM eicu_b
) ordered;

COPY (
  SELECT test_name AS assertion,passed,detail FROM assertions ORDER BY test_name
) TO 'study_b_v2/04_qc/V2_T3_TIER_B_ASSERTIONS_V0_1.csv' (HEADER,DELIMITER ',');

COPY (
  SELECT database,COUNT(*) AS landmark_rows,
    AVG((oxygen_latest_level IS NOT NULL)::INTEGER) AS oxygen_level_coverage,
    AVG((urine_12h_ml IS NOT NULL)::INTEGER) AS urine_12h_coverage,
    AVG((vaso_any_12h IS NOT NULL)::INTEGER) AS vaso_known_coverage,
    AVG(oxygen_interface_supported) AS oxygen_interface_coverage,
    AVG(urine_interface_supported) AS urine_interface_coverage,
    AVG(vaso_interface_supported) AS vaso_interface_coverage
  FROM all_b GROUP BY database ORDER BY database
) TO 'study_b_v2/04_qc/V2_T3_TIER_B_COVERAGE_V0_1.csv' (HEADER,DELIMITER ',');

SELECT CASE WHEN bool_and(passed) THEN 'PASS' ELSE error('V2 T3 Tier B assertions failed') END
FROM assertions;
