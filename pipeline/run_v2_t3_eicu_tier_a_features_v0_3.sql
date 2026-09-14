-- Predictor-only eICU Tier A feature derivation V0.3.
-- High-frequency observations are aggregated once into right-closed 2 h bins,
-- then mapped to rolling (t-12,t] windows.

ATTACH '../database/eicu-crd-2.0.duckdb' AS raw (READ_ONLY);
SET preserve_insertion_order=false;
SET threads=4;

CREATE OR REPLACE TEMP TABLE v2_keys AS
SELECT database,CAST(icu_stay_key AS VARCHAR) AS stay_key,landmark_h,hospital_id
FROM read_parquet('study_b_v2/restricted/V2_EICU_ROLLING_COHORT_KEYS_V0_1.parquet');

CREATE OR REPLACE TEMP TABLE v2_stays AS
SELECT DISTINCT icu_stay_key
FROM read_parquet('study_b_v2/restricted/V2_EICU_ROLLING_COHORT_KEYS_V0_1.parquet');

CREATE OR REPLACE TEMP TABLE v2_source_bins AS
WITH periodic_rr AS (
  SELECT v.patientunitstayid AS stay_id,CEIL(v.observationoffset/120.0)*2 AS bin_end_h,
         'rr' AS variable,MEDIAN(CAST(v.respiration AS DOUBLE)) AS bin_median,
         COUNT(*) AS measurement_count,
         arg_max(CAST(v.respiration AS DOUBLE),v.observationoffset) AS bin_latest_value,
         MAX(v.observationoffset)/60.0 AS bin_latest_offset_h
  FROM v2_stays s JOIN raw.main.vitalperiodic v ON v.patientunitstayid=s.icu_stay_key
  WHERE v.observationoffset>720 AND v.observationoffset<=4320
    AND v.respiration>0 AND v.respiration<70
  GROUP BY v.patientunitstayid,bin_end_h
), periodic_spo2 AS (
  SELECT v.patientunitstayid,CEIL(v.observationoffset/120.0)*2,'spo2',
         MEDIAN(CAST(v.sao2 AS DOUBLE)),COUNT(*),
         arg_max(CAST(v.sao2 AS DOUBLE),v.observationoffset),MAX(v.observationoffset)/60.0
  FROM v2_stays s JOIN raw.main.vitalperiodic v ON v.patientunitstayid=s.icu_stay_key
  WHERE v.observationoffset>720 AND v.observationoffset<=4320
    AND v.sao2>0 AND v.sao2<=100
  GROUP BY v.patientunitstayid,CEIL(v.observationoffset/120.0)*2
), periodic_hr AS (
  SELECT v.patientunitstayid,CEIL(v.observationoffset/120.0)*2,'hr',
         MEDIAN(CAST(v.heartrate AS DOUBLE)),COUNT(*),
         arg_max(CAST(v.heartrate AS DOUBLE),v.observationoffset),MAX(v.observationoffset)/60.0
  FROM v2_stays s JOIN raw.main.vitalperiodic v ON v.patientunitstayid=s.icu_stay_key
  WHERE v.observationoffset>720 AND v.observationoffset<=4320
    AND v.heartrate>0 AND v.heartrate<300
  GROUP BY v.patientunitstayid,CEIL(v.observationoffset/120.0)*2
), blood_pressure_rows AS (
  SELECT v.patientunitstayid AS stay_id,v.observationoffset AS offset_min,
         x.variable,CAST(x.value AS DOUBLE) AS value,1 AS source_priority
  FROM v2_stays s JOIN raw.main.vitalaperiodic v ON v.patientunitstayid=s.icu_stay_key
  CROSS JOIN LATERAL (VALUES
    ('sbp',v.noninvasivesystolic),('map',v.noninvasivemean)
  ) x(variable,value)
  WHERE v.observationoffset>720 AND v.observationoffset<=4320
    AND ((x.variable='sbp' AND x.value>0 AND x.value<350)
      OR (x.variable='map' AND x.value>0 AND x.value<300))
  UNION ALL
  SELECT v.patientunitstayid,v.observationoffset,x.variable,
         CAST(x.value AS DOUBLE),2
  FROM v2_stays s JOIN raw.main.vitalperiodic v ON v.patientunitstayid=s.icu_stay_key
  CROSS JOIN LATERAL (VALUES
    ('sbp',v.systemicsystolic),('map',v.systemicmean)
  ) x(variable,value)
  WHERE v.observationoffset>720 AND v.observationoffset<=4320
    AND ((x.variable='sbp' AND x.value>0 AND x.value<350)
      OR (x.variable='map' AND x.value>0 AND x.value<300))
), blood_pressure_selected AS (
  SELECT *
  FROM blood_pressure_rows
  QUALIFY source_priority=MIN(source_priority) OVER (
    PARTITION BY stay_id,CEIL(offset_min/120.0)*2,variable
  )
), blood_pressure AS (
  SELECT stay_id,CEIL(offset_min/120.0)*2 AS bin_end_h,variable,
         MEDIAN(value) AS bin_median,COUNT(*) AS measurement_count,
         arg_max(value,offset_min) AS bin_latest_value,
         MAX(offset_min)/60.0 AS bin_latest_offset_h
  FROM blood_pressure_selected
  GROUP BY stay_id,bin_end_h,variable
), temperature_time AS (
  SELECT n.patientunitstayid AS stay_id,n.NURSINGCHARTOFFSET AS offset_min,
         MEDIAN(CASE WHEN n.NURSINGCHARTCELLTYPEVALNAME='Temperature (F)'
           THEN (TRY_CAST(n.NURSINGCHARTVALUE AS DOUBLE)-32.0)*5.0/9.0
           ELSE TRY_CAST(n.NURSINGCHARTVALUE AS DOUBLE) END) AS value
  FROM v2_stays s JOIN raw.main.NURSECHARTING n ON n.patientunitstayid=s.icu_stay_key
  WHERE n.NURSINGCHARTOFFSET>720 AND n.NURSINGCHARTOFFSET<=4320
    AND n.NURSINGCHARTCELLTYPEVALLABEL='Temperature'
    AND n.NURSINGCHARTCELLTYPEVALNAME IN ('Temperature (C)','Temperature (F)')
    AND TRY_CAST(n.NURSINGCHARTVALUE AS DOUBLE) IS NOT NULL
  GROUP BY n.patientunitstayid,n.NURSINGCHARTOFFSET
), temperature AS (
  SELECT stay_id,CEIL(offset_min/120.0)*2 AS bin_end_h,'temperature' AS variable,
         MEDIAN(value) AS bin_median,COUNT(*) AS measurement_count,
         arg_max(value,offset_min) AS bin_latest_value,MAX(offset_min)/60.0 AS bin_latest_offset_h
  FROM temperature_time WHERE value>25 AND value<45 GROUP BY stay_id,bin_end_h
), gcs_time AS (
  SELECT n.patientunitstayid AS stay_id,n.NURSINGCHARTOFFSET AS offset_min,
         MEDIAN(TRY_CAST(n.NURSINGCHARTVALUE AS DOUBLE)) AS value
  FROM v2_stays s JOIN raw.main.NURSECHARTING n ON n.patientunitstayid=s.icu_stay_key
  WHERE n.NURSINGCHARTOFFSET>720 AND n.NURSINGCHARTOFFSET<=4320
    AND n.NURSINGCHARTCELLTYPEVALLABEL='Glasgow coma score'
    AND n.NURSINGCHARTCELLTYPEVALNAME='GCS Total'
    AND TRY_CAST(n.NURSINGCHARTVALUE AS DOUBLE) BETWEEN 3 AND 15
  GROUP BY n.patientunitstayid,n.NURSINGCHARTOFFSET
), gcs AS (
  SELECT stay_id,CEIL(offset_min/120.0)*2 AS bin_end_h,'gcs' AS variable,
         MEDIAN(value) AS bin_median,COUNT(*) AS measurement_count,
         arg_max(value,offset_min) AS bin_latest_value,MAX(offset_min)/60.0 AS bin_latest_offset_h
  FROM gcs_time GROUP BY stay_id,bin_end_h
)
SELECT * FROM periodic_rr UNION ALL SELECT * FROM periodic_spo2
UNION ALL SELECT * FROM periodic_hr UNION ALL SELECT * FROM blood_pressure
UNION ALL SELECT * FROM temperature
UNION ALL SELECT * FROM gcs;

CREATE OR REPLACE TEMP TABLE v2_window_bins AS
SELECT k.database,k.stay_key,k.landmark_h,k.hospital_id,b.variable,
       b.bin_end_h,b.bin_end_h-k.landmark_h-1 AS bin_midpoint_relative_h,
       b.bin_median,b.measurement_count,b.bin_latest_value,b.bin_latest_offset_h
FROM v2_source_bins b
CROSS JOIN LATERAL generate_series(
  CAST(GREATEST(24,CEIL(b.bin_end_h/4.0)*4) AS BIGINT),
  CAST(LEAST(72,CEIL((b.bin_end_h+12)/4.0)*4-4) AS BIGINT),4
) gs(landmark_h)
JOIN v2_keys k ON k.stay_key=CAST(b.stay_id AS VARCHAR)
 AND k.landmark_h=gs.landmark_h
WHERE b.bin_end_h>gs.landmark_h-12 AND b.bin_end_h<=gs.landmark_h;

CREATE OR REPLACE TEMP TABLE v2_tier_a_features_long AS
WITH base AS (
  SELECT database,stay_key,landmark_h,ANY_VALUE(hospital_id) AS hospital_id,variable,
         COUNT(*) AS nonempty_bin_count,SUM(measurement_count) AS measurement_count,
         MEDIAN(bin_median) AS median_value,MIN(bin_median) AS min_value,
         MAX(bin_median) AS max_value,
         CASE WHEN COUNT(*)>=2 THEN STDDEV_SAMP(bin_median) END AS sd_value,
         CASE WHEN COUNT(*)>=2 THEN QUANTILE_CONT(bin_median,0.75)-QUANTILE_CONT(bin_median,0.25) END AS iqr_value,
         arg_max(bin_latest_value,bin_latest_offset_h) AS latest_value,
         MAX(bin_latest_offset_h) AS latest_offset_h
  FROM v2_window_bins GROUP BY database,stay_key,landmark_h,variable
), slopes AS (
  SELECT database,stay_key,landmark_h,variable,MEDIAN(pair_slope) AS theilsen_slope
  FROM (
    SELECT a.database,a.stay_key,a.landmark_h,a.variable,
      (b.bin_median-a.bin_median)/(b.bin_midpoint_relative_h-a.bin_midpoint_relative_h) AS pair_slope
    FROM v2_window_bins a JOIN v2_window_bins b
      ON b.database=a.database AND b.stay_key=a.stay_key
     AND b.landmark_h=a.landmark_h AND b.variable=a.variable
     AND b.bin_end_h>a.bin_end_h
  ) pairs GROUP BY database,stay_key,landmark_h,variable
), flags AS (
  SELECT *,CASE variable
    WHEN 'rr' THEN bin_median>=24 WHEN 'spo2' THEN bin_median<92
    WHEN 'hr' THEN bin_median>=100 WHEN 'sbp' THEN bin_median<90
    WHEN 'map' THEN bin_median<65
    WHEN 'temperature' THEN bin_median<36 OR bin_median>=38
    WHEN 'gcs' THEN bin_median<13 ELSE FALSE END AS abnormal
  FROM v2_window_bins
), proportions AS (
  SELECT database,stay_key,landmark_h,variable,AVG(CAST(abnormal AS INTEGER)) AS abnormal_prop
  FROM flags GROUP BY database,stay_key,landmark_h,variable
), runs AS (
  SELECT database,stay_key,landmark_h,variable,MAX(run_length) AS longest_abnormal_run
  FROM (
    SELECT database,stay_key,landmark_h,variable,run_group,COUNT(*) AS run_length
    FROM (
      SELECT database,stay_key,landmark_h,variable,bin_end_h,
        CAST(bin_end_h/2 AS INTEGER)-ROW_NUMBER() OVER(
          PARTITION BY database,stay_key,landmark_h,variable ORDER BY bin_end_h
        ) AS run_group
      FROM flags WHERE abnormal
    ) numbered GROUP BY database,stay_key,landmark_h,variable,run_group
  ) grouped_runs GROUP BY database,stay_key,landmark_h,variable
)
SELECT b.database,b.stay_key,b.landmark_h,b.hospital_id,b.variable,
       b.latest_value,b.latest_offset_h,b.measurement_count,b.nonempty_bin_count,
       b.median_value,b.min_value,b.max_value,b.sd_value,b.iqr_value,
       CASE WHEN b.nonempty_bin_count>=4 THEN s.theilsen_slope END AS theilsen_slope,
       p.abnormal_prop,COALESCE(r.longest_abnormal_run,0) AS longest_abnormal_run
FROM base b LEFT JOIN slopes s USING(database,stay_key,landmark_h,variable)
LEFT JOIN proportions p USING(database,stay_key,landmark_h,variable)
LEFT JOIN runs r USING(database,stay_key,landmark_h,variable);

COPY (
  SELECT * FROM v2_tier_a_features_long ORDER BY stay_key,landmark_h,variable
) TO 'study_b_v2/restricted/V2_EICU_TIER_A_FEATURES_LONG_V0_3.parquet'
  (FORMAT PARQUET,COMPRESSION ZSTD);

COPY (
  SELECT database,landmark_h,variable,COUNT(*) AS rows_with_variable,
         MEDIAN(measurement_count) AS median_measurements,
         MEDIAN(nonempty_bin_count) AS median_nonempty_bins,
         AVG(CAST(theilsen_slope IS NOT NULL AS INTEGER)) AS slope_available_prop
  FROM v2_tier_a_features_long GROUP BY database,landmark_h,variable
  ORDER BY landmark_h,variable
) TO 'study_b_v2/04_qc/V2_EICU_TIER_A_FEATURE_SUMMARY_V0_3.csv'
  (HEADER,DELIMITER ',');
