-- Predictor-only MIMIC Tier B feature derivation V0.1.

ATTACH '../database/mimiciv-3.1.duckdb' AS raw (READ_ONLY);
SET preserve_insertion_order=false;
SET threads=4;

CREATE OR REPLACE TEMP TABLE v2_keys AS
SELECT database,stay_id,CAST(stay_id AS VARCHAR) AS stay_key,landmark_h
FROM read_parquet('study_b_v2/restricted/V2_MIMIC_ROLLING_COHORT_KEYS_V0_1.parquet');

CREATE OR REPLACE TEMP TABLE v2_stays AS
SELECT DISTINCT k.stay_id,i.intime
FROM v2_keys k JOIN raw.mimiciv_icu.icustays i USING(stay_id);

CREATE OR REPLACE TEMP TABLE oxygen_events AS
WITH device_rows AS (
  SELECT o.stay_id,DATE_DIFF('second',s.intime,o.charttime)/3600.0 AS offset_h,
    CASE
      WHEN lower(trim(device)) IN ('bipap mask','cpap mask') THEN 4
      WHEN lower(trim(device)) IN ('high flow nasal cannula','vapomist') THEN 3
      WHEN lower(trim(device)) IN
        ('non-rebreather','venti mask','medium conc mask','face tent',
         'aerosol-cool','high flow neb') THEN 2
      WHEN lower(trim(device)) IN ('nasal cannula','oxymizer') THEN 1
      WHEN lower(trim(device))='none' THEN 0
      WHEN lower(trim(device)) IN ('endotracheal tube','tracheostomy tube') THEN -2
    END AS support_level
  FROM v2_stays s JOIN raw.mimiciv_derived.oxygen_delivery o USING(stay_id)
  CROSS JOIN LATERAL (VALUES
    (o.o2_delivery_device_1),(o.o2_delivery_device_2),
    (o.o2_delivery_device_3),(o.o2_delivery_device_4)
  ) d(device)
  WHERE o.charttime>s.intime+INTERVAL '12 hours'
    AND o.charttime<=s.intime+INTERVAL '72 hours'
    AND device IS NOT NULL
), collapsed AS (
  SELECT stay_id,offset_h,MAX(support_level) FILTER(WHERE support_level>=0) AS support_level,
         BOOL_OR(support_level=-2) AS imv_device_present
  FROM device_rows GROUP BY stay_id,offset_h
)
SELECT stay_id,offset_h,support_level FROM collapsed
WHERE support_level IS NOT NULL AND NOT imv_device_present;

CREATE OR REPLACE TEMP TABLE oxygen_features AS
WITH joined AS (
  SELECT k.stay_key,k.landmark_h,e.offset_h,e.support_level,
         LAG(e.support_level) OVER (
           PARTITION BY k.stay_key,k.landmark_h ORDER BY e.offset_h
         ) AS prior_level
  FROM v2_keys k JOIN oxygen_events e ON e.stay_id=k.stay_id
   AND e.offset_h>k.landmark_h-12 AND e.offset_h<=k.landmark_h
)
SELECT stay_key,landmark_h,
       arg_max(support_level,offset_h) AS oxygen_latest_level,
       MAX(support_level) AS oxygen_max_level,
       COUNT_IF(prior_level IS NOT NULL AND support_level>prior_level) AS oxygen_escalation_count,
       COUNT(*) AS oxygen_documentation_count,
       MAX(offset_h) AS oxygen_latest_offset_h,
       MAX(landmark_h-offset_h) AS oxygen_time_since_latest_h_raw
FROM joined GROUP BY stay_key,landmark_h;

CREATE OR REPLACE TEMP TABLE urine_features AS
SELECT k.stay_key,k.landmark_h,
       SUM(u.urineoutput) FILTER(WHERE offset_h>k.landmark_h-6) AS urine_6h_ml,
       SUM(u.urineoutput) AS urine_12h_ml,
       COUNT(*) FILTER(WHERE offset_h>k.landmark_h-6) AS urine_6h_count,
       COUNT(*) AS urine_12h_count,
       MAX(offset_h) AS urine_latest_offset_h
FROM v2_keys k JOIN v2_stays s USING(stay_id)
JOIN LATERAL (
  SELECT u.urineoutput,
         DATE_DIFF('second',s.intime,u.charttime)/3600.0 AS offset_h
  FROM raw.mimiciv_derived.urine_output u
  WHERE u.stay_id=k.stay_id
    AND u.charttime>s.intime+(k.landmark_h-12)*INTERVAL '1 hour'
    AND u.charttime<=s.intime+k.landmark_h*INTERVAL '1 hour'
    AND u.urineoutput BETWEEN 0 AND 5000
) u ON TRUE
GROUP BY k.stay_key,k.landmark_h;

CREATE OR REPLACE TEMP TABLE vaso_features AS
WITH rows AS (
  SELECT k.stay_key,k.landmark_h,
    DATE_DIFF('second',s.intime,v.starttime)/3600.0 AS start_offset_h,
    DATE_DIFF('second',s.intime,v.endtime)/3600.0 AS end_offset_h,
    ((v.dopamine IS NOT NULL)::INTEGER+(v.epinephrine IS NOT NULL)::INTEGER+
     (v.norepinephrine IS NOT NULL)::INTEGER+(v.phenylephrine IS NOT NULL)::INTEGER+
     (v.vasopressin IS NOT NULL)::INTEGER) AS agent_count
  FROM v2_keys k JOIN v2_stays s USING(stay_id)
  JOIN raw.mimiciv_derived.vasoactive_agent v ON v.stay_id=k.stay_id
   AND v.endtime>s.intime+(k.landmark_h-12)*INTERVAL '1 hour'
   AND v.starttime<=s.intime+k.landmark_h*INTERVAL '1 hour'
  WHERE COALESCE(v.dopamine,v.epinephrine,v.norepinephrine,
                 v.phenylephrine,v.vasopressin) IS NOT NULL
)
SELECT stay_key,landmark_h,1 AS vaso_any_12h,
       MAX(CASE WHEN start_offset_h<=landmark_h AND end_offset_h>=landmark_h
                THEN 1 ELSE 0 END) AS vaso_current,
       MAX(agent_count) AS vaso_max_agent_count,
       MAX(start_offset_h) AS vaso_latest_start_offset_h
FROM rows GROUP BY stay_key,landmark_h;

COPY (
  SELECT k.database,k.stay_key,k.landmark_h,
         o.oxygen_latest_level,o.oxygen_max_level,o.oxygen_escalation_count,
         o.oxygen_documentation_count,o.oxygen_latest_offset_h,
         CASE WHEN o.oxygen_latest_offset_h IS NOT NULL
              THEN k.landmark_h-o.oxygen_latest_offset_h END AS oxygen_time_since_latest_h,
         u.urine_6h_ml,u.urine_12h_ml,u.urine_6h_count,u.urine_12h_count,
         u.urine_latest_offset_h,
         COALESCE(v.vaso_any_12h,0) AS vaso_any_12h,
         COALESCE(v.vaso_current,0) AS vaso_current,
         COALESCE(v.vaso_max_agent_count,0) AS vaso_max_agent_count,
         v.vaso_latest_start_offset_h,
         1 AS oxygen_interface_supported,1 AS urine_interface_supported,
         1 AS vaso_interface_supported
  FROM v2_keys k
  LEFT JOIN oxygen_features o USING(stay_key,landmark_h)
  LEFT JOIN urine_features u USING(stay_key,landmark_h)
  LEFT JOIN vaso_features v USING(stay_key,landmark_h)
  ORDER BY k.stay_key,k.landmark_h
) TO 'study_b_v2/restricted/V2_MIMIC_TIER_B_FEATURES_V0_1.parquet'
  (FORMAT PARQUET,COMPRESSION ZSTD);
