-- Expects v2_lab_raw(database,stay_key,landmark_h,variable,offset_h,value).

CREATE OR REPLACE TEMP TABLE v2_lab_features_long AS
WITH collapsed AS (
  SELECT database,stay_key,landmark_h,variable,offset_h,MEDIAN(value) AS value
  FROM v2_lab_raw GROUP BY database,stay_key,landmark_h,variable,offset_h
), base AS (
  SELECT database,stay_key,landmark_h,variable,
         arg_max(value,offset_h) AS latest_value,MAX(offset_h) AS latest_offset_h,
         arg_min(value,offset_h) AS earliest_value,MIN(offset_h) AS earliest_offset_h,
         MEDIAN(value) AS median_value,MIN(value) AS min_value,MAX(value) AS max_value,
         COUNT(*) AS measurement_count
  FROM collapsed GROUP BY database,stay_key,landmark_h,variable
)
SELECT database,stay_key,landmark_h,variable,latest_value,latest_offset_h,
       median_value,min_value,max_value,
       CASE WHEN measurement_count>=2 AND latest_offset_h>earliest_offset_h
            THEN latest_value-earliest_value END AS delta_value,
       CASE WHEN measurement_count>=2 AND latest_offset_h>earliest_offset_h
            THEN (latest_value-earliest_value)/(latest_offset_h-earliest_offset_h) END AS slope_value,
       measurement_count
FROM base;
