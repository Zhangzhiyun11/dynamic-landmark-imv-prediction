#!/usr/bin/env python3
"""Locked M3 OOF comparison, calibration, utility, and stability audit."""

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
MANIFEST_PATH = ROOT / "06_models" / "V2_T4_M3_EVALUATION_MANIFEST_V0_1.json"
M3_DIR = ROOT / "restricted" / "t4_m3_checkpoints_v0_2"
PRIMARY_DIR = ROOT / "restricted" / "t4_checkpoints"
RESTRICTED_DIR = ROOT / "restricted" / "t4_m3_evaluation"
RESULT_DIR = ROOT / "07_results" / "t4_m3"
M3 = "M3_DYNAMIC_EN"
COMPARISONS = ((M3, "M2_DYNAMIC_CAT"), ("M1_SNAPSHOT_CAT", M3))
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
    expected = "FROZEN_AFTER_COMPLETE_RAW_M3_OOF_BEFORE_FIRST_M3_COMPARISON_RESULT"
    if manifest["status"] != expected:
        raise RuntimeError("M3 evaluation manifest status is not frozen")
    if runtime_versions() != manifest["runtime"]:
        raise RuntimeError("M3 evaluation runtime differs from manifest")
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


def load_predictions(database: str) -> pd.DataFrame:
    keys = [
        "database", "patient_key", "stay_key", "hospital_id", "landmark_h",
        "outer_fold", "outcome_imv",
    ]
    paths = {
        M3: M3_DIR / f"{database}_{M3}_OOF_V0_2.parquet",
        "M2_DYNAMIC_CAT": PRIMARY_DIR / f"{database}_M2_DYNAMIC_CAT_OOF_V0_1.parquet",
        "M1_SNAPSHOT_CAT": PRIMARY_DIR / f"{database}_M1_SNAPSHOT_CAT_OOF_V0_1.parquet",
    }
    merged = None
    expected_rows = None
    for model_id, path in paths.items():
        frame = pd.read_parquet(path)[keys + ["probability"]]
        expected_rows = len(frame) if expected_rows is None else expected_rows
        if len(frame) != expected_rows:
            raise ValueError(f"Row count differs before merge for {database} {model_id}")
        frame = frame.rename(columns={"probability": model_id})
        merged = frame if merged is None else merged.merge(
            frame, on=keys, how="inner", validate="one_to_one"
        )
    if len(merged) != expected_rows or merged[[M3, "M2_DYNAMIC_CAT", "M1_SNAPSHOT_CAT"]].isna().any().any():
        raise ValueError(f"M3 paired rows do not reconcile for {database}")
    return merged


def evaluate_database(database: str, replicates: int) -> None:
    frame = load_predictions(database)
    y = frame["outcome_imv"].to_numpy(int)
    probabilities = {
        model: frame[model].to_numpy(float)
        for model in (M3, "M2_DYNAMIC_CAT", "M1_SNAPSHOT_CAT")
    }
    estimates = {model: metric_set(y, p) for model, p in probabilities.items()}
    point_rows = []
    for reference, candidate in COMPARISONS:
        for metric in estimates[reference]:
            point_rows.append({
                "database": database,
                "reference": reference,
                "candidate": candidate,
                "metric": metric,
                "reference_estimate": estimates[reference][metric],
                "candidate_estimate": estimates[candidate][metric],
                "candidate_minus_reference": estimates[candidate][metric] - estimates[reference][metric],
                "n_landmarks": len(frame),
                "events": int(y.sum()),
                "prevalence": float(y.mean()),
            })
    point = pd.DataFrame(point_rows)

    group = frame["patient_key"] if database == "mimic" else frame["hospital_id"]
    codes, groups = pd.factorize(group, sort=True)
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
                    "replicate": replicate,
                    "reference": reference,
                    "candidate": candidate,
                    "metric": metric,
                    "reference_estimate": sampled_metrics[reference][metric],
                    "candidate_estimate": sampled_metrics[candidate][metric],
                    "candidate_minus_reference": sampled_metrics[candidate][metric] - sampled_metrics[reference][metric],
                })
    bootstrap = pd.DataFrame(records)
    restricted_path = RESTRICTED_DIR / f"{database}_M3_COMPARISONS_CLUSTER_BOOTSTRAP_V0_1.parquet"
    bootstrap.to_parquet(restricted_path, index=False)
    os.chmod(restricted_path, 0o600)
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
        RESULT_DIR / f"{database}_M3_COMPARISONS_CLUSTER_BOOTSTRAP_CI_V0_1.csv", index=False
    )

    utility = importlib.import_module("run_v2_t4_calibration_utility_v0_1")
    m3_frame = frame[
        ["database", "patient_key", "stay_key", "hospital_id", "landmark_h", "outer_fold", "outcome_imv", M3]
    ].rename(columns={M3: "probability"})
    pd.DataFrame([utility.calibration_summary(m3_frame, database, M3)]).to_csv(
        RESULT_DIR / f"{database}_M3_RAW_CALIBRATION_SUMMARY_V0_1.csv", index=False
    )
    utility.calibration_bins(m3_frame, database, M3).to_csv(
        RESULT_DIR / f"{database}_M3_RAW_CALIBRATION_BINS_V0_1.csv", index=False
    )
    pd.DataFrame(utility.utility_rows(m3_frame, database, M3)).to_csv(
        RESULT_DIR / f"{database}_M3_DCA_ALERT_BURDEN_V0_1.csv", index=False
    )

    fold_rows = []
    for outer_fold, fold in m3_frame.groupby("outer_fold", sort=True):
        fold_y = fold["outcome_imv"].to_numpy(int)
        fold_p = fold["probability"].to_numpy(float)
        fold_rows.append({
            "database": database,
            "model_id": M3,
            "outer_fold": int(outer_fold),
            "n_landmarks": len(fold),
            "events": int(fold_y.sum()),
            "prevalence": float(fold_y.mean()),
            **metric_set(fold_y, fold_p),
        })
    pd.DataFrame(fold_rows).to_csv(
        RESULT_DIR / f"{database}_M3_OUTER_FOLD_PERFORMANCE_V0_1.csv", index=False
    )


def audit_stability(database: str) -> None:
    tuning = pd.read_csv(RESULT_DIR / f"{database}_{M3}_TUNING_V0_2.csv")
    selected = tuning.loc[tuning["selected"].eq(1)].copy()
    predictions = pd.read_parquet(M3_DIR / f"{database}_{M3}_OOF_V0_2.parquet")
    final_audit = predictions.groupby("outer_fold", as_index=False).agg(
        final_n_iter=("final_n_iter", "first"),
        final_convergence_warning=("final_convergence_warning", "first"),
        selected_config_id_from_predictions=("selected_config_id", "first"),
    )
    selected = selected.merge(final_audit, on="outer_fold", validate="one_to_one")
    if not selected["config_id"].eq(selected["selected_config_id_from_predictions"]).all():
        raise ValueError(f"Selected M3 config mismatch for {database}")
    selected.to_csv(RESULT_DIR / f"{database}_M3_SELECTED_CONFIG_AUDIT_V0_1.csv", index=False)

    coefficients = pd.read_csv(RESULT_DIR / f"{database}_{M3}_COEFFICIENTS_V0_2.csv")
    coefficients["is_nonzero"] = coefficients["coefficient"].ne(0)
    coefficients["abs_coefficient"] = coefficients["coefficient"].abs()

    def sign_consistency(values: pd.Series) -> float:
        nonzero = values.loc[values.ne(0)]
        if nonzero.empty:
            return np.nan
        positive = float(nonzero.gt(0).mean())
        return max(positive, 1 - positive)

    stability = coefficients.groupby("feature", as_index=False).agg(
        folds_present=("outer_fold", "nunique"),
        nonzero_folds=("is_nonzero", "sum"),
        median_coefficient=("coefficient", "median"),
        coefficient_q25=("coefficient", lambda x: x.quantile(0.25)),
        coefficient_q75=("coefficient", lambda x: x.quantile(0.75)),
        median_abs_coefficient=("abs_coefficient", "median"),
        sign_consistency_nonzero=("coefficient", sign_consistency),
    )
    stability["nonzero_selection_frequency"] = stability["nonzero_folds"] / stability["folds_present"]
    stability.insert(0, "model_id", M3)
    stability.insert(0, "database", database)
    stability.sort_values(
        ["nonzero_selection_frequency", "median_abs_coefficient", "feature"],
        ascending=[False, False, True],
    ).to_csv(RESULT_DIR / f"{database}_M3_COEFFICIENT_STABILITY_V0_1.csv", index=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", type=int, default=1000)
    args = parser.parse_args()
    verify_manifest()
    RESTRICTED_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(RESTRICTED_DIR, 0o700)
    for database in ("mimic", "eicu"):
        evaluate_database(database, args.bootstrap)
        audit_stability(database)


if __name__ == "__main__":
    main()
