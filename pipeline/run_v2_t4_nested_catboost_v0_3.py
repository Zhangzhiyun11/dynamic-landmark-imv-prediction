#!/usr/bin/env python3
"""V0.3 wrapper: unique read columns plus explicit primary horizon=24."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd


MODULE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE_DIR))
import run_v2_t4_nested_catboost_v0_1 as base  # noqa: E402


base.MANIFEST_PATH = base.ROOT / "06_models" / "V2_GATE3_IMPLEMENTATION_MANIFEST_V0_3.json"


def load_and_verify_manifest() -> dict:
    manifest = json.loads(base.MANIFEST_PATH.read_text(encoding="utf-8"))
    expected_status = "FROZEN_AFTER_TWO_FAILED_MERGE_PREFLIGHTS_BEFORE_FIRST_MODEL_FIT_OR_RESULT"
    if manifest["status"] != expected_status:
        raise RuntimeError("Gate 3 V0.3 manifest status is not frozen")
    observed_runtime = base.runtime_versions()
    if observed_runtime != manifest["runtime"]:
        raise RuntimeError(f"Runtime mismatch: {observed_runtime} != {manifest['runtime']}")
    for relative, expected in manifest["sha256"].items():
        observed = base.sha256(base.ROOT / relative)
        if observed != expected:
            raise RuntimeError(f"SHA-256 mismatch for {relative}: {observed} != {expected}")
    return manifest


def load_dataset(database: str, model_id: str, manifest: dict):
    label = base.DATABASE_LABELS[database]
    features, categorical = base.feature_spec(model_id)
    id_columns = ["database", "patient_key", "stay_key", "hospital_id", "landmark_h", "outer_fold"]
    read_columns = list(dict.fromkeys(id_columns + features))
    predictors = pd.read_parquet(
        base.PREDICTOR_PATH, columns=read_columns, filters=[("database", "=", label)]
    )
    if predictors.duplicated(["stay_key", "landmark_h"]).any():
        raise ValueError("Predictor keys are not unique")

    if database == "mimic":
        outcome_path = base.ROOT / "restricted" / "V2_MIMIC_ROLLING_OUTCOME_V0_1.parquet"
        outcome = pd.read_parquet(
            outcome_path, columns=["stay_id", "landmark_h", "horizon_h", "outcome_imv"]
        )
        outcome["stay_key"] = outcome["stay_id"].astype(str)
        expected_rows, expected_events = 232316, 5765
    else:
        outcome_path = base.ROOT / "restricted" / "V2_EICU_ROLLING_OUTCOME_V0_1.parquet"
        outcome = pd.read_parquet(
            outcome_path, columns=["icu_stay_key", "landmark_h", "horizon_h", "outcome_imv"]
        )
        outcome["stay_key"] = outcome["icu_stay_key"].astype(str)
        expected_rows, expected_events = 568182, 4384
    outcome = outcome.loc[
        outcome["horizon_h"].eq(24) & outcome["outcome_imv"].isin([0, 1]),
        ["stay_key", "landmark_h", "outcome_imv"],
    ]
    data = predictors.merge(outcome, on=["stay_key", "landmark_h"], how="inner", validate="one_to_one")
    if len(data) != expected_rows or int(data["outcome_imv"].sum()) != expected_events:
        raise ValueError(
            f"Outcome reconciliation failed for {database}: rows={len(data)}, events={data['outcome_imv'].sum()}"
        )
    if set(data["outer_fold"].astype(int)) != set(manifest["outer_folds"][database]):
        raise ValueError("Outer fold labels differ from manifest")
    for feature in categorical:
        data[feature] = data[feature].fillna("__MISSING__").astype(str)
    return data, features, categorical


base.load_and_verify_manifest = load_and_verify_manifest
base.load_dataset = load_dataset


if __name__ == "__main__":
    base.main()
