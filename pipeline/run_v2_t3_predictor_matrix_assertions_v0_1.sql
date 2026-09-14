CREATE OR REPLACE TEMP VIEW matrix AS
SELECT * FROM read_parquet('study_b_v2/restricted/V2_PREDICTOR_MATRIX_V0_1.parquet');

CREATE OR REPLACE TEMP TABLE assertions AS
WITH bad_schema AS (
  SELECT COUNT(*) AS n
  FROM parquet_schema('study_b_v2/restricted/V2_PREDICTOR_MATRIX_V0_1.parquet')
  WHERE column_id>0 AND lower(name) SIMILAR TO '%(outcome|event|label|death|mortality|unknown)%'
)
SELECT * FROM (
  SELECT 'total_rows' AS test_name,COUNT(*)=805374 AS passed,COUNT(*)||' rows' AS detail FROM matrix
  UNION ALL SELECT 'key_unique',COUNT(*)=COUNT(DISTINCT (database,stay_key,landmark_h)),COUNT(*)||' rows' FROM matrix
  UNION ALL SELECT 'mimic_rows',COUNT(*)=233319,COUNT(*)||' rows' FROM matrix WHERE database='MIMIC-IV v3.1'
  UNION ALL SELECT 'eicu_rows',COUNT(*)=572055,COUNT(*)||' rows' FROM matrix WHERE database='eICU-CRD v2.0'
  UNION ALL SELECT 'outer_fold_complete',COUNT(*)=0,COUNT(*)||' missing' FROM matrix WHERE outer_fold IS NULL
  UNION ALL SELECT 'mimic_fold_range',MIN(outer_fold)=1 AND MAX(outer_fold)=5,
    MIN(outer_fold)||'-'||MAX(outer_fold) FROM matrix WHERE database='MIMIC-IV v3.1'
  UNION ALL SELECT 'eicu_fold_range',MIN(outer_fold)=1 AND MAX(outer_fold)=10,
    MIN(outer_fold)||'-'||MAX(outer_fold) FROM matrix WHERE database='eICU-CRD v2.0'
  UNION ALL SELECT 'predictor_only_schema',n=0,n||' prohibited names' FROM bad_schema
  UNION ALL SELECT 'column_count_locked',COUNT(*)=204,COUNT(*)||' columns'
    FROM parquet_schema('study_b_v2/restricted/V2_PREDICTOR_MATRIX_V0_1.parquet') WHERE column_id>0
  UNION ALL SELECT 'core_snapshot_all_missing_bounded',COUNT(*)<=100,COUNT(*)||' rows' FROM matrix
    WHERE rr_latest IS NULL AND spo2_latest IS NULL AND hr_latest IS NULL
       AND sbp_latest IS NULL AND map_latest IS NULL AND temperature_latest IS NULL AND gcs_latest IS NULL
  UNION ALL SELECT 'liu_pao2_time_bounds',COUNT(*)=0,COUNT(*)||' violations' FROM matrix
    WHERE pao2_liu_24h_latest IS NOT NULL AND
      (pao2_liu_24h_latest_offset_h<=landmark_h-24 OR pao2_liu_24h_latest_offset_h>landmark_h)
) ordered;

COPY (SELECT test_name AS assertion,passed,detail FROM assertions ORDER BY test_name)
TO 'study_b_v2/04_qc/V2_T3_PREDICTOR_MATRIX_ASSERTIONS_V0_1.csv' (HEADER,DELIMITER ',');

SELECT CASE WHEN bool_and(passed) THEN 'PASS' ELSE error('V2 predictor matrix assertions failed') END
FROM assertions;
