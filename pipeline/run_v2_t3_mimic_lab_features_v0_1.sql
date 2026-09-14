-- Predictor-only MIMIC routine-lab and arterial-BG features.

ATTACH '../database/mimiciv-3.1.duckdb' AS raw (READ_ONLY);
SET preserve_insertion_order=false;
SET threads=4;

CREATE OR REPLACE TEMP TABLE v2_keys AS
SELECT database,stay_id,CAST(stay_id AS VARCHAR) AS stay_key,landmark_h
FROM read_parquet('study_b_v2/restricted/V2_MIMIC_ROLLING_COHORT_KEYS_V0_1.parquet');
CREATE OR REPLACE TEMP TABLE v2_stays AS
SELECT DISTINCT k.stay_id,i.hadm_id,i.intime
FROM v2_keys k JOIN raw.mimiciv_icu.icustays i USING(stay_id);

CREATE OR REPLACE TEMP TABLE v2_observations AS
WITH cbc AS (
  SELECT s.stay_id,DATE_DIFF('second',s.intime,l.charttime)/3600.0 AS offset_h,
         x.variable,CAST(x.value AS DOUBLE) AS value
  FROM v2_stays s JOIN raw.mimiciv_derived.complete_blood_count l USING(hadm_id)
  CROSS JOIN LATERAL (VALUES
    ('wbc',l.wbc),('hemoglobin',l.hemoglobin),('platelet',l.platelet)
  ) x(variable,value)
  WHERE l.charttime>s.intime+INTERVAL '12 hours' AND l.charttime<=s.intime+INTERVAL '72 hours'
), chemistry AS (
  SELECT s.stay_id,DATE_DIFF('second',s.intime,l.charttime)/3600.0 AS offset_h,
         x.variable,CAST(x.value AS DOUBLE) AS value
  FROM v2_stays s JOIN raw.mimiciv_derived.chemistry l USING(hadm_id)
  CROSS JOIN LATERAL (VALUES
    ('sodium',l.sodium),('potassium',l.potassium),('chloride',l.chloride),
    ('bicarbonate',l.bicarbonate),('bun',l.bun),('creatinine',l.creatinine),
    ('glucose',l.glucose)
  ) x(variable,value)
  WHERE l.charttime>s.intime+INTERVAL '12 hours' AND l.charttime<=s.intime+INTERVAL '72 hours'
), arterial_bg AS (
  SELECT s.stay_id,DATE_DIFF('second',s.intime,l.charttime)/3600.0 AS offset_h,
         x.variable,CAST(x.value AS DOUBLE) AS value
  FROM v2_stays s JOIN raw.mimiciv_derived.bg l USING(hadm_id)
  CROSS JOIN LATERAL (VALUES
    ('ph',l.ph),('pao2',l.po2),('paco2',l.pco2),('lactate',l.lactate)
  ) x(variable,value)
  WHERE l.charttime>s.intime+INTERVAL '12 hours' AND l.charttime<=s.intime+INTERVAL '72 hours'
    AND lower(l.specimen)='art.'
), unioned AS (
  SELECT * FROM cbc UNION ALL SELECT * FROM chemistry UNION ALL SELECT * FROM arterial_bg
)
SELECT * FROM unioned
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
JOIN v2_keys k ON k.stay_id=o.stay_id AND k.landmark_h=gs.landmark_h
WHERE o.offset_h>gs.landmark_h-12 AND o.offset_h<=gs.landmark_h;

.read study_b_v2/03_pipeline/v2_lab_feature_core_v0_1.sql

COPY (SELECT * FROM v2_lab_features_long ORDER BY stay_key,landmark_h,variable)
TO 'study_b_v2/restricted/V2_MIMIC_LAB_FEATURES_LONG_V0_1.parquet'
(FORMAT PARQUET,COMPRESSION ZSTD);
