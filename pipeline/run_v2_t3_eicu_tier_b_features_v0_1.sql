-- Predictor-only eICU Tier B feature derivation V0.1.

ATTACH '../database/eicu-crd-2.0.duckdb' AS raw (READ_ONLY);
SET preserve_insertion_order=false;
SET threads=4;

CREATE OR REPLACE TEMP TABLE v2_keys AS
SELECT database,icu_stay_key,CAST(icu_stay_key AS VARCHAR) AS stay_key,
       landmark_h,hospital_id
FROM read_parquet('study_b_v2/restricted/V2_EICU_ROLLING_COHORT_KEYS_V0_1.parquet');

CREATE OR REPLACE TEMP TABLE oxygen_interface AS
SELECT DISTINCT p.hospitalid AS hospital_id
FROM raw.main.nursecharting n JOIN raw.main.patient p USING(patientunitstayid)
WHERE n.NURSINGCHARTCELLTYPEVALLABEL='O2 Admin Device';

CREATE OR REPLACE TEMP TABLE urine_interface AS
SELECT DISTINCT p.hospitalid AS hospital_id
FROM raw.main.intakeoutput io JOIN raw.main.patient p USING(patientunitstayid)
WHERE lower(io.cellpath) LIKE '%i&o|output (ml)%'
  AND lower(io.celllabel) LIKE '%urine%';

CREATE OR REPLACE TEMP TABLE vaso_interface AS
SELECT DISTINCT p.hospitalid AS hospital_id
FROM raw.main.infusiondrug i JOIN raw.main.patient p USING(patientunitstayid);

CREATE OR REPLACE TEMP TABLE oxygen_events AS
WITH raw_events AS (
SELECT n.patientunitstayid AS stay_id,n.NURSINGCHARTOFFSET/60.0 AS offset_h,
  CASE
    WHEN regexp_matches(lower(trim(n.NURSINGCHARTVALUE)),'bipap|cpap|(^|[^a-z])niv([^a-z]|$)|vpap') THEN 4
    WHEN regexp_matches(lower(trim(n.NURSINGCHARTVALUE)),
      'hfnc|hhfnc|high.?flow|hi.?flow|hiflo|hi.?flo|optiflo|vapotherm|nchf|hf.?n/?c|hfo2|hhf') THEN 3
    WHEN regexp_matches(lower(trim(n.NURSINGCHARTVALUE)),'venturi|venti') THEN 2
    WHEN regexp_matches(lower(trim(n.NURSINGCHARTVALUE)),
      'ventilator|endotracheal|(^|[^a-z])(ett|vent|mv)([^a-z]|$)|(^|[^a-z])ac[0-9 /]') THEN -2
    WHEN regexp_matches(lower(trim(n.NURSINGCHARTVALUE)),
      'non.?rebreather|(^|[^a-z])nrb([^a-z]|$)|face.?mask|simple.?mask|oxy.?mask|(^|[^a-z])mask([^a-z]|$)|aerosol|misty|misti|(^|[^a-z])cam([^a-z]|$)') THEN 2
    WHEN regexp_matches(lower(trim(n.NURSINGCHARTVALUE)),
      'nasal.?can+nula|nasal.?canula|(^|[^a-z])nc([^a-z]|$)|n/c|oxym|oxim|[0-9]+.?l') THEN 1
    WHEN regexp_matches(lower(trim(n.NURSINGCHARTVALUE)),
      '^(ra|r/a|r\\.?a\\.?|room.?air|roomair|rm.?air|none|21|21%)$') THEN 0
  END AS support_level
FROM (SELECT DISTINCT icu_stay_key FROM v2_keys) s
JOIN raw.main.nursecharting n ON n.patientunitstayid=s.icu_stay_key
WHERE n.NURSINGCHARTCELLTYPEVALLABEL='O2 Admin Device'
  AND n.NURSINGCHARTOFFSET>720 AND n.NURSINGCHARTOFFSET<=4320
), collapsed AS (
  SELECT stay_id,offset_h,
         MAX(support_level) FILTER(WHERE support_level>=0) AS support_level,
         BOOL_OR(support_level=-2) AS imv_device_present
  FROM raw_events GROUP BY stay_id,offset_h
)
SELECT stay_id,offset_h,support_level FROM collapsed
WHERE support_level IS NOT NULL AND NOT imv_device_present;

CREATE OR REPLACE TEMP TABLE oxygen_features AS
WITH joined AS (
  SELECT k.stay_key,k.landmark_h,e.offset_h,e.support_level,
         LAG(e.support_level) OVER (
           PARTITION BY k.stay_key,k.landmark_h ORDER BY e.offset_h
         ) AS prior_level
  FROM v2_keys k JOIN oxygen_events e ON e.stay_id=k.icu_stay_key
   AND e.offset_h>k.landmark_h-12 AND e.offset_h<=k.landmark_h
  WHERE e.support_level>=0
)
SELECT stay_key,landmark_h,
       arg_max(support_level,offset_h) AS oxygen_latest_level,
       MAX(support_level) AS oxygen_max_level,
       COUNT_IF(prior_level IS NOT NULL AND support_level>prior_level) AS oxygen_escalation_count,
       COUNT(*) AS oxygen_documentation_count,
       MAX(offset_h) AS oxygen_latest_offset_h
FROM joined GROUP BY stay_key,landmark_h;

CREATE OR REPLACE TEMP TABLE urine_events AS
SELECT io.patientunitstayid AS stay_id,io.intakeoutputoffset/60.0 AS offset_h,
       CAST(io.cellvaluenumeric AS DOUBLE) AS urine_ml
FROM (SELECT DISTINCT icu_stay_key FROM v2_keys) s
JOIN raw.main.intakeoutput io ON io.patientunitstayid=s.icu_stay_key
WHERE io.intakeoutputoffset>720 AND io.intakeoutputoffset<=4320
  AND lower(io.cellpath) LIKE '%i&o|output (ml)%'
  AND regexp_matches(lower(trim(io.celllabel)),
    '^(urine|urine catheter|urine, void:|urine output-foley|sn urine output\\(ml\\)|urine output \\(ml\\)-urethral catheter|suprapubic urine output|or urine|urine output-urine output)$')
  AND NOT regexp_matches(lower(trim(io.celllabel)),'count|occurrence|incontinence|mixed')
  AND CAST(io.cellvaluenumeric AS DOUBLE) BETWEEN 0 AND 5000;

CREATE OR REPLACE TEMP TABLE urine_features AS
SELECT k.stay_key,k.landmark_h,
       SUM(e.urine_ml) FILTER(WHERE e.offset_h>k.landmark_h-6) AS urine_6h_ml,
       SUM(e.urine_ml) AS urine_12h_ml,
       COUNT(*) FILTER(WHERE e.offset_h>k.landmark_h-6) AS urine_6h_count,
       COUNT(*) AS urine_12h_count,MAX(e.offset_h) AS urine_latest_offset_h
FROM v2_keys k JOIN urine_events e ON e.stay_id=k.icu_stay_key
 AND e.offset_h>k.landmark_h-12 AND e.offset_h<=k.landmark_h
GROUP BY k.stay_key,k.landmark_h;

CREATE OR REPLACE TEMP TABLE vaso_events AS
SELECT i.patientunitstayid AS stay_id,i.infusionoffset/60.0 AS offset_h,
       CASE
         WHEN lower(i.drugname) LIKE '%norepinephrine%' OR lower(i.drugname) LIKE '%levophed%' THEN 'norepinephrine'
         WHEN lower(i.drugname) LIKE '%epinephrine%' THEN 'epinephrine'
         WHEN lower(i.drugname) LIKE '%phenylephrine%' THEN 'phenylephrine'
         WHEN lower(i.drugname) LIKE '%vasopressin%' THEN 'vasopressin'
         WHEN lower(i.drugname) LIKE '%dopamine%' THEN 'dopamine'
       END AS agent,
       CASE WHEN TRY_CAST(i.drugrate AS DOUBLE)>0 THEN 1 ELSE 0 END AS active
FROM (SELECT DISTINCT icu_stay_key FROM v2_keys) s
JOIN raw.main.infusiondrug i ON i.patientunitstayid=s.icu_stay_key
WHERE i.infusionoffset>720 AND i.infusionoffset<=4320
  AND regexp_matches(lower(COALESCE(i.drugname,'')),
    'norepinephrine|levophed|epinephrine|phenylephrine|vasopressin|dopamine');

CREATE OR REPLACE TEMP TABLE vaso_features AS
WITH joined AS (
  SELECT k.stay_key,k.landmark_h,e.offset_h,e.agent,e.active
  FROM v2_keys k JOIN vaso_events e ON e.stay_id=k.icu_stay_key
   AND e.offset_h>k.landmark_h-12 AND e.offset_h<=k.landmark_h
), by_agent AS (
  SELECT stay_key,landmark_h,agent,MAX(active) AS any_active,
         arg_max(active,offset_h) AS latest_active,MAX(offset_h) AS latest_offset_h
  FROM joined GROUP BY stay_key,landmark_h,agent
)
SELECT stay_key,landmark_h,MAX(any_active) AS vaso_any_12h,
       MAX(CASE WHEN landmark_h-latest_offset_h<=2 THEN latest_active ELSE 0 END) AS vaso_current,
       SUM(any_active) AS vaso_max_agent_count,
       MAX(latest_offset_h) AS vaso_latest_start_offset_h
FROM by_agent GROUP BY stay_key,landmark_h;

COPY (
  SELECT k.database,k.stay_key,k.landmark_h,
         o.oxygen_latest_level,o.oxygen_max_level,o.oxygen_escalation_count,
         o.oxygen_documentation_count,o.oxygen_latest_offset_h,
         CASE WHEN o.oxygen_latest_offset_h IS NOT NULL
              THEN k.landmark_h-o.oxygen_latest_offset_h END AS oxygen_time_since_latest_h,
         u.urine_6h_ml,u.urine_12h_ml,u.urine_6h_count,u.urine_12h_count,
         u.urine_latest_offset_h,
         CASE WHEN vi.hospital_id IS NOT NULL THEN COALESCE(v.vaso_any_12h,0) END AS vaso_any_12h,
         CASE WHEN vi.hospital_id IS NOT NULL THEN COALESCE(v.vaso_current,0) END AS vaso_current,
         CASE WHEN vi.hospital_id IS NOT NULL THEN COALESCE(v.vaso_max_agent_count,0) END AS vaso_max_agent_count,
         v.vaso_latest_start_offset_h,
         CAST(oi.hospital_id IS NOT NULL AS INTEGER) AS oxygen_interface_supported,
         CAST(ui.hospital_id IS NOT NULL AS INTEGER) AS urine_interface_supported,
         CAST(vi.hospital_id IS NOT NULL AS INTEGER) AS vaso_interface_supported
  FROM v2_keys k
  LEFT JOIN oxygen_features o USING(stay_key,landmark_h)
  LEFT JOIN urine_features u USING(stay_key,landmark_h)
  LEFT JOIN vaso_features v USING(stay_key,landmark_h)
  LEFT JOIN oxygen_interface oi USING(hospital_id)
  LEFT JOIN urine_interface ui USING(hospital_id)
  LEFT JOIN vaso_interface vi USING(hospital_id)
  ORDER BY k.stay_key,k.landmark_h
) TO 'study_b_v2/restricted/V2_EICU_TIER_B_FEATURES_V0_1.parquet'
  (FORMAT PARQUET,COMPRESSION ZSTD);
