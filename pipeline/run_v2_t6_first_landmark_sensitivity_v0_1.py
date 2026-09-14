#!/usr/bin/env python3
"""First-landmark-only sensitivity from frozen rolling OOF predictions."""

from __future__ import annotations

import argparse
import hashlib
import importlib
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
MANIFEST_PATH = ROOT / "06_models" / "V2_T6_FIRST_LANDMARK_MANIFEST_V0_1.json"
INPUT_DIR = ROOT / "restricted" / "t4_checkpoints"
OUTPUT_DIR = ROOT / "restricted" / "t6_sensitivity"
RESULT_DIR = ROOT / "07_results" / "t6_sensitivity"
MODELS = ("M0_LIU_ADAPTED", "M1_SNAPSHOT_CAT", "M2_DYNAMIC_CAT")
COMPARISONS = (("M1_SNAPSHOT_CAT", "M2_DYNAMIC_CAT"), ("M0_LIU_ADAPTED", "M2_DYNAMIC_CAT"))
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
    if manifest["status"] != "FROZEN_BEFORE_FIRST_FIRST_LANDMARK_PERFORMANCE_RESULT":
        raise RuntimeError("First-landmark manifest status is not frozen")
    if runtime_versions() != manifest["runtime"]:
        raise RuntimeError("First-landmark runtime differs from manifest")
    for relative, expected in manifest["sha256"].items():
        observed = sha256(ROOT / relative)
        if observed != expected:
            raise RuntimeError(f"SHA-256 mismatch for {relative}: {observed} != {expected}")


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
        "calibration_slope": float(joint.coef_[0, 0]),
    }


def load(database: str) -> pd.DataFrame:
    keys = [
        "database", "patient_key", "stay_key", "hospital_id", "landmark_h",
        "outer_fold", "outcome_imv",
    ]
    merged = None
    for model in MODELS:
        path = INPUT_DIR / f"{database}_{model}_OOF_V0_1.parquet"
        frame = pd.read_parquet(path, filters=[("landmark_h", "=", 24)])
        part = frame[keys + ["probability"]].rename(columns={"probability": model})
        if merged is None:
            merged = part
        else:
            merged = merged.merge(part, on=keys, how="inner", validate="one_to_one")
    assert merged is not None
    if not merged["landmark_h"].eq(24).all() or merged[list(MODELS)].isna().any().any():
        raise ValueError("Invalid first-landmark paired frame")
    return merged


def run(database: str, replicates: int) -> None:
    frame = load(database)
    y = frame["outcome_imv"].to_numpy(int)
    probabilities = {model: frame[model].to_numpy(float) for model in MODELS}
    estimates = {model: metric_set(y, p) for model, p in probabilities.items()}
    summaries = []
    for model in MODELS:
        summaries.append({
            "database": database,
            "model_id": model,
            "n_landmarks": len(frame),
            "events": int(y.sum()),
            "prevalence": float(y.mean()),
            **estimates[model],
            **calibration_set(y, probabilities[model]),
        })
    pd.DataFrame(summaries).to_csv(
        RESULT_DIR / f"{database}_FIRST_LANDMARK_RAW_SUMMARY_V0_1.csv", index=False
    )

    point_rows = []
    for reference, candidate in COMPARISONS:
        for metric in estimates[reference]:
            point_rows.append({
                "database": database, "reference": reference, "candidate": candidate,
                "metric": metric,
                "reference_estimate": estimates[reference][metric],
                "candidate_estimate": estimates[candidate][metric],
                "candidate_minus_reference": estimates[candidate][metric] - estimates[reference][metric],
                "n_landmarks": len(frame), "events": int(y.sum()), "prevalence": float(y.mean()),
            })
    point = pd.DataFrame(point_rows)
    codes, groups = pd.factorize(
        frame["patient_key"] if database == "mimic" else frame["hospital_id"], sort=True
    )
    rng = np.random.default_rng(SEED)
    records = []
    for replicate in range(1, replicates + 1):
        sampled = rng.integers(0, len(groups), size=len(groups))
        group_weights = np.bincount(sampled, minlength=len(groups)).astype(float)
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
                    "replicate": replicate, "reference": reference, "candidate": candidate,
                    "metric": metric,
                    "reference_estimate": sampled_metrics[reference][metric],
                    "candidate_estimate": sampled_metrics[candidate][metric],
                    "candidate_minus_reference": sampled_metrics[candidate][metric] - sampled_metrics[reference][metric],
                })
    bootstrap = pd.DataFrame(records)
    bootstrap_path = OUTPUT_DIR / f"{database}_FIRST_LANDMARK_CLUSTER_BOOTSTRAP_V0_1.parquet"
    bootstrap.to_parquet(bootstrap_path, index=False)
    os.chmod(bootstrap_path, 0o600)
    ci = bootstrap.groupby(["reference", "candidate", "metric"], as_index=False).agg(
        difference_ci_low=("candidate_minus_reference", lambda x: x.quantile(0.025)),
        difference_ci_high=("candidate_minus_reference", lambda x: x.quantile(0.975)),
        bootstrap_replicates=("replicate", "nunique"),
    )
    point.merge(ci, on=["reference", "candidate", "metric"], validate="one_to_one").to_csv(
        RESULT_DIR / f"{database}_FIRST_LANDMARK_CLUSTER_BOOTSTRAP_CI_V0_1.csv", index=False
    )

    utility_base = importlib.import_module("run_v2_t4_calibration_utility_v0_1")
    utility_rows = []
    for model in MODELS:
        work = frame[["stay_key", "landmark_h", "outcome_imv", model]].rename(
            columns={model: "probability"}
        )
        utility_rows.extend(utility_base.utility_rows(work, database, model))
    pd.DataFrame(utility_rows).to_csv(
        RESULT_DIR / f"{database}_FIRST_LANDMARK_DCA_ALERT_BURDEN_V0_1.csv", index=False
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", type=int, default=1000)
    args = parser.parse_args()
    verify_manifest()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(OUTPUT_DIR, 0o700)
    for database in ("mimic", "eicu"):
        run(database, args.bootstrap)


if __name__ == "__main__":
    main()
