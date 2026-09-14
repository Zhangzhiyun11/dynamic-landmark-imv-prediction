#!/usr/bin/env python3
"""Locked interface-complete hospital sensitivity using raw eICU OOF probabilities."""

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
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "06_models" / "V2_T6_INTERFACE_COMPLETE_HOSPITALS_MANIFEST_V0_1.json"
PREDICTOR_PATH = ROOT / "restricted" / "V2_PREDICTOR_MATRIX_V0_1.parquet"
OOF_DIR = ROOT / "restricted" / "t4_checkpoints"
RESTRICTED_DIR = ROOT / "restricted" / "t6_interface_complete"
RESULT_DIR = ROOT / "07_results" / "t6_sensitivity"
M1 = "M1_SNAPSHOT_CAT"
M2 = "M2_DYNAMIC_CAT"
FLAGS = (
    "oxygen_interface_supported",
    "urine_interface_supported",
    "vaso_interface_supported",
)
SEED = 20260909


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
    expected = "FROZEN_BEFORE_FIRST_INTERFACE_COMPLETE_OUTCOME_LINKED_RESULT"
    if manifest["status"] != expected:
        raise RuntimeError("Interface-complete manifest status is not frozen")
    if runtime_versions() != manifest["runtime"]:
        raise RuntimeError("Interface-complete runtime differs from manifest")
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


def complete_hospitals() -> set[int]:
    columns = ["database", "hospital_id", *FLAGS]
    frame = pd.read_parquet(PREDICTOR_PATH, columns=columns)
    frame = frame.loc[frame["database"].eq("eICU-CRD v2.0")].copy()
    if len(frame) != 572055 or frame["hospital_id"].nunique() != 208:
        raise ValueError("Unexpected eICU predictor matrix size")
    grouped = frame.groupby("hospital_id", sort=True)[list(FLAGS)].agg(["min", "max"])
    for flag in FLAGS:
        if not grouped[(flag, "min")].eq(grouped[(flag, "max")]).all():
            raise ValueError(f"Interface flag is not hospital-constant: {flag}")
    eligible = grouped.index[
        np.logical_and.reduce([grouped[(flag, "min")].eq(1) for flag in FLAGS])
    ]
    if len(eligible) != 96:
        raise ValueError("Outcome-blind complete-hospital count differs from lock")
    return set(int(value) for value in eligible)


def load_subset() -> pd.DataFrame:
    keys = [
        "database", "patient_key", "stay_key", "hospital_id", "landmark_h",
        "outer_fold", "outcome_imv",
    ]
    merged = None
    expected_rows = None
    for model in (M1, M2):
        frame = pd.read_parquet(OOF_DIR / f"eicu_{model}_OOF_V0_1.parquet")
        frame = frame[keys + ["probability"]].rename(columns={"probability": model})
        expected_rows = len(frame) if expected_rows is None else expected_rows
        if len(frame) != expected_rows:
            raise ValueError("M1/M2 OOF row count differs before merge")
        merged = frame if merged is None else merged.merge(
            frame, on=keys, how="inner", validate="one_to_one"
        )
    if len(merged) != expected_rows or merged[[M1, M2]].isna().any().any():
        raise ValueError("M1/M2 OOF rows do not reconcile")
    subset = merged.loc[merged["hospital_id"].isin(complete_hospitals())].copy()
    if subset.empty or subset["hospital_id"].nunique() != 96:
        raise ValueError("Interface-complete OOF subset is not estimable")
    if subset["outcome_imv"].nunique() != 2:
        raise ValueError("Interface-complete outcome is not binary-estimable")
    return subset


def run(replicates: int) -> None:
    frame = load_subset()
    y = frame["outcome_imv"].to_numpy(int)
    probabilities = {model: frame[model].to_numpy(float) for model in (M1, M2)}
    estimates = {model: metric_set(y, probability) for model, probability in probabilities.items()}

    point_rows = []
    for metric in estimates[M1]:
        point_rows.append({
            "database": "eicu",
            "sensitivity": "interface_complete_hospitals",
            "reference": M1,
            "candidate": M2,
            "metric": metric,
            "reference_estimate": estimates[M1][metric],
            "candidate_estimate": estimates[M2][metric],
            "candidate_minus_reference": estimates[M2][metric] - estimates[M1][metric],
            "n_hospitals": int(frame["hospital_id"].nunique()),
            "n_landmarks": len(frame),
            "events": int(y.sum()),
            "prevalence": float(y.mean()),
        })
    point = pd.DataFrame(point_rows)

    codes, groups = pd.factorize(frame["hospital_id"], sort=True)
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
        for metric in sampled_metrics[M1]:
            records.append({
                "replicate": replicate,
                "metric": metric,
                "reference_estimate": sampled_metrics[M1][metric],
                "candidate_estimate": sampled_metrics[M2][metric],
                "candidate_minus_reference": sampled_metrics[M2][metric] - sampled_metrics[M1][metric],
            })
    bootstrap = pd.DataFrame(records)
    bootstrap_path = RESTRICTED_DIR / "eicu_INTERFACE_COMPLETE_M2_VS_M1_BOOTSTRAP_V0_1.parquet"
    bootstrap.to_parquet(bootstrap_path, index=False)
    os.chmod(bootstrap_path, 0o600)
    ci = bootstrap.groupby("metric", as_index=False).agg(
        reference_ci_low=("reference_estimate", lambda x: x.quantile(0.025)),
        reference_ci_high=("reference_estimate", lambda x: x.quantile(0.975)),
        candidate_ci_low=("candidate_estimate", lambda x: x.quantile(0.025)),
        candidate_ci_high=("candidate_estimate", lambda x: x.quantile(0.975)),
        difference_ci_low=("candidate_minus_reference", lambda x: x.quantile(0.025)),
        difference_ci_high=("candidate_minus_reference", lambda x: x.quantile(0.975)),
        bootstrap_replicates=("replicate", "nunique"),
    )
    point.merge(ci, on="metric", validate="one_to_one").to_csv(
        RESULT_DIR / "eicu_INTERFACE_COMPLETE_M2_VS_M1_BOOTSTRAP_CI_V0_1.csv", index=False
    )

    utility = importlib.import_module("run_v2_t4_calibration_utility_v0_1")
    calibration_rows = []
    for model in (M1, M2):
        model_frame = frame[
            ["database", "patient_key", "stay_key", "hospital_id", "landmark_h", "outer_fold", "outcome_imv", model]
        ].rename(columns={model: "probability"})
        row = utility.calibration_summary(model_frame, "eicu", model)
        row["n_hospitals"] = int(frame["hospital_id"].nunique())
        calibration_rows.append(row)
    pd.DataFrame(calibration_rows).to_csv(
        RESULT_DIR / "eicu_INTERFACE_COMPLETE_RAW_CALIBRATION_V0_1.csv", index=False
    )
    m2_frame = frame[
        ["stay_key", "landmark_h", "outcome_imv", M2]
    ].rename(columns={M2: "probability"})
    pd.DataFrame(utility.utility_rows(m2_frame, "eicu", M2)).to_csv(
        RESULT_DIR / "eicu_INTERFACE_COMPLETE_M2_DCA_ALERT_BURDEN_V0_1.csv", index=False
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", type=int, default=1000)
    args = parser.parse_args()
    verify_manifest()
    RESTRICTED_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(RESTRICTED_DIR, 0o700)
    run(args.bootstrap)


if __name__ == "__main__":
    main()
