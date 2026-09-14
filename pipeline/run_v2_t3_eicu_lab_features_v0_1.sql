-- Predictor-only eICU routine-lab and arterial-BG features.

ATTACH '../database/eicu-crd-2.0.duckdb' AS raw (READ_ONLY);
SET preserve_insertion_order=false;
SET threads=4;

CREATE OR REPLACE TEMP TABLE v2_keys AS
SELECT database,icu_stay_key,CAST(icu_stay_key AS VARCHAR) AS stay_key,landmark_h
FROM read_parquet('study_b_v2/restricted/V2_EICU_ROLLING_COHORT_KEYS_V0_1.parquet');
CREATE OR REPLACE TEMP TABLE v2_stays AS SELECT DISTINCT icu_stay_key FROM v2_keys;

CREATE OR REPLACE TEMP TABLE v2_observations AS
WITH mapped AS (
  SELECT l.patientunitstayid AS stay_id,l.labresultoffset/60.0 AS offset_h,
    CASE lower(trim(l.labname))
      WHEN 'wbc x 1000' THEN 'wbc' WHEN 'hgb' THEN 'hemoglobin'
      WHEN 'platelets x 1000' THEN 'platelet' WHEN 'sodium' THEN 'sodium'
      WHEN 'potassium' THEN 'potassium' WHEN 'chloride' THEN 'chloride'
      WHEN 'bicarbonate' THEN 'bicarbonate' WHEN 'bun' THEN 'bun'
      WHEN 'creatinine' THEN 'creatinine' WHEN 'glucose' THEN 'glucose'
      WHEN 'ph' THEN 'ph' WHEN 'pao2' THEN 'pao2'
      WHEN 'paco2' THEN 'paco2' WHEN 'lactate' THEN 'lactate'
    END AS variable,CAST(l.labresult AS DOUBLE) AS value
  FROM v2_stays s JOIN raw.main.lab l ON l.patientunitstayid=s.icu_stay_key
  WHERE l.labresultoffset>720 AND l.labresultoffset<=4320
    AND lower(trim(l.labname)) IN
      ('wbc x 1000','hgb','platelets x 1000','sodium','potassium','chloride',
       'bicarbonate','bun','creatinine','glucose','ph','pao2','paco2','lactate')
)
SELECT * FROM mapped
WHERE value IS NOT NULL AND CASE variable
  WHEN 'wbc' THEN value BETWEEN 0.1 AND 200
  WHEN 'hemoglobin' THEN value BETWEEN 2 AND 25
  WHEN 'platelet' THEN value BETWEEN 1 AND 2000
  WHEN 'sodium' THEN value BETWEEN 80 AND 200
  WHEN 'potassium' THEN value BETWEEN 1 AND 10
  WHEN 'chloride' THEN value BETWEEN 50 AND 160
  WHEN 'bicarbonate' THEN value BETWEEN 2 AND 60
  WHEN 'bun' THEN value BETWEEN 1 AND 300
  WHEN 'creatinine' THEN value BETWEEN 0.1 AND 30
  WHEN 'glucose' THEN value BETWEEN 20 AND 2000
  WHEN 'ph' THEN value BETWEEN 6.5 AND 8
  WHEN 'pao2' THEN value BETWEEN 20 AND 800
  WHEN 'paco2' THEN value BETWEEN 5 AND 200
  WHEN 'lactate' THEN value BETWEEN 0.1 AND 30 ELSE FALSE END;

CREATE OR REPLACE TEMP TABLE v2_lab_raw AS
SELECT k.database,k.stay_key,k.landmark_h,o.variable,o.offset_h,o.value
FROM v2_observations o
CROSS JOIN LATERAL generate_series(
  CAST(GREATEST(24,CEIL(o.offset_h/4.0)*4) AS BIGINT),
  CAST(LEAST(72,CEIL((o.offset_h+12)/4.0)*4-4) AS BIGINT),4
) gs(landmark_h)
JOIN v2_keys k ON k.icu_stay_key=o.stay_id AND k.landmark_h=gs.landmark_h
WHERE o.offset_h>gs.landmark_h-12 AND o.offset_h<=gs.landmark_h;

.read study_b_v2/03_pipeline/v2_lab_feature_core_v0_1.sql

COPY (SELECT * FROM v2_lab_features_long ORDER BY stay_key,landmark_h,variable)
TO 'study_b_v2/restricted/V2_EICU_LAB_FEATURES_LONG_V0_1.parquet'
(FORMAT PARQUET,COMPRESSION ZSTD);
