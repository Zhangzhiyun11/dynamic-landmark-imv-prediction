-- Predictor-only latest arterial PaO2 in (t-24,t] for Liu-adapted models.

ATTACH '../database/mimiciv-3.1.duckdb' AS m (READ_ONLY);
ATTACH '../database/eicu-crd-2.0.duckdb' AS e (READ_ONLY);
SET preserve_insertion_order=false;

CREATE OR REPLACE TEMP TABLE mk AS
SELECT database,stay_id,CAST(stay_id AS VARCHAR) AS stay_key,landmark_h
FROM read_parquet('study_b_v2/restricted/V2_MIMIC_ROLLING_COHORT_KEYS_V0_1.parquet');
CREATE OR REPLACE TEMP TABLE ek AS
SELECT database,icu_stay_key,CAST(icu_stay_key AS VARCHAR) AS stay_key,landmark_h
FROM read_parquet('study_b_v2/restricted/V2_EICU_ROLLING_COHORT_KEYS_V0_1.parquet');

CREATE OR REPLACE TEMP TABLE mimic_pao2 AS
WITH stays AS (
  SELECT DISTINCT k.stay_id,i.hadm_id,i.intime FROM mk k JOIN m.mimiciv_icu.icustays i USING(stay_id)
), obs AS (
  SELECT s.stay_id,DATE_DIFF('second',s.intime,b.charttime)/3600.0 AS offset_h,b.po2 AS value
  FROM stays s JOIN m.mimiciv_derived.bg b USING(hadm_id)
  WHERE lower(b.specimen)='art.' AND b.po2 BETWEEN 20 AND 800
    AND b.charttime>s.intime AND b.charttime<=s.intime+INTERVAL '72 hours'
), mapped AS (
  SELECT k.stay_key,k.landmark_h,o.offset_h,o.value
  FROM obs o CROSS JOIN LATERAL generate_series(
    CAST(GREATEST(24,CEIL(o.offset_h/4.0)*4) AS BIGINT),
    CAST(LEAST(72,CEIL((o.offset_h+24)/4.0)*4-4) AS BIGINT),4
  ) gs(landmark_h)
  JOIN mk k ON k.stay_id=o.stay_id AND k.landmark_h=gs.landmark_h
  WHERE o.offset_h>gs.landmark_h-24 AND o.offset_h<=gs.landmark_h
)
SELECT stay_key,landmark_h,arg_max(value,offset_h) AS pao2_liu_24h_latest,
       MAX(offset_h) AS pao2_liu_24h_latest_offset_h
FROM mapped GROUP BY stay_key,landmark_h;

CREATE OR REPLACE TEMP TABLE eicu_pao2 AS
WITH obs AS (
  SELECT l.patientunitstayid AS stay_id,l.labresultoffset/60.0 AS offset_h,
         CAST(l.labresult AS DOUBLE) AS value
  FROM (SELECT DISTINCT icu_stay_key FROM ek) s
  JOIN e.main.lab l ON l.patientunitstayid=s.icu_stay_key
  WHERE lower(trim(l.labname))='pao2' AND l.labresult BETWEEN 20 AND 800
    AND l.labresultoffset>0 AND l.labresultoffset<=4320
), mapped AS (
  SELECT k.stay_key,k.landmark_h,o.offset_h,o.value
  FROM obs o CROSS JOIN LATERAL generate_series(
    CAST(GREATEST(24,CEIL(o.offset_h/4.0)*4) AS BIGINT),
    CAST(LEAST(72,CEIL((o.offset_h+24)/4.0)*4-4) AS BIGINT),4
  ) gs(landmark_h)
  JOIN ek k ON k.icu_stay_key=o.stay_id AND k.landmark_h=gs.landmark_h
  WHERE o.offset_h>gs.landmark_h-24 AND o.offset_h<=gs.landmark_h
)
SELECT stay_key,landmark_h,arg_max(value,offset_h) AS pao2_liu_24h_latest,
       MAX(offset_h) AS pao2_liu_24h_latest_offset_h
FROM mapped GROUP BY stay_key,landmark_h;

COPY (
  SELECT k.database,k.stay_key,k.landmark_h,p.pao2_liu_24h_latest,
         p.pao2_liu_24h_latest_offset_h
  FROM (SELECT database,stay_key,landmark_h FROM mk
        UNION ALL SELECT database,stay_key,landmark_h FROM ek) k
  LEFT JOIN (SELECT 'MIMIC-IV v3.1' AS database,* FROM mimic_pao2
             UNION ALL SELECT 'eICU-CRD v2.0',* FROM eicu_pao2) p
    USING(database,stay_key,landmark_h)
  ORDER BY k.database,k.stay_key,k.landmark_h
) TO 'study_b_v2/restricted/V2_LIU_PAO2_24H_V0_1.parquet'
  (FORMAT PARQUET,COMPRESSION ZSTD);

COPY (
  SELECT database,COUNT(*) AS landmark_rows,
         AVG((pao2_liu_24h_latest IS NOT NULL)::INTEGER) AS coverage
  FROM read_parquet('study_b_v2/restricted/V2_LIU_PAO2_24H_V0_1.parquet')
  GROUP BY database ORDER BY database
) TO 'study_b_v2/04_qc/V2_LIU_PAO2_24H_COVERAGE_V0_1.csv' (HEADER,DELIMITER ',');
