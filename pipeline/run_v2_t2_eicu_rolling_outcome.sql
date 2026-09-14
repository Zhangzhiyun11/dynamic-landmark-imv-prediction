-- V2 T2 eICU rolling cohort/Route C outcome. No predictors are exported.

ATTACH '../database/eicu-crd-2.0.duckdb' AS raw (READ_ONLY);
SET preserve_insertion_order=false;
SET threads=4;

CREATE OR REPLACE TEMP VIEW v2_treatment_status AS
SELECT DISTINCT patientunitstayid AS icu_stay_key,treatmentoffset AS event_offset_min,
       'treatment_status' AS source_domain,treatmentstring AS source_label
FROM raw.main.treatment
WHERE lower(treatmentstring) LIKE '%mechanical ventilation%'
  AND lower(treatmentstring) NOT LIKE '%non-invasive%';

CREATE OR REPLACE TEMP VIEW v2_airway_status AS
SELECT DISTINCT patientunitstayid AS icu_stay_key,RESPCARESTATUSOFFSET AS event_offset_min,
       'respiratorycare_airway' AS source_domain,AIRWAYTYPE AS source_label
FROM raw.main.RESPIRATORYCARE
WHERE AIRWAYTYPE IN ('Oral ETT','Nasal ETT','Double-Lumen Tube','Cricothyrotomy')
UNION
SELECT DISTINCT patientunitstayid,RESPCHARTOFFSET,'respiratorycharting_airway',
       RESPCHARTVALUELABEL || '=' || COALESCE(RESPCHARTVALUE,'')
FROM raw.main.RESPIRATORYCHARTING
WHERE RESPCHARTVALUELABEL IN ('Endotracheal Tube Placement Checked','Secured at-ETT')
  AND COALESCE(trim(RESPCHARTVALUE),'')<>'';

CREATE OR REPLACE TEMP VIEW v2_vent_status AS
SELECT DISTINCT patientunitstayid AS icu_stay_key,RESPCHARTOFFSET AS event_offset_min,
       'respiratorycharting_vent' AS source_domain,
       RESPCHARTVALUELABEL || '=' || COALESCE(RESPCHARTVALUE,'') AS source_label
FROM raw.main.RESPIRATORYCHARTING
WHERE (RESPCHARTVALUELABEL='RT Vent On/Off'
       AND lower(RESPCHARTVALUE) IN ('start','continued'))
   OR (RESPCHARTVALUELABEL='Mechanical Ventilator Mode'
       AND lower(RESPCHARTVALUE) IN ('ac/cmv','simv','pcv w/assist','simv+','aprv'));

CREATE OR REPLACE TEMP VIEW v2_start_anchors AS
SELECT DISTINCT patientunitstayid AS icu_stay_key,RESPCHARTOFFSET AS start_offset_min,
       'respiratorycharting_start' AS anchor_domain,'RT Vent On/Off=Start' AS anchor_label
FROM raw.main.RESPIRATORYCHARTING
WHERE RESPCHARTVALUELABEL='RT Vent On/Off' AND lower(RESPCHARTVALUE)='start'
UNION
SELECT DISTINCT patientunitstayid,treatmentoffset,'treatment_airway_start',treatmentstring
FROM raw.main.treatment
WHERE lower(treatmentstring) LIKE '%endotracheal tube|insertion%'
   OR lower(treatmentstring) LIKE '%|reintubation'
UNION
SELECT DISTINCT r.patientunitstayid,r.VENTSTARTOFFSET,'respiratorycare_start',
       'VENTSTARTOFFSET|' || r.AIRWAYTYPE
FROM raw.main.RESPIRATORYCARE r
JOIN raw.main.patient p USING(patientunitstayid)
WHERE r.VENTSTARTOFFSET IS NOT NULL AND r.VENTSTARTOFFSET<>0
  AND r.VENTSTARTOFFSET<=p.unitdischargeoffset
  AND r.AIRWAYTYPE IN ('Oral ETT','Nasal ETT','Double-Lumen Tube','Cricothyrotomy');

CREATE OR REPLACE TEMP VIEW v2_support_events AS
SELECT * FROM v2_treatment_status
UNION ALL SELECT * FROM v2_airway_status
UNION ALL SELECT * FROM v2_vent_status;

CREATE OR REPLACE TEMP VIEW v2_strict_candidates AS
SELECT a.icu_stay_key,a.start_offset_min,a.anchor_domain,a.anchor_label,
       COUNT(DISTINCT s.source_domain) AS independent_support_domains
FROM v2_start_anchors a JOIN v2_support_events s
  ON s.icu_stay_key=a.icu_stay_key
 AND abs(s.event_offset_min-a.start_offset_min)<=360
 AND ((a.anchor_domain='respiratorycharting_start' AND s.source_domain<>'respiratorycharting_vent')
   OR (a.anchor_domain='treatment_airway_start' AND s.source_domain<>'treatment_status')
   OR (a.anchor_domain='respiratorycare_start' AND s.source_domain<>'respiratorycare_airway'))
GROUP BY 1,2,3,4
UNION ALL
SELECT DISTINCT t.icu_stay_key,greatest(t.event_offset_min,a.event_offset_min),
       'treatment_status+airway',t.source_label || ' || ' || a.source_label,1
FROM v2_treatment_status t JOIN v2_airway_status a
  ON a.icu_stay_key=t.icu_stay_key AND abs(a.event_offset_min-t.event_offset_min)<=360
UNION ALL
SELECT DISTINCT t.icu_stay_key,greatest(t.event_offset_min,v.event_offset_min),
       'treatment_status+vent_status',t.source_label || ' || ' || v.source_label,1
FROM v2_treatment_status t JOIN v2_vent_status v
  ON v.icu_stay_key=t.icu_stay_key AND abs(v.event_offset_min-t.event_offset_min)<=360
UNION ALL
SELECT DISTINCT v.icu_stay_key,greatest(v.event_offset_min,a.event_offset_min),
       'vent_status+airway',v.source_label || ' || ' || a.source_label,1
FROM v2_vent_status v JOIN v2_airway_status a
  ON a.icu_stay_key=v.icu_stay_key AND abs(a.event_offset_min-v.event_offset_min)<=360;

CREATE OR REPLACE TEMP VIEW v2_strict_starts AS
SELECT * FROM v2_strict_candidates WHERE independent_support_domains>=1;

CREATE OR REPLACE TEMP VIEW v2_sensitive_imv_evidence AS
SELECT icu_stay_key,event_offset_min FROM v2_support_events
UNION
SELECT icu_stay_key,start_offset_min FROM v2_start_anchors;

CREATE OR REPLACE TEMP TABLE v2_eicu_candidate_landmarks AS
SELECT c.*,gs.landmark_h,gs.landmark_h*60 AS landmark_offset_min
FROM read_parquet('03_derived/restricted/eicu/EICU_COHORT.parquet') c
CROSS JOIN generate_series(24,72,4) gs(landmark_h)
WHERE c.analysis_eligible_flag=1
  AND c.icu_discharge_offset_min>gs.landmark_h*60
  AND (c.death_offset_h IS NULL OR c.death_offset_h>gs.landmark_h);

CREATE OR REPLACE TEMP TABLE v2_eicu_landmarks AS
SELECT l.*
FROM v2_eicu_candidate_landmarks l
WHERE NOT EXISTS (
  SELECT 1 FROM v2_sensitive_imv_evidence e
  WHERE e.icu_stay_key=l.icu_stay_key AND e.event_offset_min<=l.landmark_offset_min
)
AND NOT EXISTS (
  SELECT 1 FROM raw.main.RESPIRATORYCARE r
  WHERE r.patientunitstayid=l.icu_stay_key
    AND r.RESPCARESTATUSOFFSET<=l.landmark_offset_min
    AND r.AIRWAYTYPE='Tracheostomy'
)
AND (
  EXISTS (SELECT 1 FROM raw.main.vitalperiodic v
    WHERE v.patientunitstayid=l.icu_stay_key
      AND v.observationoffset BETWEEN l.landmark_offset_min-720 AND l.landmark_offset_min
      AND COALESCE(v.respiration,v.sao2,v.heartrate,v.systemicsystolic,v.systemicmean,v.temperature) IS NOT NULL)
  OR EXISTS (SELECT 1 FROM raw.main.vitalaperiodic v
    WHERE v.patientunitstayid=l.icu_stay_key
      AND v.observationoffset BETWEEN l.landmark_offset_min-720 AND l.landmark_offset_min
      AND COALESCE(v.noninvasivesystolic,v.noninvasivemean) IS NOT NULL)
  OR EXISTS (SELECT 1 FROM raw.main.NURSECHARTING n
    WHERE n.patientunitstayid=l.icu_stay_key
      AND n.NURSINGCHARTOFFSET BETWEEN l.landmark_offset_min-720 AND l.landmark_offset_min
      AND TRY_CAST(n.NURSINGCHARTVALUE AS DOUBLE) IS NOT NULL
      AND n.NURSINGCHARTCELLTYPEVALLABEL IN
        ('Respiratory Rate','Heart Rate','SpO2','O2 Saturation','MAP (mmHg)',
         'Arterial Line MAP (mmHg)','Temperature','Glasgow coma score'))
);

CREATE OR REPLACE TEMP TABLE v2_eicu_windows AS
SELECT l.*,w.horizon_h,l.landmark_offset_min+w.horizon_h*60 AS horizon_end_offset_min
FROM v2_eicu_landmarks l CROSS JOIN (VALUES (12),(24)) w(horizon_h)
UNION ALL
SELECT l.*,72 AS horizon_h,l.landmark_offset_min+72*60 AS horizon_end_offset_min
FROM v2_eicu_landmarks l WHERE l.landmark_h=24;

CREATE OR REPLACE TEMP TABLE v2_eicu_window_evidence AS
SELECT
  w.patient_key,w.hospital_encounter_key,w.icu_stay_key,w.hospital_id,w.icu_type,
  w.hospitaldischargeyear,w.landmark_h,w.landmark_offset_min,w.horizon_h,
  w.horizon_end_offset_min,w.death_offset_h,w.icu_discharge_offset_h,
  (SELECT MIN(s.start_offset_min) FROM v2_strict_starts s
    WHERE s.icu_stay_key=w.icu_stay_key
      AND s.start_offset_min>w.landmark_offset_min
      AND s.start_offset_min<=w.horizon_end_offset_min) AS first_strict_offset_min,
  (SELECT MIN(a.start_offset_min) FROM v2_start_anchors a
    WHERE a.icu_stay_key=w.icu_stay_key
      AND a.start_offset_min>w.landmark_offset_min
      AND a.start_offset_min<=w.horizon_end_offset_min) AS first_anchor_offset_min,
  (SELECT COUNT(DISTINCT (t.event_offset_min,lower(trim(t.source_label))))
    FROM v2_treatment_status t
    WHERE t.icu_stay_key=w.icu_stay_key
      AND t.event_offset_min>w.landmark_offset_min
      AND t.event_offset_min<=w.horizon_end_offset_min) AS nonduplicate_treatment_count,
  (SELECT COUNT(DISTINCT t.event_offset_min) FROM v2_treatment_status t
    WHERE t.icu_stay_key=w.icu_stay_key
      AND t.event_offset_min>w.landmark_offset_min
      AND t.event_offset_min<=w.horizon_end_offset_min) AS distinct_treatment_time_count,
  (SELECT MIN(t.event_offset_min) FROM v2_treatment_status t
    WHERE t.icu_stay_key=w.icu_stay_key
      AND t.event_offset_min>w.landmark_offset_min
      AND t.event_offset_min<=w.horizon_end_offset_min) AS first_treatment_offset_min,
  (SELECT COUNT(*) FROM v2_support_events s
    WHERE s.icu_stay_key=w.icu_stay_key
      AND s.event_offset_min>w.landmark_offset_min
      AND s.event_offset_min<=w.horizon_end_offset_min) AS support_record_count,
  (SELECT MIN(s.event_offset_min) FROM v2_support_events s
    WHERE s.icu_stay_key=w.icu_stay_key
      AND s.event_offset_min>w.landmark_offset_min
      AND s.event_offset_min<=w.horizon_end_offset_min) AS first_support_offset_min
FROM v2_eicu_windows w;

CREATE OR REPLACE TEMP TABLE v2_eicu_rolling_outcome AS
SELECT
  'eICU-CRD v2.0' AS database,
  patient_key,hospital_encounter_key,icu_stay_key,hospital_id,icu_type,
  hospitaldischargeyear,landmark_h,horizon_h,'EICU_ROUTE_C_ROLLING' AS phenotype,
  CASE WHEN first_strict_offset_min IS NOT NULL THEN 'A'
       WHEN first_anchor_offset_min IS NOT NULL THEN 'B'
       WHEN nonduplicate_treatment_count>=2 THEN 'C'
       WHEN support_record_count>=1 THEN 'Unknown'
       ELSE 'None' END AS evidence_grade,
  CASE WHEN first_strict_offset_min IS NOT NULL OR first_anchor_offset_min IS NOT NULL
             OR nonduplicate_treatment_count>=2 THEN 1
       WHEN support_record_count>=1 THEN 9 ELSE 0 END AS outcome_imv,
  CASE WHEN first_strict_offset_min IS NOT NULL OR first_anchor_offset_min IS NOT NULL
             OR distinct_treatment_time_count>=2 THEN 1
       WHEN support_record_count>=1 THEN 9 ELSE 0 END AS outcome_imv_distinct_time,
  first_strict_offset_min/60.0 AS first_strict_offset_h,
  first_anchor_offset_min/60.0 AS first_anchor_offset_h,
  first_treatment_offset_min/60.0 AS first_treatment_offset_h,
  nonduplicate_treatment_count,distinct_treatment_time_count,support_record_count,
  CASE
    WHEN death_offset_h>landmark_h AND death_offset_h<=horizon_end_offset_min/60.0
     AND icu_discharge_offset_h>landmark_h AND icu_discharge_offset_h<=horizon_end_offset_min/60.0
     AND abs(death_offset_h-icu_discharge_offset_h)<1.0/60.0
     AND (first_strict_offset_min IS NULL OR death_offset_h*60<first_strict_offset_min)
      THEN 'both_same_time'
    WHEN death_offset_h>landmark_h AND death_offset_h<=horizon_end_offset_min/60.0
     AND (first_strict_offset_min IS NULL OR death_offset_h*60<first_strict_offset_min)
      THEN 'death'
    WHEN icu_discharge_offset_h>landmark_h AND icu_discharge_offset_h<=horizon_end_offset_min/60.0
     AND (first_strict_offset_min IS NULL OR icu_discharge_offset_h*60<first_strict_offset_min)
      THEN 'ICU discharge'
    ELSE 'none' END AS competing_event_type,
  death_offset_h,icu_discharge_offset_h
FROM v2_eicu_window_evidence;

COPY (
  SELECT * FROM v2_eicu_rolling_outcome
  ORDER BY hospital_id,patient_key,icu_stay_key,landmark_h,horizon_h
) TO 'study_b_v2/restricted/V2_EICU_ROLLING_OUTCOME_V0_1.parquet'
  (FORMAT PARQUET,COMPRESSION ZSTD);

COPY (
  SELECT 'eICU-CRD v2.0' AS database,patient_key,hospital_encounter_key,
         icu_stay_key,hospital_id,icu_type,hospitaldischargeyear,landmark_h
  FROM v2_eicu_landmarks
  ORDER BY patient_key,icu_stay_key,landmark_h
) TO 'study_b_v2/restricted/V2_EICU_ROLLING_COHORT_KEYS_V0_1.parquet'
  (FORMAT PARQUET,COMPRESSION ZSTD);

COPY (
  SELECT landmark_h,horizon_h,evidence_grade,COUNT(*) AS landmark_rows
  FROM v2_eicu_rolling_outcome
  GROUP BY landmark_h,horizon_h,evidence_grade
  ORDER BY landmark_h,horizon_h,evidence_grade
) TO 'study_b_v2/04_qc/V2_EICU_ROLLING_OUTCOME_COUNTS_V0_1.csv'
  (HEADER,DELIMITER ',');
