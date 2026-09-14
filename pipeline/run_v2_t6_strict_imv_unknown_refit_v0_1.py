#!/usr/bin/env python3
"""Reconstruct frozen primary M1/M2 fits and extend held-out scoring to Unknown rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from pathlib import Path

import catboost
import numpy as np
import pandas as pd
import pyarrow
import scipy
import sklearn


ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE_DIR))
import run_v2_t4_nested_catboost_v0_3 as primary_module  # noqa: E402


MANIFEST_PATH = ROOT / "06_models" / "V2_T6_STRICT_IMV_UNKNOWN_MANIFEST_V0_1.json"
PREDICTOR_PATH = ROOT / "restricted" / "V2_PREDICTOR_MATRIX_V0_1.parquet"
PRIMARY_DIR = ROOT / "restricted" / "t4_checkpoints"
OUTPUT_DIR = ROOT / "restricted" / "t6_strict_imv_unknown"
RESULT_DIR = ROOT / "07_results" / "t6_sensitivity"
MODELS = ("M1_SNAPSHOT_CAT", "M2_DYNAMIC_CAT")
DATABASE_LABELS = {"mimic": "MIMIC-IV v3.1", "eicu": "eICU-CRD v2.0"}
EXPECTED_ROWS = {"mimic": 233319, "eicu": 572055}
EXPECTED_PRIMARY = {"mimic": (232316, 5765), "eicu": (568182, 4384)}
MAX_RECONSTRUCTION_ERROR = 1e-12


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_versions() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "catboost": catboost.__version__,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "pyarrow": pyarrow.__version__,
        "scipy": scipy.__version__,
        "scikit_learn": sklearn.__version__,
    }


def verify_manifest() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    expected = "FROZEN_BEFORE_FIRST_STRICT_IMV_UNKNOWN_MODEL_REFIT_OR_RESULT"
    if manifest["status"] != expected:
        raise RuntimeError("Strict-IMV/Unknown manifest status is not frozen")
    if runtime_versions() != manifest["runtime"]:
        raise RuntimeError("Strict-IMV/Unknown runtime differs from manifest")
    for relative, expected_hash in manifest["sha256"].items():
        observed = sha256(ROOT / relative)
        if observed != expected_hash:
            raise RuntimeError(f"SHA-256 mismatch for {relative}: {observed} != {expected_hash}")
    return manifest


def load_all_rows(database: str, model_id: str, base_manifest: dict):
    features, categorical = primary_module.base.feature_spec(model_id)
    id_columns = ["database", "patient_key", "stay_key", "hospital_id", "landmark_h", "outer_fold"]
    read_columns = list(dict.fromkeys(id_columns + features))
    predictors = pd.read_parquet(
        PREDICTOR_PATH,
        columns=read_columns,
        filters=[("database", "=", DATABASE_LABELS[database])],
    )
    if predictors.duplicated(["stay_key", "landmark_h"]).any():
        raise ValueError("Predictor keys are not unique")
    if database == "mimic":
        outcome = pd.read_parquet(
            ROOT / "restricted" / "V2_MIMIC_ROLLING_OUTCOME_V0_1.parquet",
            columns=["stay_id", "landmark_h", "horizon_h", "evidence_grade", "outcome_imv"],
        )
        outcome["stay_key"] = outcome["stay_id"].astype(str)
        keep = ["stay_key", "landmark_h", "evidence_grade", "outcome_imv"]
    else:
        outcome = pd.read_parquet(
            ROOT / "restricted" / "V2_EICU_ROLLING_OUTCOME_V0_1.parquet",
            columns=[
                "icu_stay_key", "landmark_h", "horizon_h", "evidence_grade",
                "outcome_imv", "outcome_imv_distinct_time",
            ],
        )
        outcome["stay_key"] = outcome["icu_stay_key"].astype(str)
        keep = [
            "stay_key", "landmark_h", "evidence_grade", "outcome_imv",
            "outcome_imv_distinct_time",
        ]
    outcome = outcome.loc[outcome["horizon_h"].eq(24), keep]
    data = predictors.merge(outcome, on=["stay_key", "landmark_h"], how="inner", validate="one_to_one")
    if len(data) != EXPECTED_ROWS[database]:
        raise ValueError(f"Full-row reconciliation failed for {database}: {len(data)}")
    primary = data["outcome_imv"].isin([0, 1])
    expected_n, expected_events = EXPECTED_PRIMARY[database]
    if int(primary.sum()) != expected_n or int(data.loc[primary, "outcome_imv"].sum()) != expected_events:
        raise ValueError(f"Primary-row reconciliation failed for {database}")
    if set(data["outer_fold"].astype(int)) != set(base_manifest["outer_folds"][database]):
        raise ValueError("Outer fold labels differ from frozen primary manifest")
    for feature in categorical:
        data[feature] = data[feature].fillna("__MISSING__").astype(str)
    return data, features, categorical


def validate_checkpoint(output: pd.DataFrame, database: str, model_id: str, fold: int) -> None:
    required = {
        "database", "patient_key", "stay_key", "hospital_id", "landmark_h", "outer_fold",
        "outcome_imv", "evidence_grade", "model_id", "probability", "selected_config_id",
        "refit_iterations", "primary_probability", "primary_abs_error",
    }
    if database == "eicu":
        required.add("outcome_imv_distinct_time")
    if not required.issubset(output.columns):
        raise ValueError(f"Checkpoint columns incomplete for {database} {model_id} fold {fold}")
    if output.empty or not output["outer_fold"].eq(fold).all() or not output["model_id"].eq(model_id).all():
        raise ValueError(f"Checkpoint identity mismatch for {database} {model_id} fold {fold}")
    if output.duplicated(["stay_key", "landmark_h"]).any():
        raise ValueError(f"Duplicate checkpoint keys for {database} {model_id} fold {fold}")
    primary = output["outcome_imv"].isin([0, 1])
    if output.loc[primary, "primary_abs_error"].max() > MAX_RECONSTRUCTION_ERROR:
        raise ValueError(f"Reconstruction mismatch in checkpoint for {database} {model_id} fold {fold}")
    if output["probability"].isna().any() or not output["probability"].between(0, 1).all():
        raise ValueError(f"Invalid probabilities for {database} {model_id} fold {fold}")


def run(database: str, model_id: str) -> None:
    base_manifest = primary_module.load_and_verify_manifest()
    data, features, categorical = load_all_rows(database, model_id, base_manifest)
    primary_oof_path = PRIMARY_DIR / f"{database}_{model_id}_OOF_V0_1.parquet"
    primary_oof = pd.read_parquet(primary_oof_path)
    choices = primary_oof[["outer_fold", "selected_config_id", "refit_iterations"]].drop_duplicates()
    if choices["outer_fold"].duplicated().any():
        raise ValueError("Primary OOF has multiple refit choices within an outer fold")
    choices = choices.set_index("outer_fold")
    grid = {item["config_id"]: item for item in base_manifest["catboost_grid"]}
    seed = int(base_manifest["primary_seed"])
    group_column = "patient_key" if database == "mimic" else "hospital_id"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(OUTPUT_DIR, 0o700)
    parts = []
    audit_rows = []
    for fold in sorted(data["outer_fold"].astype(int).unique()):
        checkpoint = OUTPUT_DIR / f"{database}_{model_id}_outer{fold}_extended_predictions.parquet"
        if checkpoint.exists():
            output = pd.read_parquet(checkpoint)
            validate_checkpoint(output, database, model_id, fold)
            status = "REUSED_LOCKED_CHECKPOINT"
        else:
            train = data.loc[data["outer_fold"].ne(fold) & data["outcome_imv"].isin([0, 1])].copy()
            test = data.loc[data["outer_fold"].eq(fold)].copy()
            if set(train[group_column].astype(str)) & set(test[group_column].astype(str)):
                raise ValueError(f"Outer group leakage in {database} {model_id} fold {fold}")
            config_id = str(choices.loc[fold, "selected_config_id"])
            iterations = int(choices.loc[fold, "refit_iterations"])
            fitted = primary_module.base.fit_one(
                train, None, features, categorical, grid[config_id], iterations, seed
            )
            probability = fitted.predict_proba(test[features])[:, 1]
            identity = [
                "database", "patient_key", "stay_key", "hospital_id", "landmark_h", "outer_fold",
                "outcome_imv", "evidence_grade",
            ]
            if database == "eicu":
                identity.append("outcome_imv_distinct_time")
            output = test[identity].copy()
            output["model_id"] = model_id
            output["probability"] = probability
            output["selected_config_id"] = config_id
            output["refit_iterations"] = iterations
            saved = primary_oof.loc[
                primary_oof["outer_fold"].eq(fold), ["stay_key", "landmark_h", "probability"]
            ].rename(columns={"probability": "primary_probability"})
            output = output.merge(saved, on=["stay_key", "landmark_h"], how="left", validate="one_to_one")
            output["primary_abs_error"] = (output["probability"] - output["primary_probability"]).abs()
            validate_checkpoint(output, database, model_id, fold)
            output.to_parquet(checkpoint, index=False)
            os.chmod(checkpoint, 0o600)
            status = "FIT_AND_SAVED"
        primary_mask = output["outcome_imv"].isin([0, 1])
        audit_rows.append({
            "database": database,
            "model_id": model_id,
            "outer_fold": fold,
            "status": status,
            "selected_config_id": str(output["selected_config_id"].iloc[0]),
            "refit_iterations": int(output["refit_iterations"].iloc[0]),
            "n_scored": len(output),
            "n_primary": int(primary_mask.sum()),
            "n_unknown": int((output["outcome_imv"] == 9).sum()),
            "max_primary_abs_error": float(output.loc[primary_mask, "primary_abs_error"].max()),
        })
        parts.append(output)

    extended = pd.concat(parts, ignore_index=True).sort_values(["outer_fold", "stay_key", "landmark_h"])
    if len(extended) != EXPECTED_ROWS[database] or extended.duplicated(["stay_key", "landmark_h"]).any():
        raise ValueError(f"Extended OOF reconciliation failed for {database} {model_id}")
    primary_mask = extended["outcome_imv"].isin([0, 1])
    if extended.loc[primary_mask, "primary_abs_error"].max() > MAX_RECONSTRUCTION_ERROR:
        raise ValueError(f"Extended OOF reconstruction mismatch for {database} {model_id}")
    path = OUTPUT_DIR / f"{database}_{model_id}_EXTENDED_OOF_V0_1.parquet"
    extended.to_parquet(path, index=False)
    os.chmod(path, 0o600)
    pd.DataFrame(audit_rows).to_csv(
        RESULT_DIR / f"{database}_{model_id}_STRICT_UNKNOWN_REFIT_AUDIT_V0_1.csv", index=False
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", choices=["mimic", "eicu"], required=True)
    parser.add_argument("--models", nargs="+", choices=list(MODELS), default=list(MODELS))
    args = parser.parse_args()
    verify_manifest()
    for model_id in args.models:
        run(args.database, model_id)


if __name__ == "__main__":
    main()
