-- Aggregate-only assertions for V2 T1 feasibility outputs.

COPY (
  WITH m AS (
    SELECT * FROM read_csv_auto('study_b_v2/02_feasibility/V2_MIMIC_DOMAIN_COVERAGE_V0_1.csv')
  ), e AS (
    SELECT * FROM read_csv_auto('study_b_v2/02_feasibility/V2_EICU_DOMAIN_COVERAGE_V0_1.csv')
  ), h AS (
    SELECT * FROM read_csv_auto('study_b_v2/02_feasibility/V2_EICU_HOSPITAL_COVERAGE_V0_1.csv')
  ), t AS (
    SELECT * FROM read_csv_auto('study_b_v2/02_feasibility/V2_MIMIC_TEMPORAL_GROUP_FEASIBILITY_V0_1.csv')
  ), s AS (
    SELECT * FROM read_csv_auto('study_b_v2/02_feasibility/V2_EICU_SOURCE_INTERFACE_CATALOG_V0_1.csv')
  )
  SELECT * FROM (
    SELECT 'mimic_coverage_bounded' AS assertion,
      (SELECT COALESCE(bool_and(coverage BETWEEN 0 AND 1),FALSE) FROM m) AS passed,
      'all coverage values in [0,1]' AS detail
    UNION ALL SELECT 'eicu_coverage_bounded',
      (SELECT COALESCE(bool_and(coverage BETWEEN 0 AND 1),FALSE) FROM e),
      'all coverage values in [0,1]'
    UNION ALL SELECT 'mimic_13_landmarks',
      (SELECT COUNT(DISTINCT landmark_h)=13 FROM m),
      '24 to 72 hours every 4 hours'
    UNION ALL SELECT 'eicu_13_landmarks',
      (SELECT COUNT(DISTINCT landmark_h)=13 FROM e),
      '24 to 72 hours every 4 hours'
    UNION ALL SELECT 'mimic_unique_rows',
      (SELECT COUNT(*)=COUNT(DISTINCT (landmark_h,domain)) FROM m),
      'one aggregate row per landmark and domain'
    UNION ALL SELECT 'eicu_unique_rows',
      (SELECT COUNT(*)=COUNT(DISTINCT (landmark_h,domain)) FROM e),
      'one aggregate row per landmark and domain'
    UNION ALL SELECT 'eicu_core_vitals_min85pct',
      (SELECT MIN(coverage)>=0.85 FROM e
       WHERE domain IN ('rr','spo2','hr','sbp','map','temperature')),
      'outcome-blind Tier A feasibility threshold'
    UNION ALL SELECT 'mimic_core_vitals_min95pct',
      (SELECT MIN(coverage)>=0.95 FROM m
       WHERE domain IN ('rr','spo2','hr','sbp','map','temperature')),
      'outcome-blind Tier A feasibility threshold'
    UNION ALL SELECT 'oxygen_union_both_databases_min50pct',
      (SELECT LEAST(
        (SELECT MIN(coverage) FROM m WHERE domain='oxygen_any_documentation'),
        (SELECT MIN(coverage) FROM e WHERE domain='oxygen_any_documentation'))>=0.50),
      'semantic parsing still required before Tier B inclusion'
    UNION ALL SELECT 'mimic_five_anchor_year_groups',
      (SELECT COUNT(*)=5 FROM t),
      'deidentified temporal groups available'
    UNION ALL SELECT 'eicu_hospital_counts_valid',
      (SELECT COALESCE(bool_and(hospitals BETWEEN 1 AND 208
        AND hospitals_coverage_ge50pct BETWEEN 0 AND hospitals
        AND hospitals_zero_coverage BETWEEN 0 AND hospitals),FALSE) FROM h),
      'hospital aggregation bounds'
    UNION ALL SELECT 'source_catalog_hospitals_valid',
      (SELECT COALESCE(bool_and(source_hospitals BETWEEN 1 AND 208),FALSE) FROM s),
      'source interface hospitals in valid range'
  ) q
  ORDER BY assertion
) TO 'study_b_v2/04_qc/V2_T1_FEASIBILITY_ASSERTIONS_V0_1.csv'
  (HEADER,DELIMITER ',');
