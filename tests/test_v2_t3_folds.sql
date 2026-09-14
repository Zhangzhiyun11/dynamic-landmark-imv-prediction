COPY (
  WITH eh AS (
    SELECT * FROM read_csv_auto('study_b_v2/restricted/V2_EICU_HOSPITAL_FOLD_INPUT_V0_1.csv')
  ), ef AS (
    SELECT * FROM read_csv_auto('study_b_v2/restricted/V2_EICU_HOSPITAL_FOLD_INPUT_V0_1.csv') i
    JOIN read_csv_auto('study_b_v2/restricted/V2_EICU_HOSPITAL_OUTER_FOLDS_V0_1.csv') m
      USING(hospital_id)
  ), mf AS (
    SELECT * FROM read_csv_auto('study_b_v2/restricted/V2_MIMIC_PATIENT_OUTER_FOLDS_V0_1.csv')
  ), summary AS (
    SELECT * FROM read_csv_auto('study_b_v2/04_qc/V2_T3_OUTER_FOLD_SUMMARY_V0_1.csv')
  )
  SELECT * FROM (
    SELECT 'eicu_208_hospitals_once' AS assertion,
      (SELECT COUNT(*)=208 AND COUNT(DISTINCT hospital_id)=208 FROM ef) AS passed,
      'each hospital has exactly one outer fold' AS detail
    UNION ALL SELECT 'eicu_ten_folds',
      (SELECT MIN(outer_fold)=1 AND MAX(outer_fold)=10 AND COUNT(DISTINCT outer_fold)=10 FROM ef),
      'fold ids 1 through 10'
    UNION ALL SELECT 'eicu_event_rows_balanced',
      (SELECT MAX(event_rows)-MIN(event_rows)<=2 FROM summary WHERE database='eICU'),
      'grouped folds differ by at most two event rows'
    UNION ALL SELECT 'eicu_each_fold_has_events',
      (SELECT MIN(event_rows)>0 FROM summary WHERE database='eICU'),
      'all outer folds estimable for discrimination'
    UNION ALL SELECT 'eicu_rows_inherent_cluster_balance',
      (SELECT MAX(landmarks)::DOUBLE/MIN(landmarks)<1.40 FROM summary WHERE database='eICU'),
      'hospital indivisibility accepted below 1.40 max/min'
    UNION ALL SELECT 'eicu_fold_totals_reconcile',
      (SELECT SUM(landmarks)=572055 AND SUM(event_rows)=4384
         FROM summary WHERE database='eICU'),
      'primary rolling cohort totals'
    UNION ALL SELECT 'mimic_patients_once',
      (SELECT COUNT(*)=31976 AND COUNT(DISTINCT subject_id)=31976 FROM mf),
      'each patient has exactly one outer fold'
    UNION ALL SELECT 'mimic_five_folds',
      (SELECT MIN(outer_fold)=1 AND MAX(outer_fold)=5 AND COUNT(DISTINCT outer_fold)=5 FROM mf),
      'fold ids 1 through 5'
    UNION ALL SELECT 'mimic_event_rows_balanced',
      (SELECT MAX(event_rows)-MIN(event_rows)<=1 FROM summary WHERE database='MIMIC-IV'),
      'patient grouped event rows balanced'
    UNION ALL SELECT 'mimic_rows_balanced',
      (SELECT MAX(landmarks)-MIN(landmarks)<=1 FROM summary WHERE database='MIMIC-IV'),
      'patient grouped landmark rows balanced'
    UNION ALL SELECT 'mimic_temporal_labels',
      (SELECT COUNT(*)=0 FROM mf WHERE
        (anchor_year_group IN ('2008 - 2010','2011 - 2013','2014 - 2016') AND temporal_role<>'temporal_development')
        OR (anchor_year_group IN ('2017 - 2019','2020 - 2022') AND temporal_role<>'temporal_validation')),
      'predefined deidentified time grouping'
  ) q ORDER BY assertion
) TO 'study_b_v2/04_qc/V2_T3_FOLD_ASSERTIONS_V0_1.csv'
  (HEADER,DELIMITER ',');
