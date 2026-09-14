# Dynamic landmark IMV prediction code

This directory is the public-code release candidate for the Study B/V2 analysis of repeated-landmark prediction of observed new invasive mechanical ventilation in MIMIC-IV v3.1 and eICU-CRD v2.0.

## Contents

- `pipeline/`: versioned SQL and Python scripts for cohort construction, feature engineering, grouped folds, nested modelling, calibration, transport, clinical-utility evaluation, and sensitivity analyses.
- `tests/`: SQL and Python integrity checks used during development.
- `specifications/`: frozen protocol, statistical analysis plan, feature definitions, model-comparison matrix, and analysis locks.
- `reporting/`: scripts that generate manuscript figures from aggregate result files.
- `public_assets/`: public feature and aggregate missingness dictionaries containing no patient-, stay-, or hospital-level rows.
- `requirements-lock.txt`: Python environment versions used for the locked analysis.
- `CODE_RELEASE_MANIFEST_V0_6.json`: SHA-256 inventory of every file in this release candidate.

## Data access

MIMIC-IV and eICU-CRD data are not included. Researchers must obtain credentialed access through PhysioNet and comply with the applicable data-use agreements. The scripts expect protected source data and derived row-level files under a local `restricted/` directory, which is excluded from this release.

## Reproduction order

1. Review the protocol, statistical analysis plan, outcome definition, feature registry, and model-comparison matrix in `specifications/`.
2. Run the T1-T3 SQL/Python files in `pipeline/` to construct outcome evidence, rolling landmarks, predictors, and grouped folds.
3. Run the T4 scripts for nested CatBoost and Elastic Net evaluation.
4. Run the T5 scripts for fixed-window analysis, transport, recalibration, heterogeneity, and clinical utility.
5. Run the T6 scripts for the prespecified sensitivity analyses.
6. Generate reporting figures only from the required aggregate outputs.

The scripts retain their frozen version identifiers and therefore are not renumbered simply for repository presentation. They do not download MIMIC-IV or eICU-CRD automatically.

## Public-release boundary

This candidate excludes patient-level data, hospital identifiers and mappings, row-level predictions, restricted bootstrap replicates, checkpoints, fitted model artefacts, database files, and local credentials. A repository license has not been selected; add an appropriate `LICENSE` file before describing the GitHub repository as open source.
