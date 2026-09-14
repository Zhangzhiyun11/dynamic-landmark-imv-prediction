#!/usr/bin/env python3
"""Locked hospital-bootstrap and cross-fit recalibration of transport predictions."""

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
MANIFEST_PATH = ROOT / "06_models" / "V2_T5_TRANSPORT_POSTEVALUATION_MANIFEST_V0_1.json"
INPUT_DIR = ROOT / "restricted" / "t5_transport"
OUTPUT_DIR = ROOT / "restricted" / "t5_transport_postevaluation"
RESULT_DIR = ROOT / "07_results" / "t5_transport"
MODELS = ("M0_LIU_ADAPTED", "M1_SNAPSHOT_CAT", "M2_DYNAMIC_CAT")
COMPARISONS = (("M1_SNAPSHOT_CAT", "M2_DYNAMIC_CAT"), ("M0_LIU_ADAPTED", "M2_DYNAMIC_CAT"))
PROBABILITY_COLUMNS = {
    "raw": "raw_probability",
    "intercept_only": "crossfit_intercept_only_probability",
    "intercept_slope": "crossfit_intercept_slope_probability",
}
SEED = 20260909
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
    expected = "FROZEN_AFTER_RAW_TRANSPORT_BEFORE_FIRST_BOOTSTRAP_OR_UPDATED_RESULT"
    if manifest["status"] != expected:
        raise RuntimeError("Transport post-evaluation manifest status is not frozen")
    if runtime_versions() != manifest["runtime"]:
        raise RuntimeError("Transport post-evaluation runtime differs from manifest")
    for relative, expected_hash in manifest["sha256"].items():
        observed = sha256(ROOT / relative)
        if observed != expected_hash:
            raise RuntimeError(f"SHA-256 mismatch for {relative}: {observed} != {expected_hash}")


def metric_set(y: np.ndarray, probability: np.ndarray, weight=None) -> dict[str, float]:
    return {
        "auprc": float(average_precision_score(y, probability, sample_weight=weight)),
        "auroc": float(roc_auc_score(y, probability, sample_weight=weight)),
        "brier": float(np.average((y - probability) ** 2, weights=weight)),
        "logloss": float(log_loss(y, probability, sample_weight=weight, labels=[0, 1])),
    }


def calibration_set(y: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    p = np.clip(probability, EPSILON, 1 - EPSILON)
    lp = logit(p)
    citl = brentq(lambda value: float(np.sum(y - expit(lp + value))), -30, 30)
    joint = LogisticRegression(C=np.inf, solver="lbfgs", max_iter=1000)
    joint.fit(lp.reshape(-1, 1), y)
    return {
        "mean_predicted_risk": float(p.mean()),
        "oe_ratio": float(y.mean() / p.mean()),
        "calibration_in_the_large": float(citl),
        "calibration_intercept_joint": float(joint.intercept_[0]),
        "calibration_slope": float(joint.coef_[0, 0]),
    }


def fit_calibrators(y: np.ndarray, probability: np.ndarray) -> tuple[float, float, float]:
    lp = logit(np.clip(probability, EPSILON, 1 - EPSILON))
    intercept_only = brentq(
        lambda value: float(np.sum(y - expit(lp + value))), -30, 30
    )
    joint = LogisticRegression(C=np.inf, solver="lbfgs", max_iter=1000)
    joint.fit(lp.reshape(-1, 1), y)
    return float(intercept_only), float(joint.intercept_[0]), float(joint.coef_[0, 0])


def load_scope(scope: str) -> pd.DataFrame:
    keys = [
        "database", "patient_key", "stay_key", "hospital_id", "landmark_h",
        "outer_fold", "outcome_imv", "transport_source", "transport_target",
    ]
    merged = None
    for model_id in MODELS:
        path = INPUT_DIR / f"{scope}_MIMIC_TO_EICU_{model_id}_PREDICTIONS_V0_1.parquet"
        frame = pd.read_parquet(path)
        required = set(keys + ["model_id", "probability"])
        if not required.issubset(frame.columns):
            raise ValueError(f"Missing required columns in {path}")
        if frame.duplicated(["stay_key", "landmark_h"]).any():
            raise ValueError(f"Duplicate transport keys in {path}")
        part = frame[keys + ["probability"]].rename(columns={"probability": model_id})
        if merged is None:
            merged = part
        else:
            merged = merged.merge(part, on=keys, how="inner", validate="one_to_one")
    assert merged is not None
    expected_rows = 568182 if scope == "rolling" else 78409
    expected_events = 4384 if scope == "rolling" else 1418
    if len(merged) != expected_rows or int(merged["outcome_imv"].sum()) != expected_events:
        raise ValueError("Transport row/event reconciliation failed")
    if merged[list(MODELS)].isna().any().any():
        raise ValueError("Missing transport probability")
    return merged


def run_bootstrap(scope: str, frame: pd.DataFrame, replicates: int) -> None:
    y = frame["outcome_imv"].to_numpy(int)
    probabilities = {model: frame[model].to_numpy(float) for model in MODELS}
    raw_metrics = {model: metric_set(y, probability) for model, probability in probabilities.items()}
    point_rows = []
    for reference, candidate in COMPARISONS:
        for metric in raw_metrics[reference]:
            point_rows.append({
                "scope": scope,
                "reference": reference,
                "candidate": candidate,
                "metric": metric,
                "reference_estimate": raw_metrics[reference][metric],
                "candidate_estimate": raw_metrics[candidate][metric],
                "candidate_minus_reference": (
                    raw_metrics[candidate][metric] - raw_metrics[reference][metric]
                ),
                "n_landmarks": len(frame),
                "events": int(y.sum()),
                "prevalence": float(y.mean()),
            })
    point = pd.DataFrame(point_rows)
    point.to_csv(RESULT_DIR / f"{scope}_RAW_TRANSPORT_PAIRED_POINT_V0_1.csv", index=False)

    codes, hospitals = pd.factorize(frame["hospital_id"], sort=True)
    n_hospitals = len(hospitals)
    rng = np.random.default_rng(SEED)
    records = []
    for replicate in range(1, replicates + 1):
        sampled = rng.integers(0, n_hospitals, size=n_hospitals)
        group_weights = np.bincount(sampled, minlength=n_hospitals).astype(float)
        weight = group_weights[codes]
        if np.sum(weight[y == 1]) == 0 or np.sum(weight[y == 0]) == 0:
            continue
        sampled_metrics = {
            model: metric_set(y, probability, weight)
            for model, probability in probabilities.items()
        }
        for reference, candidate in COMPARISONS:
            for metric in sampled_metrics[reference]:
                records.append({
                    "replicate": replicate,
                    "reference": reference,
                    "candidate": candidate,
                    "metric": metric,
                    "reference_estimate": sampled_metrics[reference][metric],
                    "candidate_estimate": sampled_metrics[candidate][metric],
                    "candidate_minus_reference": (
                        sampled_metrics[candidate][metric] - sampled_metrics[reference][metric]
                    ),
                })
    bootstrap = pd.DataFrame(records)
    output_path = OUTPUT_DIR / f"{scope}_RAW_TRANSPORT_HOSPITAL_BOOTSTRAP_V0_1.parquet"
    bootstrap.to_parquet(output_path, index=False)
    os.chmod(output_path, 0o600)
    ci = bootstrap.groupby(["reference", "candidate", "metric"], as_index=False).agg(
        reference_ci_low=("reference_estimate", lambda x: x.quantile(0.025)),
        reference_ci_high=("reference_estimate", lambda x: x.quantile(0.975)),
        candidate_ci_low=("candidate_estimate", lambda x: x.quantile(0.025)),
        candidate_ci_high=("candidate_estimate", lambda x: x.quantile(0.975)),
        difference_ci_low=("candidate_minus_reference", lambda x: x.quantile(0.025)),
        difference_ci_high=("candidate_minus_reference", lambda x: x.quantile(0.975)),
        bootstrap_replicates=("replicate", "nunique"),
    )
    point.merge(ci, on=["reference", "candidate", "metric"], validate="one_to_one").to_csv(
        RESULT_DIR / f"{scope}_RAW_TRANSPORT_HOSPITAL_BOOTSTRAP_CI_V0_1.csv", index=False
    )


def run_recalibration(scope: str, frame: pd.DataFrame) -> None:
    for model_id in MODELS:
        parts = []
        parameter_rows = []
        for fold in sorted(frame["outer_fold"].astype(int).unique()):
            train = frame.loc[frame["outer_fold"].ne(fold)]
            test = frame.loc[frame["outer_fold"].eq(fold)].copy()
            hospital_overlap = set(train["hospital_id"].astype(str)) & set(test["hospital_id"].astype(str))
            if hospital_overlap:
                raise ValueError(f"Hospital leakage in recalibration fold {fold}")
            if train["outcome_imv"].nunique() != 2 or test["outcome_imv"].nunique() != 2:
                raise ValueError(f"Non-estimable recalibration fold {fold}")
            intercept_only, joint_intercept, joint_slope = fit_calibrators(
                train["outcome_imv"].to_numpy(int), train[model_id].to_numpy(float)
            )
            raw = test[model_id].to_numpy(float)
            test_lp = logit(np.clip(raw, EPSILON, 1 - EPSILON))
            test["raw_probability"] = raw
            test["crossfit_intercept_only_probability"] = expit(test_lp + intercept_only)
            test["crossfit_intercept_slope_probability"] = expit(
                joint_intercept + joint_slope * test_lp
            )
            parts.append(test)
            parameter_rows.append({
                "scope": scope,
                "database": "eicu",
                "model_id": model_id,
                "held_out_outer_fold": int(fold),
                "calibration_training_rows": len(train),
                "calibration_training_events": int(train["outcome_imv"].sum()),
                "intercept_only": intercept_only,
                "joint_intercept": joint_intercept,
                "joint_slope": joint_slope,
            })
        updated = pd.concat(parts, ignore_index=True).sort_values(
            ["outer_fold", "stay_key", "landmark_h"]
        )
        if len(updated) != len(frame) or updated.duplicated(["stay_key", "landmark_h"]).any():
            raise ValueError("Updated transport rows do not reconcile")
        restricted_columns = [
            "database", "patient_key", "stay_key", "hospital_id", "landmark_h",
            "outer_fold", "outcome_imv", *PROBABILITY_COLUMNS.values(),
        ]
        output_path = OUTPUT_DIR / f"{scope}_MIMIC_TO_EICU_{model_id}_CROSSFIT_RECALIBRATED_V0_1.parquet"
        updated[restricted_columns].to_parquet(output_path, index=False)
        os.chmod(output_path, 0o600)

        y_updated = updated["outcome_imv"].to_numpy(int)
        result_rows = []
        for update_type, column in PROBABILITY_COLUMNS.items():
            probability = updated[column].to_numpy(float)
            result_rows.append({
                "scope": scope,
                "development_database": "mimic",
                "transport_database": "eicu",
                "model_id": model_id,
                "update_type": update_type,
                "n_landmarks": len(updated),
                "events": int(y_updated.sum()),
                "prevalence": float(y_updated.mean()),
                **metric_set(y_updated, probability),
                **calibration_set(y_updated, probability),
            })
        pd.DataFrame(result_rows).to_csv(
            RESULT_DIR / f"{scope}_MIMIC_TO_EICU_{model_id}_CROSSFIT_RECALIBRATION_SUMMARY_V0_1.csv",
            index=False,
        )
        pd.DataFrame(parameter_rows).to_csv(
            RESULT_DIR / f"{scope}_MIMIC_TO_EICU_{model_id}_CROSSFIT_RECALIBRATION_PARAMETERS_V0_1.csv",
            index=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=["rolling", "fixed"], required=True)
    parser.add_argument("--bootstrap", type=int, default=1000)
    args = parser.parse_args()
    verify_manifest()
    frame = load_scope(args.scope)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(OUTPUT_DIR, 0o700)
    run_bootstrap(args.scope, frame, args.bootstrap)
    run_recalibration(args.scope, frame)


if __name__ == "__main__":
    main()
