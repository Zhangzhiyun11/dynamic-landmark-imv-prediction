-- Predictor-only MIMIC Tier A feature derivation V0.2.

ATTACH '../database/mimiciv-3.1.duckdb' AS raw (READ_ONLY);
SET preserve_insertion_order=false;
SET threads=4;

CREATE OR REPLACE TEMP TABLE v2_keys AS
SELECT database,CAST(stay_id AS VARCHAR) AS stay_key,landmark_h,
       NULL::INTEGER AS hospital_id
FROM read_parquet('study_b_v2/restricted/V2_MIMIC_ROLLING_COHORT_KEYS_V0_1.parquet');

CREATE OR REPLACE TEMP TABLE v2_stays AS
SELECT DISTINCT stay_id
FROM read_parquet('study_b_v2/restricted/V2_MIMIC_ROLLING_COHORT_KEYS_V0_1.parquet');

CREATE OR REPLACE TEMP TABLE v2_observations AS
WITH vital AS (
  SELECT i.stay_id,DATE_DIFF('second',i.intime,v.charttime)/3600.0 AS offset_h,
         x.variable,CAST(x.value AS DOUBLE) AS raw_value,1 AS source_priority
  FROM v2_stays s
  JOIN raw.mimiciv_icu.icustays i USING(stay_id)
  JOIN raw.mimiciv_derived.vitalsign v USING(stay_id)
  CROSS JOIN LATERAL (VALUES
    ('rr',v.resp_rate),('spo2',v.spo2),('hr',v.heart_rate),
    ('sbp',COALESCE(v.sbp,v.sbp_ni)),('map',COALESCE(v.mbp,v.mbp_ni)),
    ('temperature',CAST(v.temperature AS DOUBLE))
  ) x(variable,value)
  WHERE v.charttime>i.intime+INTERVAL '12 hours' AND v.charttime<=i.intime+INTERVAL '72 hours'
    AND x.value IS NOT NULL
), neurologic AS (
  SELECT i.stay_id,DATE_DIFF('second',i.intime,g.charttime)/3600.0 AS offset_h,
         'gcs' AS variable,CAST(g.gcs AS DOUBLE) AS raw_value,1 AS source_priority
  FROM v2_stays s
  JOIN raw.mimiciv_icu.icustays i USING(stay_id)
  JOIN raw.mimiciv_derived.gcs g USING(stay_id)
  WHERE g.charttime>i.intime+INTERVAL '12 hours' AND g.charttime<=i.intime+INTERVAL '72 hours'
    AND g.gcs IS NOT NULL
)
SELECT * FROM vital UNION ALL SELECT * FROM neurologic;

CREATE OR REPLACE TEMP TABLE v2_raw AS
SELECT k.database,k.stay_key,k.landmark_h,k.hospital_id,
       o.offset_h,o.variable,o.raw_value,o.source_priority
FROM v2_observations o
CROSS JOIN LATERAL generate_series(
  CAST(GREATEST(24,CEIL(o.offset_h/4.0)*4) AS BIGINT),
  CAST(LEAST(72,CEIL((o.offset_h+12)/4.0)*4-4) AS BIGINT),4
) gs(landmark_h)
JOIN v2_keys k ON k.stay_key=CAST(o.stay_id AS VARCHAR)
 AND k.landmark_h=gs.landmark_h
WHERE gs.landmark_h BETWEEN 24 AND 72;

.read study_b_v2/03_pipeline/v2_tier_a_feature_core_v0_2.sql

COPY (
  SELECT * FROM v2_tier_a_features_long ORDER BY stay_key,landmark_h,variable
) TO 'study_b_v2/restricted/V2_MIMIC_TIER_A_FEATURES_LONG_V0_2.parquet'
  (FORMAT PARQUET,COMPRESSION ZSTD);

COPY (
  SELECT database,landmark_h,variable,COUNT(*) AS rows_with_variable,
         MEDIAN(measurement_count) AS median_measurements,
         MEDIAN(nonempty_bin_count) AS median_nonempty_bins,
         AVG(CAST(theilsen_slope IS NOT NULL AS INTEGER)) AS slope_available_prop
  FROM v2_tier_a_features_long
  GROUP BY database,landmark_h,variable ORDER BY landmark_h,variable
) TO 'study_b_v2/04_qc/V2_MIMIC_TIER_A_FEATURE_SUMMARY_V0_2.csv'
  (HEADER,DELIMITER ',');
