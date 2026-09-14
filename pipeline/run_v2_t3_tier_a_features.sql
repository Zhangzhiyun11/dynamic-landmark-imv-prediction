-- V2 T3 predictor-only Tier A feature derivation.
-- Reads cohort-key files and predictor source tables only; no outcome file.

ATTACH '../database/mimiciv-3.1.duckdb' AS mimic_raw (READ_ONLY);
ATTACH '.restricted_data/mimic_t1/t1_mimic.duckdb' AS mimic_old (READ_ONLY);
ATTACH '../database/eicu-crd-2.0.duckdb' AS eicu_raw (READ_ONLY);
SET preserve_insertion_order=false;
SET threads=4;

CREATE OR REPLACE TEMP TABLE v2_keys AS
SELECT database,CAST(stay_id AS VARCHAR) AS stay_key,landmark_h,
       NULL::INTEGER AS hospital_id
FROM read_parquet('study_b_v2/restricted/V2_MIMIC_ROLLING_COHORT_KEYS_V0_1.parquet')
UNION ALL
SELECT database,CAST(icu_stay_key AS VARCHAR),landmark_h,hospital_id
FROM read_parquet('study_b_v2/restricted/V2_EICU_ROLLING_COHORT_KEYS_V0_1.parquet');

CREATE OR REPLACE TEMP TABLE v2_raw AS
-- MIMIC vitals
SELECT k.database,k.stay_key,k.landmark_h,k.hospital_id,
       DATE_DIFF('second',i.intime,v.charttime)/3600.0 AS offset_h,
       x.variable,CAST(x.value AS DOUBLE) AS raw_value,1 AS source_priority
FROM read_parquet('study_b_v2/restricted/V2_MIMIC_ROLLING_COHORT_KEYS_V0_1.parquet') km
JOIN mimic_raw.mimiciv_icu.icustays i USING(stay_id)
JOIN mimic_raw.mimiciv_derived.vitalsign v USING(stay_id)
JOIN v2_keys k ON k.database='MIMIC-IV v3.1'
 AND k.stay_key=CAST(km.stay_id AS VARCHAR) AND k.landmark_h=km.landmark_h
CROSS JOIN LATERAL (VALUES
  ('rr',v.resp_rate),('spo2',v.spo2),('hr',v.heart_rate),
  ('sbp',COALESCE(v.sbp,v.sbp_ni)),('map',COALESCE(v.mbp,v.mbp_ni)),
  ('temperature',CAST(v.temperature AS DOUBLE))
) x(variable,value)
WHERE v.charttime BETWEEN i.intime+(km.landmark_h-12)*INTERVAL '1 hour'
                      AND i.intime+km.landmark_h*INTERVAL '1 hour'
  AND x.value IS NOT NULL
UNION ALL
-- MIMIC GCS
SELECT k.database,k.stay_key,k.landmark_h,k.hospital_id,
       DATE_DIFF('second',i.intime,g.charttime)/3600.0,'gcs',CAST(g.gcs AS DOUBLE),1
FROM read_parquet('study_b_v2/restricted/V2_MIMIC_ROLLING_COHORT_KEYS_V0_1.parquet') km
JOIN mimic_raw.mimiciv_icu.icustays i USING(stay_id)
JOIN mimic_raw.mimiciv_derived.gcs g USING(stay_id)
JOIN v2_keys k ON k.database='MIMIC-IV v3.1'
 AND k.stay_key=CAST(km.stay_id AS VARCHAR) AND k.landmark_h=km.landmark_h
WHERE g.charttime BETWEEN i.intime+(km.landmark_h-12)*INTERVAL '1 hour'
                      AND i.intime+km.landmark_h*INTERVAL '1 hour'
  AND g.gcs IS NOT NULL
UNION ALL
-- eICU periodic vitals
SELECT k.database,k.stay_key,k.landmark_h,k.hospital_id,
       v.observationoffset/60.0,x.variable,CAST(x.value AS DOUBLE),1
FROM read_parquet('study_b_v2/restricted/V2_EICU_ROLLING_COHORT_KEYS_V0_1.parquet') ke
JOIN eicu_raw.main.vitalperiodic v ON v.patientunitstayid=ke.icu_stay_key
JOIN v2_keys k ON k.database='eICU-CRD v2.0'
 AND k.stay_key=CAST(ke.icu_stay_key AS VARCHAR) AND k.landmark_h=ke.landmark_h
CROSS JOIN LATERAL (VALUES
  ('rr',CAST(v.respiration AS DOUBLE)),('spo2',CAST(v.sao2 AS DOUBLE)),
  ('hr',CAST(v.heartrate AS DOUBLE)),('sbp',CAST(v.systemicsystolic AS DOUBLE)),
  ('map',CAST(v.systemicmean AS DOUBLE)),('temperature',CAST(v.temperature AS DOUBLE))
) x(variable,value)
WHERE v.observationoffset BETWEEN (ke.landmark_h-12)*60 AND ke.landmark_h*60
  AND x.value IS NOT NULL
UNION ALL
-- eICU aperiodic blood pressure
SELECT k.database,k.stay_key,k.landmark_h,k.hospital_id,
       v.observationoffset/60.0,x.variable,CAST(x.value AS DOUBLE),2
FROM read_parquet('study_b_v2/restricted/V2_EICU_ROLLING_COHORT_KEYS_V0_1.parquet') ke
JOIN eicu_raw.main.vitalaperiodic v ON v.patientunitstayid=ke.icu_stay_key
JOIN v2_keys k ON k.database='eICU-CRD v2.0'
 AND k.stay_key=CAST(ke.icu_stay_key AS VARCHAR) AND k.landmark_h=ke.landmark_h
CROSS JOIN LATERAL (VALUES
  ('sbp',CAST(v.noninvasivesystolic AS DOUBLE)),('map',CAST(v.noninvasivemean AS DOUBLE))
) x(variable,value)
WHERE v.observationoffset BETWEEN (ke.landmark_h-12)*60 AND ke.landmark_h*60
  AND x.value IS NOT NULL;

INSERT INTO v2_raw
SELECT k.database,k.stay_key,k.landmark_h,k.hospital_id,
       n.NURSINGCHARTOFFSET/60.0,
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
FROM read_parquet('study_b_v2/restricted/V2_EICU_ROLLING_COHORT_KEYS_V0_1.parquet') ke
JOIN eicu_raw.main.NURSECHARTING n ON n.patientunitstayid=ke.icu_stay_key
JOIN v2_keys k ON k.database='eICU-CRD v2.0'
 AND k.stay_key=CAST(ke.icu_stay_key AS VARCHAR) AND k.landmark_h=ke.landmark_h
WHERE n.NURSINGCHARTOFFSET BETWEEN (ke.landmark_h-12)*60 AND ke.landmark_h*60
  AND TRY_CAST(n.NURSINGCHARTVALUE AS DOUBLE) IS NOT NULL
  AND (
    n.NURSINGCHARTCELLTYPEVALLABEL IN
      ('Respiratory Rate','Heart Rate','SpO2','O2 Saturation',
       'MAP (mmHg)','Arterial Line MAP (mmHg)')
    OR (n.NURSINGCHARTCELLTYPEVALLABEL='Temperature'
        AND n.NURSINGCHARTCELLTYPEVALNAME IN ('Temperature (C)','Temperature (F)'))
    OR (n.NURSINGCHARTCELLTYPEVALLABEL='Glasgow coma score'
        AND n.NURSINGCHARTCELLTYPEVALNAME='GCS Total')
  );

CREATE OR REPLACE TEMP TABLE v2_valid_raw AS
SELECT DISTINCT *
FROM v2_raw
WHERE CASE variable
  WHEN 'rr' THEN raw_value>0 AND raw_value<70
  WHEN 'spo2' THEN raw_value>0 AND raw_value<=100
  WHEN 'hr' THEN raw_value>0 AND raw_value<300
  WHEN 'sbp' THEN raw_value>0 AND raw_value<350
  WHEN 'map' THEN raw_value>0 AND raw_value<300
  WHEN 'temperature' THEN raw_value>25 AND raw_value<45
  WHEN 'gcs' THEN raw_value BETWEEN 3 AND 15
  ELSE FALSE END;

CREATE OR REPLACE TEMP TABLE v2_selected_timepoints AS
WITH ranked AS (
  SELECT *,MIN(source_priority) OVER(
    PARTITION BY database,stay_key,landmark_h,variable,offset_h
  ) AS selected_priority
  FROM v2_valid_raw
)
SELECT database,stay_key,landmark_h,ANY_VALUE(hospital_id) AS hospital_id,
       variable,offset_h,MEDIAN(raw_value) AS value
FROM ranked
WHERE source_priority=selected_priority
GROUP BY database,stay_key,landmark_h,variable,offset_h;

CREATE OR REPLACE TEMP TABLE v2_bins AS
SELECT database,stay_key,landmark_h,ANY_VALUE(hospital_id) AS hospital_id,variable,
       LEAST(5,CAST(FLOOR((offset_h-(landmark_h-12))/2) AS INTEGER)) AS bin_index,
       -11.0+2.0*LEAST(5,CAST(FLOOR((offset_h-(landmark_h-12))/2) AS INTEGER)) AS bin_midpoint_relative_h,
       MEDIAN(value) AS bin_median
FROM v2_selected_timepoints
GROUP BY database,stay_key,landmark_h,variable,bin_index;

CREATE OR REPLACE TEMP TABLE v2_stats_long AS
WITH base AS (
  SELECT database,stay_key,landmark_h,ANY_VALUE(hospital_id) AS hospital_id,variable,
         COUNT(*) AS nonempty_bin_count,MEDIAN(bin_median) AS median_value,
         MIN(bin_median) AS min_value,MAX(bin_median) AS max_value,
         CASE WHEN COUNT(*)>=2 THEN STDDEV_SAMP(bin_median) END AS sd_value,
         CASE WHEN COUNT(*)>=2 THEN QUANTILE_CONT(bin_median,0.75)-QUANTILE_CONT(bin_median,0.25) END AS iqr_value
  FROM v2_bins GROUP BY database,stay_key,landmark_h,variable
), slopes AS (
  SELECT database,stay_key,landmark_h,variable,MEDIAN(pair_slope) AS theilsen_slope
  FROM (
    SELECT a.database,a.stay_key,a.landmark_h,a.variable,
           (b.bin_median-a.bin_median)/(b.bin_midpoint_relative_h-a.bin_midpoint_relative_h) AS pair_slope
    FROM v2_bins a JOIN v2_bins b
      ON b.database=a.database AND b.stay_key=a.stay_key
     AND b.landmark_h=a.landmark_h AND b.variable=a.variable
     AND b.bin_index>a.bin_index
  ) p GROUP BY database,stay_key,landmark_h,variable
), point_stats AS (
  SELECT database,stay_key,landmark_h,variable,
         COUNT(*) AS measurement_count,arg_max(value,offset_h) AS latest_value,
         MAX(offset_h) AS latest_offset_h
  FROM v2_selected_timepoints GROUP BY database,stay_key,landmark_h,variable
), flags AS (
  SELECT *,CASE variable
    WHEN 'rr' THEN bin_median>=24
    WHEN 'spo2' THEN bin_median<92
    WHEN 'hr' THEN bin_median>=100
    WHEN 'sbp' THEN bin_median<90
    WHEN 'map' THEN bin_median<65
    WHEN 'temperature' THEN bin_median<36 OR bin_median>=38
    WHEN 'gcs' THEN bin_median<13
    ELSE FALSE END AS abnormal
  FROM v2_bins
), prop AS (
  SELECT database,stay_key,landmark_h,variable,
         AVG(CAST(abnormal AS INTEGER)) AS abnormal_prop
  FROM flags GROUP BY database,stay_key,landmark_h,variable
), abnormal_runs AS (
  SELECT database,stay_key,landmark_h,variable,MAX(run_length) AS longest_abnormal_run
  FROM (
    SELECT database,stay_key,landmark_h,variable,run_group,COUNT(*) AS run_length
    FROM (
      SELECT database,stay_key,landmark_h,variable,bin_index,
             bin_index-ROW_NUMBER() OVER(
               PARTITION BY database,stay_key,landmark_h,variable ORDER BY bin_index
             ) AS run_group
      FROM flags WHERE abnormal
    ) a GROUP BY database,stay_key,landmark_h,variable,run_group
  ) r GROUP BY database,stay_key,landmark_h,variable
)
SELECT b.database,b.stay_key,b.landmark_h,b.hospital_id,b.variable,
       p.latest_value,p.latest_offset_h,p.measurement_count,b.nonempty_bin_count,
       b.median_value,b.min_value,b.max_value,b.sd_value,b.iqr_value,
       CASE WHEN b.nonempty_bin_count>=4 THEN s.theilsen_slope END AS theilsen_slope,
       f.abnormal_prop,COALESCE(r.longest_abnormal_run,0) AS longest_abnormal_run
FROM base b
JOIN point_stats p USING(database,stay_key,landmark_h,variable)
LEFT JOIN slopes s USING(database,stay_key,landmark_h,variable)
LEFT JOIN prop f USING(database,stay_key,landmark_h,variable)
LEFT JOIN abnormal_runs r USING(database,stay_key,landmark_h,variable);

CREATE OR REPLACE TEMP VIEW v2_tier_a_features_long AS
SELECT * FROM v2_stats_long;

COPY (
  SELECT * FROM v2_tier_a_features_long ORDER BY database,stay_key,landmark_h,variable
) TO 'study_b_v2/restricted/V2_TIER_A_FEATURES_LONG_V0_1.parquet'
  (FORMAT PARQUET,COMPRESSION ZSTD);

CREATE OR REPLACE TEMP TABLE v2_static_context AS
SELECT k.database,CAST(k.subject_id AS VARCHAR) AS patient_key,
       CAST(k.stay_id AS VARCHAR) AS stay_key,NULL::INTEGER AS hospital_id,
       k.anchor_year_group,NULL::SMALLINT AS hospitaldischargeyear,k.landmark_h,
       s.age,s.sex,s.admission_source_v2 AS admission_source,
       s.icu_type_candidate AS icu_type,s.preicu_los_hours,
       hw.bmi_candidate AS bmi
FROM read_parquet('study_b_v2/restricted/V2_MIMIC_ROLLING_COHORT_KEYS_V0_1.parquet') k
JOIN read_parquet('03_derived/restricted/mimic/MIMIC_STATIC_HARMONIZED_V2.parquet') s USING(stay_id)
LEFT JOIN mimic_old.mimic_t1.height_weight_candidates hw USING(stay_id)
UNION ALL
SELECT k.database,k.patient_key,CAST(k.icu_stay_key AS VARCHAR),k.hospital_id,
       NULL::VARCHAR,k.hospitaldischargeyear,k.landmark_h,
       c.age,c.sex,c.admission_source,c.icu_type,c.preicu_los_hours,
       CASE WHEN c.admissionheight>0 AND c.admissionweight>0
            THEN c.admissionweight/POWER(c.admissionheight/100.0,2) END
FROM read_parquet('study_b_v2/restricted/V2_EICU_ROLLING_COHORT_KEYS_V0_1.parquet') k
JOIN read_parquet('03_derived/restricted/eicu/EICU_COHORT.parquet') c
  ON c.icu_stay_key=k.icu_stay_key;

COPY (
  SELECT * FROM v2_static_context ORDER BY database,patient_key,stay_key,landmark_h
) TO 'study_b_v2/restricted/V2_STATIC_CONTEXT_V0_1.parquet'
  (FORMAT PARQUET,COMPRESSION ZSTD);

COPY (
  SELECT database,landmark_h,variable,
         COUNT(*) AS rows_with_variable,
         MEDIAN(measurement_count) AS median_measurements,
         MEDIAN(nonempty_bin_count) AS median_nonempty_bins,
         AVG(CAST(theilsen_slope IS NOT NULL AS INTEGER)) AS slope_available_prop
  FROM v2_tier_a_features_long
  GROUP BY database,landmark_h,variable
  ORDER BY database,landmark_h,variable
) TO 'study_b_v2/04_qc/V2_T3_TIER_A_FEATURE_SUMMARY_V0_1.csv'
  (HEADER,DELIMITER ',');
