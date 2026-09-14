-- Shared V2 Tier A aggregation core V0.2.
-- Required temp tables:
--   v2_keys(database,stay_key,landmark_h,hospital_id)
--   v2_raw(database,stay_key,landmark_h,hospital_id,offset_h,
--          variable,raw_value,source_priority)

CREATE OR REPLACE TEMP TABLE v2_valid_raw AS
SELECT DISTINCT *
FROM v2_raw
WHERE offset_h>landmark_h-12 AND offset_h<=landmark_h
  AND CASE variable
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
       GREATEST(0,LEAST(5,CAST(CEIL((offset_h-(landmark_h-12))/2.0) AS INTEGER)-1)) AS bin_index,
       -11.0+2.0*GREATEST(0,LEAST(5,CAST(CEIL((offset_h-(landmark_h-12))/2.0) AS INTEGER)-1))
         AS bin_midpoint_relative_h,
       MEDIAN(value) AS bin_median
FROM v2_selected_timepoints
GROUP BY database,stay_key,landmark_h,variable,bin_index;

CREATE OR REPLACE TEMP TABLE v2_tier_a_features_long AS
WITH base AS (
  SELECT database,stay_key,landmark_h,ANY_VALUE(hospital_id) AS hospital_id,variable,
         COUNT(*) AS nonempty_bin_count,MEDIAN(bin_median) AS median_value,
         MIN(bin_median) AS min_value,MAX(bin_median) AS max_value,
         CASE WHEN COUNT(*)>=2 THEN STDDEV_SAMP(bin_median) END AS sd_value,
         CASE WHEN COUNT(*)>=2
              THEN QUANTILE_CONT(bin_median,0.75)-QUANTILE_CONT(bin_median,0.25)
         END AS iqr_value
  FROM v2_bins GROUP BY database,stay_key,landmark_h,variable
), slopes AS (
  SELECT database,stay_key,landmark_h,variable,MEDIAN(pair_slope) AS theilsen_slope
  FROM (
    SELECT a.database,a.stay_key,a.landmark_h,a.variable,
           (b.bin_median-a.bin_median)/
           (b.bin_midpoint_relative_h-a.bin_midpoint_relative_h) AS pair_slope
    FROM v2_bins a JOIN v2_bins b
      ON b.database=a.database AND b.stay_key=a.stay_key
     AND b.landmark_h=a.landmark_h AND b.variable=a.variable
     AND b.bin_index>a.bin_index
  ) pairs
  GROUP BY database,stay_key,landmark_h,variable
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
), proportions AS (
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
    ) numbered
    GROUP BY database,stay_key,landmark_h,variable,run_group
  ) runs
  GROUP BY database,stay_key,landmark_h,variable
)
SELECT b.database,b.stay_key,b.landmark_h,b.hospital_id,b.variable,
       p.latest_value,p.latest_offset_h,p.measurement_count,b.nonempty_bin_count,
       b.median_value,b.min_value,b.max_value,b.sd_value,b.iqr_value,
       CASE WHEN b.nonempty_bin_count>=4 THEN s.theilsen_slope END AS theilsen_slope,
       a.abnormal_prop,COALESCE(r.longest_abnormal_run,0) AS longest_abnormal_run
FROM base b
JOIN point_stats p USING(database,stay_key,landmark_h,variable)
LEFT JOIN slopes s USING(database,stay_key,landmark_h,variable)
LEFT JOIN proportions a USING(database,stay_key,landmark_h,variable)
LEFT JOIN abnormal_runs r USING(database,stay_key,landmark_h,variable);
