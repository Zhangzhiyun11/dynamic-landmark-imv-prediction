-- Predictor-only eICU Tier A feature derivation V0.2.

ATTACH '../database/eicu-crd-2.0.duckdb' AS raw (READ_ONLY);
SET preserve_insertion_order=false;
SET threads=4;

CREATE OR REPLACE TEMP TABLE v2_keys AS
SELECT database,CAST(icu_stay_key AS VARCHAR) AS stay_key,landmark_h,hospital_id
FROM read_parquet('study_b_v2/restricted/V2_EICU_ROLLING_COHORT_KEYS_V0_1.parquet');

CREATE OR REPLACE TEMP TABLE v2_stays AS
SELECT DISTINCT icu_stay_key
FROM read_parquet('study_b_v2/restricted/V2_EICU_ROLLING_COHORT_KEYS_V0_1.parquet');

CREATE OR REPLACE TEMP TABLE v2_observations AS
WITH periodic AS (
  SELECT v.patientunitstayid AS stay_id,v.observationoffset/60.0 AS offset_h,
         x.variable,CAST(x.value AS DOUBLE) AS raw_value,1 AS source_priority
  FROM v2_stays s JOIN raw.main.vitalperiodic v ON v.patientunitstayid=s.icu_stay_key
  CROSS JOIN LATERAL (VALUES
    ('rr',CAST(v.respiration AS DOUBLE)),('spo2',CAST(v.sao2 AS DOUBLE)),
    ('hr',CAST(v.heartrate AS DOUBLE)),('sbp',CAST(v.systemicsystolic AS DOUBLE)),
    ('map',CAST(v.systemicmean AS DOUBLE)),('temperature',CAST(v.temperature AS DOUBLE))
  ) x(variable,value)
  WHERE v.observationoffset BETWEEN 720 AND 4320 AND x.value IS NOT NULL
), aperiodic AS (
  SELECT v.patientunitstayid,v.observationoffset/60.0,x.variable,
         CAST(x.value AS DOUBLE),2
  FROM v2_stays s JOIN raw.main.vitalaperiodic v ON v.patientunitstayid=s.icu_stay_key
  CROSS JOIN LATERAL (VALUES
    ('sbp',CAST(v.noninvasivesystolic AS DOUBLE)),('map',CAST(v.noninvasivemean AS DOUBLE))
  ) x(variable,value)
  WHERE v.observationoffset BETWEEN 720 AND 4320 AND x.value IS NOT NULL
), nursing AS (
  SELECT n.patientunitstayid,n.NURSINGCHARTOFFSET/60.0,
         CASE n.NURSINGCHARTCELLTYPEVALLABEL
           WHEN 'Respiratory Rate' THEN 'rr'
           WHEN 'Heart Rate' THEN 'hr'
           WHEN 'SpO2' THEN 'spo2'
           WHEN 'O2 Saturation' THEN 'spo2'
           WHEN 'MAP (mmHg)' THEN 'map'
           WHEN 'Arterial Line MAP (mmHg)' THEN 'map'
           WHEN 'Temperature' THEN 'temperature'
           WHEN 'Glasgow coma score' THEN 'gcs'
         END AS variable,
         CASE WHEN n.NURSINGCHARTCELLTYPEVALLABEL='Temperature'
                    AND n.NURSINGCHARTCELLTYPEVALNAME='Temperature (F)'
                THEN (TRY_CAST(n.NURSINGCHARTVALUE AS DOUBLE)-32.0)*5.0/9.0
              ELSE TRY_CAST(n.NURSINGCHARTVALUE AS DOUBLE) END AS raw_value,
         3 AS source_priority
  FROM v2_stays s JOIN raw.main.NURSECHARTING n ON n.patientunitstayid=s.icu_stay_key
  WHERE n.NURSINGCHARTOFFSET BETWEEN 720 AND 4320
    AND TRY_CAST(n.NURSINGCHARTVALUE AS DOUBLE) IS NOT NULL
    AND (
      n.NURSINGCHARTCELLTYPEVALLABEL IN
        ('Respiratory Rate','Heart Rate','SpO2','O2 Saturation',
         'MAP (mmHg)','Arterial Line MAP (mmHg)')
      OR (n.NURSINGCHARTCELLTYPEVALLABEL='Temperature'
          AND n.NURSINGCHARTCELLTYPEVALNAME IN ('Temperature (C)','Temperature (F)'))
      OR (n.NURSINGCHARTCELLTYPEVALLABEL='Glasgow coma score'
          AND n.NURSINGCHARTCELLTYPEVALNAME='GCS Total')
    )
)
SELECT * FROM periodic UNION ALL SELECT * FROM aperiodic UNION ALL SELECT * FROM nursing;

CREATE OR REPLACE TEMP TABLE v2_raw AS
SELECT k.database,k.stay_key,k.landmark_h,k.hospital_id,
       o.offset_h,o.variable,o.raw_value,o.source_priority
FROM v2_observations o
CROSS JOIN LATERAL generate_series(
  CAST(GREATEST(24,CEIL(o.offset_h/4.0)*4) AS BIGINT),
  CAST(LEAST(72,FLOOR((o.offset_h+12)/4.0)*4) AS BIGINT),4
) gs(landmark_h)
JOIN v2_keys k ON k.stay_key=CAST(o.stay_id AS VARCHAR)
 AND k.landmark_h=gs.landmark_h
WHERE gs.landmark_h BETWEEN 24 AND 72;

.read study_b_v2/03_pipeline/v2_tier_a_feature_core_v0_2.sql

COPY (
  SELECT * FROM v2_tier_a_features_long ORDER BY stay_key,landmark_h,variable
) TO 'study_b_v2/restricted/V2_EICU_TIER_A_FEATURES_LONG_V0_2.parquet'
  (FORMAT PARQUET,COMPRESSION ZSTD);

COPY (
  SELECT database,landmark_h,variable,COUNT(*) AS rows_with_variable,
         MEDIAN(measurement_count) AS median_measurements,
         MEDIAN(nonempty_bin_count) AS median_nonempty_bins,
         AVG(CAST(theilsen_slope IS NOT NULL AS INTEGER)) AS slope_available_prop
  FROM v2_tier_a_features_long
  GROUP BY database,landmark_h,variable ORDER BY landmark_h,variable
) TO 'study_b_v2/04_qc/V2_EICU_TIER_A_FEATURE_SUMMARY_V0_2.csv'
  (HEADER,DELIMITER ',');
