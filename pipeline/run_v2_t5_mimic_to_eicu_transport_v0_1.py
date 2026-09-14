#!/usr/bin/env python3
"""Locked MIMIC-to-eICU CatBoost transport evaluation."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import platform
from pathlib import Path

import catboost
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
MANIFEST_PATH = ROOT / "06_models" / "V2_T5_TRANSPORT_IMPLEMENTATION_MANIFEST_V0_1.json"
OUTPUT_DIR = ROOT / "restricted" / "t5_transport"
MODEL_DIR = ROOT / "06_models" / "transport_models"
RESULT_DIR = ROOT / "07_results" / "t5_transport"
MODELS = ("M0_LIU_ADAPTED", "M1_SNAPSHOT_CAT", "M2_DYNAMIC_CAT")
SCOPE_MODULES = {
    "rolling": "run_v2_t4_nested_catboost_v0_3",
    "fixed": "run_v2_t5_fixed_24_96_catboost_v0_1",
}
SCOPE_TUNING_DIRS = {
    "rolling": ROOT / "07_results" / "t4",
    "fixed": ROOT / "07_results" / "t5_fixed",
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
        "catboost": catboost.__version__,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "pyarrow": pyarrow.__version__,
        "scipy": scipy.__version__,
        "scikit_learn": sklearn.__version__,
    }


def verify_manifest() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest["status"] != "FROZEN_BEFORE_FIRST_TRANSPORT_PREDICTION":
        raise RuntimeError("Transport manifest status is not frozen")
    if runtime_versions() != manifest["runtime"]:
        raise RuntimeError("Transport runtime differs from manifest")
    for relative, expected in manifest["sha256"].items():
        observed = sha256(ROOT / relative)
        if observed != expected:
            raise RuntimeError(f"SHA-256 mismatch for {relative}: {observed} != {expected}")
    return manifest


def select_mimic_refit(tuning_path: Path) -> dict:
    tuning = pd.read_csv(tuning_path)
    required = {
        "outer_fold", "config_id", "depth", "learning_rate", "mean_auprc",
        "mean_logloss", "median_best_iteration",
    }
    if not required.issubset(tuning.columns):
        raise ValueError(f"Incomplete tuning file: {tuning_path}")
    fold_count = tuning["outer_fold"].nunique()
    summary = tuning.groupby(
        ["config_id", "depth", "learning_rate"], as_index=False
    ).agg(
        outer_folds=("outer_fold", "nunique"),
        aggregate_mean_auprc=("mean_auprc", "mean"),
        aggregate_mean_logloss=("mean_logloss", "mean"),
        refit_iterations=("median_best_iteration", "median"),
    )
    if len(summary) != 6 or not summary["outer_folds"].eq(fold_count).all():
        raise ValueError(f"Tuning configurations do not cover all MIMIC folds: {tuning_path}")
    winner = summary.sort_values(
        ["aggregate_mean_auprc", "aggregate_mean_logloss", "depth", "config_id"],
        ascending=[False, True, True, True],
    ).iloc[0].to_dict()
    winner["refit_iterations"] = min(700, max(50, int(np.median([
        winner["refit_iterations"]
    ]))))
    winner["mimic_outer_folds"] = int(fold_count)
    return winner


def metrics(y: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    return {
        "auprc": float(average_precision_score(y, probability)),
        "auroc": float(roc_auc_score(y, probability)),
        "brier": float(np.mean((y - probability) ** 2)),
        "logloss": float(log_loss(y, probability, labels=[0, 1])),
    }


def calibration(y: np.ndarray, probability: np.ndarray) -> dict[str, float]:
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


def load_scope_module(scope: str):
    module = importlib.import_module(SCOPE_MODULES[scope])
    return module, module.load_and_verify_manifest()


def run_one(scope: str, model_id: str, transport_manifest: dict) -> None:
    module, base_manifest = load_scope_module(scope)
    mimic, mimic_features, mimic_categorical = module.load_dataset("mimic", model_id, base_manifest)
    eicu, eicu_features, eicu_categorical = module.load_dataset("eicu", model_id, base_manifest)
    if mimic_features != eicu_features or mimic_categorical != eicu_categorical:
        raise ValueError("MIMIC/eICU feature specifications differ")

    tuning_path = SCOPE_TUNING_DIRS[scope] / f"mimic_{model_id}_TUNING_V0_1.csv"
    selected = select_mimic_refit(tuning_path)
    locked = transport_manifest["mimic_selected_refit"][scope][model_id]
    observed_lock = {
        "config_id": selected["config_id"],
        "depth": int(selected["depth"]),
        "learning_rate": float(selected["learning_rate"]),
        "iterations": int(selected["refit_iterations"]),
    }
    if observed_lock != locked:
        raise RuntimeError(
            f"MIMIC-only refit selection differs from frozen transport manifest: "
            f"{observed_lock} != {locked}"
        )
    params = {
        "depth": int(selected["depth"]),
        "learning_rate": float(selected["learning_rate"]),
    }
    iterations = int(selected["refit_iterations"])
    seed = int(base_manifest["primary_seed"])
    fitted = module.base.fit_one(
        mimic, None, mimic_features, mimic_categorical, params, iterations, seed
    )

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODEL_DIR / f"{scope}_MIMIC_TO_EICU_{model_id}_V0_1.cbm"
    fitted.save_model(model_path)

    probability = fitted.predict_proba(eicu[eicu_features])[:, 1]
    output = eicu[
        ["database", "patient_key", "stay_key", "hospital_id", "landmark_h", "outer_fold", "outcome_imv"]
    ].copy()
    output["model_id"] = model_id
    output["transport_source"] = "MIMIC-IV_v3.1"
    output["transport_target"] = "eICU-CRD_v2.0"
    output["probability"] = probability
    if output.duplicated(["stay_key", "landmark_h"]).any() or output["probability"].isna().any():
        raise ValueError("Invalid transport predictions")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(OUTPUT_DIR, 0o700)
    prediction_path = OUTPUT_DIR / f"{scope}_MIMIC_TO_EICU_{model_id}_PREDICTIONS_V0_1.parquet"
    output.to_parquet(prediction_path, index=False)
    os.chmod(prediction_path, 0o600)

    y = output["outcome_imv"].to_numpy(int)
    result = {
        "scope": scope,
        "development_database": "mimic",
        "transport_database": "eicu",
        "model_id": model_id,
        "n_landmarks": len(output),
        "events": int(y.sum()),
        "prevalence": float(y.mean()),
        "selected_config_id": selected["config_id"],
        "selected_depth": int(selected["depth"]),
        "selected_learning_rate": float(selected["learning_rate"]),
        "refit_iterations": iterations,
        "mimic_aggregate_inner_auprc": float(selected["aggregate_mean_auprc"]),
        "mimic_aggregate_inner_logloss": float(selected["aggregate_mean_logloss"]),
        **metrics(y, probability),
        **calibration(y, probability),
    }
    pd.DataFrame([result]).to_csv(
        RESULT_DIR / f"{scope}_MIMIC_TO_EICU_{model_id}_RAW_TRANSPORT_SUMMARY_V0_1.csv",
        index=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=list(SCOPE_MODULES), required=True)
    parser.add_argument("--models", nargs="+", choices=list(MODELS), default=list(MODELS))
    args = parser.parse_args()
    transport_manifest = verify_manifest()
    for model_id in args.models:
        run_one(args.scope, model_id, transport_manifest)


if __name__ == "__main__":
    main()
