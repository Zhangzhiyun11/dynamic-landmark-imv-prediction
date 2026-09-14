-- Outcome-blind Tier B source-value audit. Aggregated outputs only.

ATTACH '../database/mimiciv-3.1.duckdb' AS m (READ_ONLY);
ATTACH '../database/eicu-crd-2.0.duckdb' AS e (READ_ONLY);

CREATE OR REPLACE TEMP TABLE mimic_stays AS
SELECT DISTINCT stay_id FROM read_parquet(
  'study_b_v2/restricted/V2_MIMIC_ROLLING_COHORT_KEYS_V0_1.parquet');

CREATE OR REPLACE TEMP TABLE eicu_stays AS
SELECT DISTINCT icu_stay_key FROM read_parquet(
  'study_b_v2/restricted/V2_EICU_ROLLING_COHORT_KEYS_V0_1.parquet');

COPY (
  WITH devices AS (
    SELECT lower(trim(device)) AS source_value
    FROM mimic_stays s
    JOIN m.mimiciv_derived.oxygen_delivery o USING(stay_id)
    CROSS JOIN LATERAL (VALUES
      (o.o2_delivery_device_1),(o.o2_delivery_device_2),
      (o.o2_delivery_device_3),(o.o2_delivery_device_4)
    ) d(device)
    WHERE device IS NOT NULL AND trim(device)<>''
  )
  SELECT source_value,COUNT(*) AS rows FROM devices
  GROUP BY source_value ORDER BY rows DESC,source_value
) TO 'study_b_v2/02_feasibility/V2_MIMIC_OXYGEN_DEVICE_CATALOG_V0_1.csv'
  (HEADER,DELIMITER ',');

COPY (
  SELECT n.NURSINGCHARTCELLTYPEVALLABEL AS source_label,
         n.NURSINGCHARTCELLTYPEVALNAME AS source_name,
         lower(trim(n.NURSINGCHARTVALUE)) AS source_value,
         COUNT(*) AS rows,COUNT(DISTINCT n.patientunitstayid) AS stays,
         COUNT(DISTINCT p.hospitalid) AS hospitals
  FROM eicu_stays s JOIN e.main.nursecharting n
    ON n.patientunitstayid=s.icu_stay_key
  JOIN e.main.patient p USING(patientunitstayid)
  WHERE n.NURSINGCHARTCELLTYPEVALLABEL IN ('O2 Admin Device','O2 L/%')
    AND n.NURSINGCHARTOFFSET>720 AND n.NURSINGCHARTOFFSET<=4320
    AND trim(COALESCE(n.NURSINGCHARTVALUE,''))<>''
  GROUP BY source_label,source_name,source_value
  ORDER BY source_label,rows DESC LIMIT 500
) TO 'study_b_v2/02_feasibility/V2_EICU_NURSE_OXYGEN_VALUE_CATALOG_V0_1.csv'
  (HEADER,DELIMITER ',');

COPY (
  SELECT r.RESPCHARTTYPECAT AS source_category,
         r.RESPCHARTVALUELABEL AS source_label,
         lower(trim(r.RESPCHARTVALUE)) AS source_value,
         COUNT(*) AS rows,COUNT(DISTINCT r.patientunitstayid) AS stays,
         COUNT(DISTINCT p.hospitalid) AS hospitals
  FROM eicu_stays s JOIN e.main.respiratorycharting r
    ON r.patientunitstayid=s.icu_stay_key
  JOIN e.main.patient p USING(patientunitstayid)
  WHERE r.RESPCHARTOFFSET>720 AND r.RESPCHARTOFFSET<=4320
    AND (lower(r.RESPCHARTVALUELABEL) LIKE '%o2%'
      OR lower(r.RESPCHARTVALUELABEL) LIKE '%fio2%'
      OR lower(r.RESPCHARTVALUELABEL) LIKE '%oxygen%'
      OR lower(r.RESPCHARTVALUELABEL) LIKE '%device%')
    AND trim(COALESCE(r.RESPCHARTVALUE,''))<>''
  GROUP BY source_category,source_label,source_value
  ORDER BY source_label,rows DESC LIMIT 1000
) TO 'study_b_v2/02_feasibility/V2_EICU_RESP_OXYGEN_VALUE_CATALOG_V0_1.csv'
  (HEADER,DELIMITER ',');

COPY (
  SELECT lower(trim(io.cellpath)) AS cellpath,
         lower(trim(io.celllabel)) AS celllabel,
         COUNT(*) AS rows,COUNT(DISTINCT io.patientunitstayid) AS stays,
         COUNT(DISTINCT p.hospitalid) AS hospitals,
         MIN(TRY_CAST(io.cellvaluenumeric AS DOUBLE)) AS min_value,
         QUANTILE_CONT(TRY_CAST(io.cellvaluenumeric AS DOUBLE),0.5) AS median_value,
         QUANTILE_CONT(TRY_CAST(io.cellvaluenumeric AS DOUBLE),0.99) AS p99_value,
         MAX(TRY_CAST(io.cellvaluenumeric AS DOUBLE)) AS max_value
  FROM eicu_stays s JOIN e.main.intakeoutput io
    ON io.patientunitstayid=s.icu_stay_key
  JOIN e.main.patient p USING(patientunitstayid)
  WHERE io.intakeoutputoffset>720 AND io.intakeoutputoffset<=4320
    AND lower(io.cellpath) LIKE '%output (ml)%'
    AND lower(io.celllabel) LIKE '%urine%'
  GROUP BY cellpath,celllabel ORDER BY rows DESC
) TO 'study_b_v2/02_feasibility/V2_EICU_URINE_SOURCE_CATALOG_V0_1.csv'
  (HEADER,DELIMITER ',');

COPY (
  SELECT lower(trim(i.drugname)) AS drugname,
         regexp_replace(lower(trim(COALESCE(i.drugrate,''))),'[-+0-9., ]','','g') AS rate_text_pattern,
         COUNT(*) AS rows,COUNT(DISTINCT i.patientunitstayid) AS stays,
         COUNT(DISTINCT p.hospitalid) AS hospitals,
         AVG(CAST(TRY_CAST(i.drugrate AS DOUBLE) IS NOT NULL AS INTEGER)) AS numeric_rate_prop
  FROM eicu_stays s JOIN e.main.infusiondrug i
    ON i.patientunitstayid=s.icu_stay_key
  JOIN e.main.patient p USING(patientunitstayid)
  WHERE i.infusionoffset>720 AND i.infusionoffset<=4320
    AND regexp_matches(lower(COALESCE(i.drugname,'')),
      'norepinephrine|levophed|epinephrine|phenylephrine|vasopressin|dopamine')
  GROUP BY drugname,rate_text_pattern ORDER BY rows DESC
) TO 'study_b_v2/02_feasibility/V2_EICU_VASOPRESSOR_SOURCE_CATALOG_V0_1.csv'
  (HEADER,DELIMITER ',');
