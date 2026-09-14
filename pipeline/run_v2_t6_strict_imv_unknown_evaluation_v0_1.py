#!/usr/bin/env python3
"""Evaluate frozen strict-IMV and Unknown scenarios from reconstructed extended OOF."""

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
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "06_models" / "V2_T6_STRICT_IMV_UNKNOWN_MANIFEST_V0_1.json"
EXTENDED_DIR = ROOT / "restricted" / "t6_strict_imv_unknown"
RESULT_DIR = ROOT / "07_results" / "t6_sensitivity"
M1 = "M1_SNAPSHOT_CAT"
M2 = "M2_DYNAMIC_CAT"
SEED = 20260909
MAX_RECONSTRUCTION_ERROR = 1e-12
EXPECTED_SCENARIOS = {
    "mimic": {
        "MIMIC_PRIMARY_A_VS_NONE": (232316, 5765),
        "MIMIC_UNKNOWN_AS_NEGATIVE": (233319, 5765),
        "MIMIC_UNKNOWN_AS_POSITIVE": (233319, 6768),
    },
    "eicu": {
        "EICU_PRIMARY_ABC_VS_NONE": (568182, 4384),
        "EICU_STRICT_AB_VS_NONE": (565670, 1872),
        "EICU_DISTINCT_TIME_VS_NONE": (568081, 4283),
        "EICU_UNKNOWN_AS_NEGATIVE": (572055, 4384),
        "EICU_UNKNOWN_AS_POSITIVE": (572055, 8257),
    },
}


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


def verify_manifest() -> None:
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


def metric_set(y: np.ndarray, probability: np.ndarray, weight=None) -> dict[str, float]:
    return {
        "auprc": float(average_precision_score(y, probability, sample_weight=weight)),
        "auroc": float(roc_auc_score(y, probability, sample_weight=weight)),
        "brier": float(np.average((y - probability) ** 2, weights=weight)),
        "logloss": float(log_loss(y, probability, sample_weight=weight, labels=[0, 1])),
    }


def load_extended(database: str) -> pd.DataFrame:
    identity = [
        "database", "patient_key", "stay_key", "hospital_id", "landmark_h", "outer_fold",
        "outcome_imv", "evidence_grade",
    ]
    if database == "eicu":
        identity.append("outcome_imv_distinct_time")
    merged = None
    expected_rows = 233319 if database == "mimic" else 572055
    for model in (M1, M2):
        path = EXTENDED_DIR / f"{database}_{model}_EXTENDED_OOF_V0_1.parquet"
        frame = pd.read_parquet(path)
        if len(frame) != expected_rows or frame.duplicated(["stay_key", "landmark_h"]).any():
            raise ValueError(f"Invalid extended OOF for {database} {model}")
        primary = frame["outcome_imv"].isin([0, 1])
        if frame.loc[primary, "primary_abs_error"].max() > MAX_RECONSTRUCTION_ERROR:
            raise ValueError(f"Primary reconstruction mismatch for {database} {model}")
        frame = frame[identity + ["probability"]].rename(columns={"probability": model})
        merged = frame if merged is None else merged.merge(frame, on=identity, how="inner", validate="one_to_one")
    if len(merged) != expected_rows or merged[[M1, M2]].isna().any().any():
        raise ValueError(f"M1/M2 extended OOF rows do not reconcile for {database}")
    return merged


def scenario_frames(database: str, frame: pd.DataFrame):
    if database == "mimic":
        definitions = {
            "MIMIC_PRIMARY_A_VS_NONE": (
                frame["evidence_grade"].isin(["A", "None"]), frame["evidence_grade"].eq("A")
            ),
            "MIMIC_UNKNOWN_AS_NEGATIVE": (
                pd.Series(True, index=frame.index), frame["evidence_grade"].eq("A")
            ),
            "MIMIC_UNKNOWN_AS_POSITIVE": (
                pd.Series(True, index=frame.index), frame["evidence_grade"].isin(["A", "Unknown"])
            ),
        }
    else:
        definitions = {
            "EICU_PRIMARY_ABC_VS_NONE": (
                frame["evidence_grade"].isin(["A", "B", "C", "None"]),
                frame["evidence_grade"].isin(["A", "B", "C"]),
            ),
            "EICU_STRICT_AB_VS_NONE": (
                frame["evidence_grade"].isin(["A", "B", "None"]),
                frame["evidence_grade"].isin(["A", "B"]),
            ),
            "EICU_DISTINCT_TIME_VS_NONE": (
                frame["outcome_imv_distinct_time"].isin([0, 1]),
                frame["outcome_imv_distinct_time"].eq(1),
            ),
            "EICU_UNKNOWN_AS_NEGATIVE": (
                pd.Series(True, index=frame.index), frame["evidence_grade"].isin(["A", "B", "C"])
            ),
            "EICU_UNKNOWN_AS_POSITIVE": (
                pd.Series(True, index=frame.index),
                frame["evidence_grade"].isin(["A", "B", "C", "Unknown"]),
            ),
        }
    for name, (include, positive) in definitions.items():
        subset = frame.loc[include].copy()
        subset["outcome_imv"] = positive.loc[include].astype(int).to_numpy()
        expected_n, expected_events = EXPECTED_SCENARIOS[database][name]
        if len(subset) != expected_n or int(subset["outcome_imv"].sum()) != expected_events:
            raise ValueError(
                f"Scenario reconciliation failed for {name}: n={len(subset)}, events={subset['outcome_imv'].sum()}"
            )
        yield name, subset


def evaluate_scenario(database: str, scenario: str, frame: pd.DataFrame, replicates: int):
    y = frame["outcome_imv"].to_numpy(int)
    probabilities = {model: frame[model].to_numpy(float) for model in (M1, M2)}
    estimates = {model: metric_set(y, probability) for model, probability in probabilities.items()}
    group_column = "patient_key" if database == "mimic" else "hospital_id"
    n_groups = int(frame[group_column].nunique())
    point_rows = []
    for metric in estimates[M1]:
        point_rows.append({
            "database": database,
            "scenario": scenario,
            "reference": M1,
            "candidate": M2,
            "metric": metric,
            "reference_estimate": estimates[M1][metric],
            "candidate_estimate": estimates[M2][metric],
            "candidate_minus_reference": estimates[M2][metric] - estimates[M1][metric],
            "cluster": group_column,
            "n_groups": n_groups,
            "n_stays": int(frame["stay_key"].nunique()),
            "n_landmarks": len(frame),
            "events": int(y.sum()),
            "prevalence": float(y.mean()),
        })

    codes, groups = pd.factorize(frame[group_column], sort=True)
    scenario_seed = SEED + list(EXPECTED_SCENARIOS[database]).index(scenario) * 10000
    rng = np.random.default_rng(scenario_seed)
    bootstrap_rows = []
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
            bootstrap_rows.append({
                "database": database,
                "scenario": scenario,
                "replicate": replicate,
                "metric": metric,
                "reference_estimate": sampled_metrics[M1][metric],
                "candidate_estimate": sampled_metrics[M2][metric],
                "candidate_minus_reference": sampled_metrics[M2][metric] - sampled_metrics[M1][metric],
            })
    bootstrap = pd.DataFrame(bootstrap_rows)
    ci = bootstrap.groupby(["database", "scenario", "metric"], as_index=False).agg(
        reference_ci_low=("reference_estimate", lambda x: x.quantile(0.025)),
        reference_ci_high=("reference_estimate", lambda x: x.quantile(0.975)),
        candidate_ci_low=("candidate_estimate", lambda x: x.quantile(0.025)),
        candidate_ci_high=("candidate_estimate", lambda x: x.quantile(0.975)),
        difference_ci_low=("candidate_minus_reference", lambda x: x.quantile(0.025)),
        difference_ci_high=("candidate_minus_reference", lambda x: x.quantile(0.975)),
        bootstrap_replicates=("replicate", "nunique"),
    )
    point = pd.DataFrame(point_rows).merge(
        ci, on=["database", "scenario", "metric"], how="left", validate="one_to_one"
    )

    utility = importlib.import_module("run_v2_t4_calibration_utility_v0_1")
    calibration_rows = []
    for model in (M1, M2):
        model_frame = frame[
            ["database", "patient_key", "stay_key", "hospital_id", "landmark_h", "outer_fold", "outcome_imv", model]
        ].rename(columns={model: "probability"})
        row = utility.calibration_summary(model_frame, database, model)
        row["scenario"] = scenario
        row["n_groups"] = n_groups
        calibration_rows.append(row)
    m2_frame = frame[["stay_key", "landmark_h", "outcome_imv", M2]].rename(columns={M2: "probability"})
    utility_rows = utility.utility_rows(m2_frame, database, M2)
    for row in utility_rows:
        row["scenario"] = scenario
        row["n_groups"] = n_groups
    return point, bootstrap, pd.DataFrame(calibration_rows), pd.DataFrame(utility_rows)


def run(database: str, replicates: int) -> None:
    frame = load_extended(database)
    points, bootstraps, calibrations, utilities = [], [], [], []
    for scenario, subset in scenario_frames(database, frame):
        point, bootstrap, calibration, utility = evaluate_scenario(
            database, scenario, subset, replicates
        )
        points.append(point)
        bootstraps.append(bootstrap)
        calibrations.append(calibration)
        utilities.append(utility)
    bootstrap = pd.concat(bootstraps, ignore_index=True)
    bootstrap_path = EXTENDED_DIR / f"{database}_STRICT_UNKNOWN_M2_VS_M1_BOOTSTRAP_V0_1.parquet"
    bootstrap.to_parquet(bootstrap_path, index=False)
    os.chmod(bootstrap_path, 0o600)
    pd.concat(points, ignore_index=True).to_csv(
        RESULT_DIR / f"{database}_STRICT_UNKNOWN_M2_VS_M1_BOOTSTRAP_CI_V0_1.csv", index=False
    )
    pd.concat(calibrations, ignore_index=True).to_csv(
        RESULT_DIR / f"{database}_STRICT_UNKNOWN_RAW_CALIBRATION_V0_1.csv", index=False
    )
    pd.concat(utilities, ignore_index=True).to_csv(
        RESULT_DIR / f"{database}_STRICT_UNKNOWN_M2_DCA_ALERT_BURDEN_V0_1.csv", index=False
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", choices=["mimic", "eicu"], required=True)
    parser.add_argument("--bootstrap", type=int, default=1000)
    args = parser.parse_args()
    if args.bootstrap <= 0:
        raise ValueError("Bootstrap replicate count must be positive")
    verify_manifest()
    OUTPUT_DIR = EXTENDED_DIR
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(OUTPUT_DIR, 0o700)
    run(args.database, args.bootstrap)


if __name__ == "__main__":
    main()
