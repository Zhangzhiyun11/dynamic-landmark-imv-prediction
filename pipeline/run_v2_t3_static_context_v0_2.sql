ATTACH '.restricted_data/mimic_t1/t1_mimic.duckdb' AS mimic_old (READ_ONLY);

CREATE OR REPLACE TEMP TABLE v2_static_context AS
SELECT k.database,CAST(k.subject_id AS VARCHAR) AS patient_key,
       CAST(k.stay_id AS VARCHAR) AS stay_key,NULL::INTEGER AS hospital_id,
       k.anchor_year_group,NULL::SMALLINT AS hospitaldischargeyear,k.landmark_h,
       s.age,s.sex,s.admission_source_v2 AS admission_source,
       s.icu_type_candidate AS icu_type,s.preicu_los_hours,hw.bmi_candidate AS bmi
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
) TO 'study_b_v2/restricted/V2_STATIC_CONTEXT_V0_2.parquet'
  (FORMAT PARQUET,COMPRESSION ZSTD);

COPY (
  SELECT database,COUNT(*) AS landmark_rows,
         AVG(CAST(age IS NOT NULL AS INTEGER)) AS age_coverage,
         AVG(CAST(preicu_los_hours IS NOT NULL AS INTEGER)) AS preicu_los_coverage,
         AVG(CAST(bmi IS NOT NULL AS INTEGER)) AS bmi_coverage
  FROM v2_static_context GROUP BY database ORDER BY database
) TO 'study_b_v2/04_qc/V2_STATIC_CONTEXT_COVERAGE_V0_2.csv'
  (HEADER,DELIMITER ',');
