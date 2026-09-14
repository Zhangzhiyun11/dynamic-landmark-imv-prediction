-- Study B / V2 T1 outcome-blind feasibility audit.
-- This script reads predictor/source availability only. It does not read an
-- outcome table, create labels, train models, or calculate performance.

ATTACH '../database/mimiciv-3.1.duckdb' AS mimic_raw (READ_ONLY);
ATTACH '.restricted_data/mimic_t1/t1_mimic.duckdb' AS mimic_old (READ_ONLY);
ATTACH '../database/eicu-crd-2.0.duckdb' AS eicu_raw (READ_ONLY);

SET preserve_insertion_order = false;
SET threads = 4;

CREATE OR REPLACE TEMP TABLE v2_mimic_landmarks AS
SELECT
  c.subject_id,
  c.hadm_id,
  c.stay_id,
  c.icu_intime,
  p.anchor_year_group,
  gs.landmark_h
FROM mimic_old.mimic_t1.cohort c
JOIN mimic_raw.mimiciv_hosp.patients p USING (subject_id)
CROSS JOIN generate_series(24, 72, 4) AS gs(landmark_h)
WHERE c.analysis_eligible_flag = 1
  AND c.icu_los_hours > gs.landmark_h;

-- These are candidate coverage rows only. Later landmarks are not yet filtered
-- for incident IMV because doing so belongs to the separately governed outcome
-- phenotype stage.
CREATE OR REPLACE TEMP TABLE v2_mimic_hits AS
WITH vital AS (
  SELECT DISTINCT l.stay_id,l.landmark_h,x.domain
  FROM v2_mimic_landmarks l
  JOIN mimic_raw.mimiciv_derived.vitalsign v
    ON v.stay_id=l.stay_id
   AND v.charttime BETWEEN l.icu_intime + (l.landmark_h-12) * INTERVAL '1 hour'
                       AND l.icu_intime + l.landmark_h * INTERVAL '1 hour'
  CROSS JOIN LATERAL (VALUES
    ('rr',v.resp_rate),('spo2',v.spo2),('hr',v.heart_rate),
    ('sbp',COALESCE(v.sbp,v.sbp_ni)),('map',COALESCE(v.mbp,v.mbp_ni)),
    ('temperature',CAST(v.temperature AS DOUBLE))
  ) x(domain,value)
  WHERE x.value IS NOT NULL
), gcs AS (
  SELECT DISTINCT l.stay_id,l.landmark_h,'gcs' AS domain
  FROM v2_mimic_landmarks l
  JOIN mimic_raw.mimiciv_derived.gcs g
    ON g.stay_id=l.stay_id
   AND g.charttime BETWEEN l.icu_intime + (l.landmark_h-12) * INTERVAL '1 hour'
                       AND l.icu_intime + l.landmark_h * INTERVAL '1 hour'
  WHERE g.gcs IS NOT NULL
), oxygen AS (
  SELECT DISTINCT l.stay_id,l.landmark_h,'oxygen_support' AS domain
  FROM v2_mimic_landmarks l
  JOIN mimic_raw.mimiciv_derived.oxygen_delivery o
    ON o.stay_id=l.stay_id
   AND o.charttime BETWEEN l.icu_intime + (l.landmark_h-12) * INTERVAL '1 hour'
                       AND l.icu_intime + l.landmark_h * INTERVAL '1 hour'
  WHERE COALESCE(o.o2_delivery_device_1,o.o2_delivery_device_2,
                 o.o2_delivery_device_3,o.o2_delivery_device_4) IS NOT NULL
), oxygen_flow AS (
  SELECT DISTINCT l.stay_id,l.landmark_h,'oxygen_flow' AS domain
  FROM v2_mimic_landmarks l
  JOIN mimic_raw.mimiciv_derived.oxygen_delivery o
    ON o.stay_id=l.stay_id
   AND o.charttime BETWEEN l.icu_intime + (l.landmark_h-12) * INTERVAL '1 hour'
                       AND l.icu_intime + l.landmark_h * INTERVAL '1 hour'
  WHERE COALESCE(o.o2_flow,o.o2_flow_additional) IS NOT NULL
), abg AS (
  SELECT DISTINCT l.stay_id,l.landmark_h,x.domain
  FROM v2_mimic_landmarks l
  JOIN mimic_raw.mimiciv_derived.bg b
    ON b.subject_id=l.subject_id AND b.hadm_id=l.hadm_id
   AND b.charttime BETWEEN l.icu_intime + (l.landmark_h-12) * INTERVAL '1 hour'
                       AND l.icu_intime + l.landmark_h * INTERVAL '1 hour'
  CROSS JOIN LATERAL (VALUES
    ('abg_ph',b.ph),('abg_pao2',b.po2),('abg_paco2',b.pco2),
    ('lactate',b.lactate),('fio2',COALESCE(b.fio2,b.fio2_chartevents))
  ) x(domain,value)
  WHERE x.value IS NOT NULL
), urine AS (
  SELECT DISTINCT l.stay_id,l.landmark_h,'urine_output' AS domain
  FROM v2_mimic_landmarks l
  JOIN mimic_raw.mimiciv_derived.urine_output u
    ON u.stay_id=l.stay_id
   AND u.charttime BETWEEN l.icu_intime + (l.landmark_h-12) * INTERVAL '1 hour'
                       AND l.icu_intime + l.landmark_h * INTERVAL '1 hour'
  WHERE u.urineoutput IS NOT NULL
), vaso AS (
  SELECT DISTINCT l.stay_id,l.landmark_h,'vasopressor' AS domain
  FROM v2_mimic_landmarks l
  JOIN mimic_raw.mimiciv_derived.vasoactive_agent v
    ON v.stay_id=l.stay_id
   AND v.starttime <= l.icu_intime + l.landmark_h * INTERVAL '1 hour'
   AND v.endtime >= l.icu_intime + (l.landmark_h-12) * INTERVAL '1 hour'
  WHERE COALESCE(v.dopamine,v.epinephrine,v.norepinephrine,v.phenylephrine,
                 v.vasopressin,v.dobutamine,v.milrinone) IS NOT NULL
), oxygen_any AS (
  SELECT DISTINCT stay_id,landmark_h,'oxygen_any_documentation' AS domain
  FROM (
    SELECT stay_id,landmark_h FROM oxygen
    UNION ALL SELECT stay_id,landmark_h FROM oxygen_flow
    UNION ALL SELECT stay_id,landmark_h FROM abg WHERE domain='fio2'
  ) x
)
SELECT * FROM vital UNION ALL SELECT * FROM gcs UNION ALL SELECT * FROM oxygen
UNION ALL SELECT * FROM oxygen_flow UNION ALL SELECT * FROM abg
UNION ALL SELECT * FROM urine UNION ALL SELECT * FROM vaso
UNION ALL SELECT * FROM oxygen_any;

CREATE OR REPLACE TEMP TABLE v2_eicu_landmarks AS
SELECT
  c.patient_key,
  c.icu_stay_key,
  c.hospital_id,
  c.icu_type,
  c.hospitaldischargeyear,
  gs.landmark_h
FROM read_parquet('03_derived/restricted/eicu/EICU_COHORT.parquet') c
CROSS JOIN generate_series(24, 72, 4) AS gs(landmark_h)
WHERE c.analysis_eligible_flag = 1
  AND c.icu_discharge_offset_min > gs.landmark_h * 60;

CREATE OR REPLACE TEMP TABLE v2_eicu_hits AS
WITH vital_periodic AS (
  SELECT DISTINCT l.icu_stay_key,l.hospital_id,l.landmark_h,x.domain
  FROM v2_eicu_landmarks l
  JOIN eicu_raw.main.vitalperiodic v
    ON v.patientunitstayid=l.icu_stay_key
   AND v.observationoffset BETWEEN (l.landmark_h-12)*60 AND l.landmark_h*60
  CROSS JOIN LATERAL (VALUES
    ('rr',CAST(v.respiration AS DOUBLE)),('spo2',CAST(v.sao2 AS DOUBLE)),
    ('hr',CAST(v.heartrate AS DOUBLE)),('sbp',CAST(v.systemicsystolic AS DOUBLE)),
    ('map',CAST(v.systemicmean AS DOUBLE)),('temperature',CAST(v.temperature AS DOUBLE))
  ) x(domain,value)
  WHERE x.value IS NOT NULL
), vital_aperiodic AS (
  SELECT DISTINCT l.icu_stay_key,l.hospital_id,l.landmark_h,x.domain
  FROM v2_eicu_landmarks l
  JOIN eicu_raw.main.vitalaperiodic v
    ON v.patientunitstayid=l.icu_stay_key
   AND v.observationoffset BETWEEN (l.landmark_h-12)*60 AND l.landmark_h*60
  CROSS JOIN LATERAL (VALUES
    ('sbp',CAST(v.noninvasivesystolic AS DOUBLE)),
    ('map',CAST(v.noninvasivemean AS DOUBLE))
  ) x(domain,value)
  WHERE x.value IS NOT NULL
), gcs AS (
  SELECT DISTINCT l.icu_stay_key,l.hospital_id,l.landmark_h,'gcs' AS domain
  FROM v2_eicu_landmarks l
  JOIN eicu_raw.main.NURSECHARTING n ON n.patientunitstayid=l.icu_stay_key
   AND n.NURSINGCHARTOFFSET BETWEEN (l.landmark_h-12)*60 AND l.landmark_h*60
  WHERE n.NURSINGCHARTCELLTYPEVALLABEL='Glasgow coma score'
    AND n.NURSINGCHARTCELLTYPEVALNAME='GCS Total'
    AND TRY_CAST(n.NURSINGCHARTVALUE AS DOUBLE) BETWEEN 3 AND 15
), nurse_extra AS (
  SELECT DISTINCT l.icu_stay_key,l.hospital_id,l.landmark_h,x.domain
  FROM v2_eicu_landmarks l
  JOIN eicu_raw.main.NURSECHARTING n ON n.patientunitstayid=l.icu_stay_key
   AND n.NURSINGCHARTOFFSET BETWEEN (l.landmark_h-12)*60 AND l.landmark_h*60
  CROSS JOIN LATERAL (VALUES
    ('temperature',CASE WHEN n.NURSINGCHARTCELLTYPEVALLABEL='Temperature'
      AND n.NURSINGCHARTCELLTYPEVALNAME IN ('Temperature (C)','Temperature (F)')
      AND TRY_CAST(n.NURSINGCHARTVALUE AS DOUBLE) IS NOT NULL THEN 1 END),
    ('rass',CASE WHEN n.NURSINGCHARTCELLTYPEVALLABEL='RASS'
      AND TRY_CAST(n.NURSINGCHARTVALUE AS DOUBLE) BETWEEN -5 AND 4 THEN 1 END),
    ('oxygen_device',CASE WHEN n.NURSINGCHARTCELLTYPEVALLABEL='O2 Admin Device'
      AND NULLIF(trim(n.NURSINGCHARTVALUE),'') IS NOT NULL THEN 1 END),
    ('oxygen_flow_or_percent',CASE WHEN n.NURSINGCHARTCELLTYPEVALLABEL='O2 L/%'
      AND NULLIF(trim(n.NURSINGCHARTVALUE),'') IS NOT NULL THEN 1 END),
    ('spo2',CASE WHEN n.NURSINGCHARTCELLTYPEVALLABEL IN ('O2 Saturation','SpO2')
      AND TRY_CAST(n.NURSINGCHARTVALUE AS DOUBLE) IS NOT NULL THEN 1 END)
  ) x(domain,present)
  WHERE x.present=1
), resp AS (
  SELECT DISTINCT l.icu_stay_key,l.hospital_id,l.landmark_h,x.domain
  FROM v2_eicu_landmarks l
  JOIN eicu_raw.main.RESPIRATORYCHARTING r
    ON r.patientunitstayid=l.icu_stay_key
   AND r.RESPCHARTOFFSET BETWEEN (l.landmark_h-12)*60 AND l.landmark_h*60
  CROSS JOIN LATERAL (VALUES
    ('oxygen_device',CASE WHEN r.RESPCHARTVALUELABEL='O2 Device' THEN 1 END),
    ('oxygen_flow',CASE WHEN r.RESPCHARTVALUELABEL='LPM O2' THEN 1 END),
    ('fio2',CASE WHEN r.RESPCHARTVALUELABEL IN ('FiO2','FIO2 (%)') THEN 1 END)
  ) x(domain,present)
  WHERE x.present=1 AND NULLIF(trim(r.RESPCHARTVALUE),'') IS NOT NULL
), support AS (
  SELECT DISTINCT l.icu_stay_key,l.hospital_id,l.landmark_h,'oxygen_support' AS domain
  FROM v2_eicu_landmarks l
  JOIN eicu_raw.main.treatment t ON t.patientunitstayid=l.icu_stay_key
   AND t.treatmentoffset BETWEEN (l.landmark_h-12)*60 AND l.landmark_h*60
  WHERE regexp_matches(lower(t.treatmentstring),
    'non-invasive ventilation|high flow nasal cannula|nasal cannula|oxygen therapy')
), lab AS (
  SELECT DISTINCT l.icu_stay_key,l.hospital_id,l.landmark_h,x.domain
  FROM v2_eicu_landmarks l
  JOIN eicu_raw.main.lab b ON b.patientunitstayid=l.icu_stay_key
   AND b.labresultoffset BETWEEN (l.landmark_h-12)*60 AND l.landmark_h*60
  CROSS JOIN LATERAL (VALUES
    ('abg_ph',CASE WHEN lower(b.labname)='ph' THEN 1 END),
    ('abg_pao2',CASE WHEN lower(b.labname)='pao2' THEN 1 END),
    ('abg_paco2',CASE WHEN lower(b.labname)='paco2' THEN 1 END),
    ('lactate',CASE WHEN lower(b.labname)='lactate' THEN 1 END),
    ('routine_labs',CASE WHEN lower(b.labname) IN
      ('wbc x 1000','hgb','platelets x 1000','sodium','potassium','chloride',
       'bicarbonate','total co2','bun','creatinine','glucose') THEN 1 END)
  ) x(domain,present)
  WHERE x.present=1 AND b.labresult IS NOT NULL
), urine AS (
  SELECT DISTINCT l.icu_stay_key,l.hospital_id,l.landmark_h,'urine_fluid' AS domain
  FROM v2_eicu_landmarks l
  JOIN eicu_raw.main.intakeoutput i ON i.patientunitstayid=l.icu_stay_key
   AND i.intakeoutputoffset BETWEEN (l.landmark_h-12)*60 AND l.landmark_h*60
  WHERE COALESCE(i.outputtotal,i.nettotal,i.cellvaluenumeric) IS NOT NULL
), vaso AS (
  SELECT DISTINCT l.icu_stay_key,l.hospital_id,l.landmark_h,'vasopressor' AS domain
  FROM v2_eicu_landmarks l
  JOIN eicu_raw.main.infusiondrug i ON i.patientunitstayid=l.icu_stay_key
   AND i.infusionoffset BETWEEN (l.landmark_h-12)*60 AND l.landmark_h*60
  WHERE regexp_matches(lower(COALESCE(i.drugname,'')),
    'norepinephrine|levophed|epinephrine|phenylephrine|vasopressin|dopamine')
), oxygen_any AS (
  SELECT DISTINCT icu_stay_key,hospital_id,landmark_h,
         'oxygen_any_documentation' AS domain
  FROM (
    SELECT icu_stay_key,hospital_id,landmark_h FROM nurse_extra
      WHERE domain IN ('oxygen_device','oxygen_flow_or_percent')
    UNION ALL SELECT icu_stay_key,hospital_id,landmark_h FROM resp
      WHERE domain IN ('oxygen_device','oxygen_flow','fio2')
    UNION ALL SELECT icu_stay_key,hospital_id,landmark_h FROM support
  ) x
)
SELECT * FROM vital_periodic UNION ALL SELECT * FROM vital_aperiodic
UNION ALL SELECT * FROM gcs UNION ALL SELECT * FROM nurse_extra
UNION ALL SELECT * FROM resp
UNION ALL SELECT * FROM support UNION ALL SELECT * FROM lab
UNION ALL SELECT * FROM urine UNION ALL SELECT * FROM vaso
UNION ALL SELECT * FROM oxygen_any;

COPY (
  WITH denominators AS (
    SELECT landmark_h,COUNT(*) AS candidate_landmarks
    FROM v2_mimic_landmarks GROUP BY landmark_h
  ), domains AS (
    SELECT DISTINCT domain FROM v2_mimic_hits
  ), numerators AS (
    SELECT landmark_h,domain,COUNT(DISTINCT stay_id) AS landmarks_with_domain
    FROM v2_mimic_hits GROUP BY landmark_h,domain
  )
  SELECT 'MIMIC-IV' AS database,d.landmark_h,x.domain,
         d.candidate_landmarks,COALESCE(n.landmarks_with_domain,0) AS landmarks_with_domain,
         COALESCE(n.landmarks_with_domain,0)::DOUBLE/d.candidate_landmarks AS coverage
  FROM denominators d CROSS JOIN domains x
  LEFT JOIN numerators n USING(landmark_h,domain)
  ORDER BY landmark_h,domain
) TO 'study_b_v2/02_feasibility/V2_MIMIC_DOMAIN_COVERAGE_V0_1.csv'
  (HEADER,DELIMITER ',');

COPY (
  WITH denominators AS (
    SELECT landmark_h,COUNT(*) AS candidate_landmarks
    FROM v2_eicu_landmarks GROUP BY landmark_h
  ), domains AS (
    SELECT DISTINCT domain FROM v2_eicu_hits
  ), numerators AS (
    SELECT landmark_h,domain,COUNT(DISTINCT icu_stay_key) AS landmarks_with_domain
    FROM v2_eicu_hits GROUP BY landmark_h,domain
  )
  SELECT 'eICU' AS database,d.landmark_h,x.domain,
         d.candidate_landmarks,COALESCE(n.landmarks_with_domain,0) AS landmarks_with_domain,
         COALESCE(n.landmarks_with_domain,0)::DOUBLE/d.candidate_landmarks AS coverage
  FROM denominators d CROSS JOIN domains x
  LEFT JOIN numerators n USING(landmark_h,domain)
  ORDER BY landmark_h,domain
) TO 'study_b_v2/02_feasibility/V2_EICU_DOMAIN_COVERAGE_V0_1.csv'
  (HEADER,DELIMITER ',');

COPY (
  WITH den AS (
    SELECT hospital_id,landmark_h,COUNT(*) AS n
    FROM v2_eicu_landmarks GROUP BY hospital_id,landmark_h
  ), domains AS (SELECT DISTINCT domain FROM v2_eicu_hits),
  num AS (
    SELECT hospital_id,landmark_h,domain,COUNT(DISTINCT icu_stay_key) AS nhit
    FROM v2_eicu_hits GROUP BY hospital_id,landmark_h,domain
  ), hc AS (
    SELECT d.hospital_id,d.landmark_h,x.domain,d.n,
           COALESCE(num.nhit,0)::DOUBLE/d.n AS coverage
    FROM den d CROSS JOIN domains x
    LEFT JOIN num USING(hospital_id,landmark_h,domain)
  )
  SELECT landmark_h,domain,COUNT(*) AS hospitals,
         MEDIAN(coverage) AS median_hospital_coverage,
         QUANTILE_CONT(coverage,0.25) AS q1_hospital_coverage,
         QUANTILE_CONT(coverage,0.75) AS q3_hospital_coverage,
         SUM(coverage>=0.50) AS hospitals_coverage_ge50pct,
         SUM(coverage=0) AS hospitals_zero_coverage
  FROM hc GROUP BY landmark_h,domain ORDER BY landmark_h,domain
) TO 'study_b_v2/02_feasibility/V2_EICU_HOSPITAL_COVERAGE_V0_1.csv'
  (HEADER,DELIMITER ',');

COPY (
  SELECT anchor_year_group,COUNT(DISTINCT subject_id) AS patients,
         COUNT(DISTINCT stay_id) AS stays,COUNT(*) AS candidate_landmarks
  FROM v2_mimic_landmarks GROUP BY anchor_year_group ORDER BY anchor_year_group
) TO 'study_b_v2/02_feasibility/V2_MIMIC_TEMPORAL_GROUP_FEASIBILITY_V0_1.csv'
  (HEADER,DELIMITER ',');

COPY (
  WITH source_rows AS (
    SELECT 'nurse_temperature' AS source_group,n.patientunitstayid,p.hospitalid
    FROM eicu_raw.main.NURSECHARTING n JOIN eicu_raw.main.patient p USING(patientunitstayid)
    WHERE n.NURSINGCHARTCELLTYPEVALLABEL='Temperature'
      AND n.NURSINGCHARTCELLTYPEVALNAME IN ('Temperature (C)','Temperature (F)')
    UNION ALL
    SELECT 'nurse_gcs_total',n.patientunitstayid,p.hospitalid
    FROM eicu_raw.main.NURSECHARTING n JOIN eicu_raw.main.patient p USING(patientunitstayid)
    WHERE n.NURSINGCHARTCELLTYPEVALLABEL='Glasgow coma score'
      AND n.NURSINGCHARTCELLTYPEVALNAME='GCS Total'
    UNION ALL
    SELECT 'nurse_rass',n.patientunitstayid,p.hospitalid
    FROM eicu_raw.main.NURSECHARTING n JOIN eicu_raw.main.patient p USING(patientunitstayid)
    WHERE n.NURSINGCHARTCELLTYPEVALLABEL='RASS'
    UNION ALL
    SELECT 'nurse_o2_l_or_percent',n.patientunitstayid,p.hospitalid
    FROM eicu_raw.main.NURSECHARTING n JOIN eicu_raw.main.patient p USING(patientunitstayid)
    WHERE n.NURSINGCHARTCELLTYPEVALLABEL='O2 L/%'
    UNION ALL
    SELECT 'nurse_o2_admin_device',n.patientunitstayid,p.hospitalid
    FROM eicu_raw.main.NURSECHARTING n JOIN eicu_raw.main.patient p USING(patientunitstayid)
    WHERE n.NURSINGCHARTCELLTYPEVALLABEL='O2 Admin Device'
    UNION ALL
    SELECT 'respchart_fio2',r.patientunitstayid,p.hospitalid
    FROM eicu_raw.main.RESPIRATORYCHARTING r JOIN eicu_raw.main.patient p USING(patientunitstayid)
    WHERE r.RESPCHARTVALUELABEL IN ('FiO2','FIO2 (%)')
    UNION ALL
    SELECT 'respchart_lpm_o2',r.patientunitstayid,p.hospitalid
    FROM eicu_raw.main.RESPIRATORYCHARTING r JOIN eicu_raw.main.patient p USING(patientunitstayid)
    WHERE r.RESPCHARTVALUELABEL='LPM O2'
    UNION ALL
    SELECT 'respchart_o2_device',r.patientunitstayid,p.hospitalid
    FROM eicu_raw.main.RESPIRATORYCHARTING r JOIN eicu_raw.main.patient p USING(patientunitstayid)
    WHERE r.RESPCHARTVALUELABEL='O2 Device'
    UNION ALL
    SELECT 'infusion_vasopressor',i.patientunitstayid,p.hospitalid
    FROM eicu_raw.main.infusiondrug i JOIN eicu_raw.main.patient p USING(patientunitstayid)
    WHERE regexp_matches(lower(COALESCE(i.drugname,'')),
      'norepinephrine|levophed|epinephrine|phenylephrine|vasopressin|dopamine')
    UNION ALL
    SELECT 'intakeoutput_any',i.patientunitstayid,p.hospitalid
    FROM eicu_raw.main.intakeoutput i JOIN eicu_raw.main.patient p USING(patientunitstayid)
  )
  SELECT source_group,COUNT(*) AS source_rows,
         COUNT(DISTINCT patientunitstayid) AS source_stays,
         COUNT(DISTINCT hospitalid) AS source_hospitals
  FROM source_rows GROUP BY source_group ORDER BY source_group
) TO 'study_b_v2/02_feasibility/V2_EICU_SOURCE_INTERFACE_CATALOG_V0_1.csv'
  (HEADER,DELIMITER ',');
