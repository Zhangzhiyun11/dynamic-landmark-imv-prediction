#!/usr/bin/env python3
"""Cross-fit logistic recalibration for frozen rolling and fixed OOF predictions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow
import scipy
import sklearn
from scipy.optimize import brentq
from scipy.special import expit, logit
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score


ROOT = Path(__file__).resolve().parents[1]
INPUT_DIRS = {
    "rolling": ROOT / "restricted" / "t4_checkpoints",
    "fixed": ROOT / "restricted" / "t5_fixed_checkpoints",
}
OUTPUT_DIR = ROOT / "restricted" / "t5_recalibration"
RESULT_DIR = ROOT / "07_results" / "t5_recalibration"
MANIFEST_PATH = ROOT / "06_models" / "V2_T5_RECALIBRATION_IMPLEMENTATION_MANIFEST_V0_1.json"
MODELS = ("M0_LIU_ADAPTED", "M1_SNAPSHOT_CAT", "M2_DYNAMIC_CAT")
PROBABILITY_COLUMNS = {
    "raw": "raw_probability",
    "intercept_only": "crossfit_intercept_only_probability",
    "intercept_slope": "crossfit_intercept_slope_probability",
}
EPSILON = 1e-6


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_versions() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "pyarrow": pyarrow.__version__,
        "scipy": scipy.__version__,
        "scikit_learn": sklearn.__version__,
    }


def verify_manifest() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest["status"] != "FROZEN_BEFORE_FIRST_CROSSFIT_RECALIBRATED_RESULT":
        raise RuntimeError("Recalibration manifest status is not frozen")
    if runtime_versions() != manifest["runtime"]:
        raise RuntimeError("Recalibration runtime differs from manifest")
    for relative, expected in manifest["sha256"].items():
        observed = sha256(ROOT / relative)
        if observed != expected:
            raise RuntimeError(f"SHA-256 mismatch for {relative}: {observed} != {expected}")


def metric_set(y: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    return {
        "auprc": float(average_precision_score(y, probability)),
        "auroc": float(roc_auc_score(y, probability)),
        "brier": float(np.mean((y - probability) ** 2)),
        "logloss": float(log_loss(y, probability, labels=[0, 1])),
    }


def calibration_set(y: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    p = np.clip(probability, EPSILON, 1 - EPSILON)
    lp = logit(p)
    calibration_in_the_large = brentq(
        lambda value: float(np.sum(y - expit(lp + value))), -30, 30
    )
    joint = LogisticRegression(C=np.inf, solver="lbfgs", max_iter=1000)
    joint.fit(lp.reshape(-1, 1), y)
    return {
        "mean_predicted_risk": float(p.mean()),
        "oe_ratio": float(y.mean() / p.mean()),
        "calibration_in_the_large": float(calibration_in_the_large),
        "calibration_intercept_joint": float(joint.intercept_[0]),
        "calibration_slope": float(joint.coef_[0, 0]),
    }


def fit_calibrators(y: np.ndarray, probability: np.ndarray) -> tuple[float, float, float]:
    p = np.clip(probability, EPSILON, 1 - EPSILON)
    lp = logit(p)
    intercept_only = brentq(
        lambda value: float(np.sum(y - expit(lp + value))), -30, 30
    )
    joint = LogisticRegression(C=np.inf, solver="lbfgs", max_iter=1000)
    joint.fit(lp.reshape(-1, 1), y)
    return float(intercept_only), float(joint.intercept_[0]), float(joint.coef_[0, 0])


def run_one(scope: str, database: str, model_id: str) -> None:
    input_path = INPUT_DIRS[scope] / f"{database}_{model_id}_OOF_V0_1.parquet"
    frame = pd.read_parquet(input_path)
    required = {
        "database", "patient_key", "stay_key", "hospital_id", "landmark_h",
        "outer_fold", "outcome_imv", "probability",
    }
    if not required.issubset(frame.columns):
        raise ValueError(f"Missing required columns in {input_path}")
    if frame.duplicated(["stay_key", "landmark_h"]).any():
        raise ValueError("OOF keys are not unique")

    output_parts: list[pd.DataFrame] = []
    parameter_rows: list[dict] = []
    for fold in sorted(frame["outer_fold"].astype(int).unique()):
        train = frame.loc[frame["outer_fold"].ne(fold)]
        test = frame.loc[frame["outer_fold"].eq(fold)].copy()
        if train["outcome_imv"].nunique() != 2 or test["outcome_imv"].nunique() != 2:
            raise ValueError(f"Non-estimable recalibration fold {fold}")
        intercept_only, joint_intercept, joint_slope = fit_calibrators(
            train["outcome_imv"].to_numpy(int), train["probability"].to_numpy(float)
        )
        test_lp = logit(np.clip(test["probability"].to_numpy(float), EPSILON, 1 - EPSILON))
        test["raw_probability"] = test["probability"].to_numpy(float)
        test["crossfit_intercept_only_probability"] = expit(test_lp + intercept_only)
        test["crossfit_intercept_slope_probability"] = expit(joint_intercept + joint_slope * test_lp)
        output_parts.append(test)
        parameter_rows.append({
            "scope": scope,
            "database": database,
            "model_id": model_id,
            "held_out_outer_fold": int(fold),
            "calibration_training_rows": len(train),
            "calibration_training_events": int(train["outcome_imv"].sum()),
            "intercept_only": intercept_only,
            "joint_intercept": joint_intercept,
            "joint_slope": joint_slope,
        })

    updated = pd.concat(output_parts, ignore_index=True).sort_values(
        ["outer_fold", "stay_key", "landmark_h"]
    )
    if len(updated) != len(frame) or updated.duplicated(["stay_key", "landmark_h"]).any():
        raise ValueError("Cross-fit updated rows do not reconcile")
    for column in PROBABILITY_COLUMNS.values():
        if updated[column].isna().any() or not updated[column].between(0, 1).all():
            raise ValueError(f"Invalid probabilities in {column}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(OUTPUT_DIR, 0o700)
    restricted_columns = [
        "database", "patient_key", "stay_key", "hospital_id", "landmark_h",
        "outer_fold", "outcome_imv", *PROBABILITY_COLUMNS.values(),
    ]
    output_path = OUTPUT_DIR / f"{scope}_{database}_{model_id}_CROSSFIT_RECALIBRATED_V0_1.parquet"
    updated[restricted_columns].to_parquet(output_path, index=False)
    os.chmod(output_path, 0o600)

    y = updated["outcome_imv"].to_numpy(int)
    result_rows = []
    for update_type, column in PROBABILITY_COLUMNS.items():
        probability = updated[column].to_numpy(float)
        result_rows.append({
            "scope": scope,
            "database": database,
            "model_id": model_id,
            "update_type": update_type,
            "n_landmarks": len(updated),
            "events": int(y.sum()),
            "prevalence": float(y.mean()),
            **metric_set(y, probability),
            **calibration_set(y, probability),
        })
    pd.DataFrame(result_rows).to_csv(
        RESULT_DIR / f"{scope}_{database}_{model_id}_CROSSFIT_RECALIBRATION_SUMMARY_V0_1.csv",
        index=False,
    )
    pd.DataFrame(parameter_rows).to_csv(
        RESULT_DIR / f"{scope}_{database}_{model_id}_CROSSFIT_RECALIBRATION_PARAMETERS_V0_1.csv",
        index=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=list(INPUT_DIRS), required=True)
    parser.add_argument("--database", choices=["mimic", "eicu"], required=True)
    parser.add_argument("--models", nargs="+", choices=list(MODELS), default=list(MODELS))
    args = parser.parse_args()
    verify_manifest()
    for model_id in args.models:
        run_one(args.scope, args.database, model_id)


if __name__ == "__main__":
    main()
