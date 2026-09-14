#!/usr/bin/env python3
"""Fixed t24 to t96 Liu-compatible secondary analysis using frozen budgets."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd


MODULE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE_DIR))
import run_v2_t4_nested_catboost_v0_1 as base  # noqa: E402


base.MANIFEST_PATH = base.ROOT / "06_models" / "V2_T5_FIXED_IMPLEMENTATION_MANIFEST_V0_1.json"
base.CHECKPOINT_DIR = base.ROOT / "restricted" / "t5_fixed_checkpoints"
base.RESULT_DIR = base.ROOT / "07_results" / "t5_fixed"


def load_and_verify_manifest() -> dict:
    manifest = json.loads(base.MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest["status"] != "FROZEN_BEFORE_FIRST_FIXED_T24_T96_MODEL_RESULT":
        raise RuntimeError("Fixed-window manifest status is not frozen")
    if base.runtime_versions() != manifest["runtime"]:
        raise RuntimeError("Runtime mismatch")
    for relative, expected in manifest["sha256"].items():
        if base.sha256(base.ROOT / relative) != expected:
            raise RuntimeError(f"SHA-256 mismatch for {relative}")
    return manifest


def load_dataset(database: str, model_id: str, manifest: dict):
    label = base.DATABASE_LABELS[database]
    features, categorical = base.feature_spec(model_id)
    id_columns = ["database", "patient_key", "stay_key", "hospital_id", "landmark_h", "outer_fold"]
    read_columns = list(dict.fromkeys(id_columns + features))
    predictors = pd.read_parquet(
        base.PREDICTOR_PATH,
        columns=read_columns,
        filters=[("database", "=", label), ("landmark_h", "=", 24)],
    )
    if predictors.duplicated(["stay_key", "landmark_h"]).any():
        raise ValueError("Fixed-window predictor keys are not unique")

    if database == "mimic":
        outcome = pd.read_parquet(
            base.ROOT / "restricted" / "V2_MIMIC_ROLLING_OUTCOME_V0_1.parquet",
            columns=["stay_id", "landmark_h", "horizon_h", "outcome_imv"],
        )
        outcome["stay_key"] = outcome["stay_id"].astype(str)
        expected_rows, expected_events = 31751, 1488
    else:
        outcome = pd.read_parquet(
            base.ROOT / "restricted" / "V2_EICU_ROLLING_OUTCOME_V0_1.parquet",
            columns=["icu_stay_key", "landmark_h", "horizon_h", "outcome_imv"],
        )
        outcome["stay_key"] = outcome["icu_stay_key"].astype(str)
        expected_rows, expected_events = 78409, 1418
    outcome = outcome.loc[
        outcome["landmark_h"].eq(24)
        & outcome["horizon_h"].eq(72)
        & outcome["outcome_imv"].isin([0, 1]),
        ["stay_key", "landmark_h", "outcome_imv"],
    ]
    data = predictors.merge(outcome, on=["stay_key", "landmark_h"], how="inner", validate="one_to_one")
    if len(data) != expected_rows or int(data["outcome_imv"].sum()) != expected_events:
        raise ValueError(
            f"Fixed outcome reconciliation failed: rows={len(data)}, events={data['outcome_imv'].sum()}"
        )
    if set(data["outer_fold"].astype(int)) != set(manifest["outer_folds"][database]):
        raise ValueError("Fixed outer fold labels differ from manifest")
    for feature in categorical:
        data[feature] = data[feature].fillna("__MISSING__").astype(str)
    return data, features, categorical


base.load_and_verify_manifest = load_and_verify_manifest
base.load_dataset = load_dataset


if __name__ == "__main__":
    base.main()
