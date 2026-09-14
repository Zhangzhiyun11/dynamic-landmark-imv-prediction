#!/usr/bin/env python3
"""Locked complete-case sensitivities for death/discharge before IMV documentation."""

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
MANIFEST_PATH = ROOT / "06_models" / "V2_T6_COMPETING_DEATH_DISCHARGE_MANIFEST_V0_1.json"
OOF_DIR = ROOT / "restricted" / "t4_checkpoints"
RESTRICTED_DIR = ROOT / "restricted" / "t6_competing_death_discharge"
RESULT_DIR = ROOT / "07_results" / "t6_sensitivity"
M1 = "M1_SNAPSHOT_CAT"
M2 = "M2_DYNAMIC_CAT"
SEED = 20260909
SCENARIOS = (
    "PRIMARY_COMPETING_AS_OBSERVED_NONE",
    "DEATH_CENSORED",
    "DISCHARGE_CENSORED",
    "ANY_COMPETING_CENSORED",
)
EXPECTED_PRIMARY = {"mimic": (232316, 5765), "eicu": (568182, 4384)}


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
    expected = "FROZEN_BEFORE_FIRST_COMPETING_EVENT_PERFORMANCE_RESULT"
    if manifest["status"] != expected:
        raise RuntimeError("Competing-event manifest status is not frozen")
    if runtime_versions() != manifest["runtime"]:
        raise RuntimeError("Competing-event runtime differs from manifest")
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


def load_outcome(database: str) -> pd.DataFrame:
    if database == "mimic":
        path = ROOT / "restricted" / "V2_MIMIC_ROLLING_OUTCOME_V0_1.parquet"
        columns = [
            "stay_id", "landmark_h", "horizon_h", "evidence_grade", "outcome_imv",
            "first_strict_start_offset_h", "competing_event_type", "death_offset_h",
            "icu_discharge_offset_h",
        ]
        outcome = pd.read_parquet(path, columns=columns)
        outcome["stay_key"] = outcome["stay_id"].astype(str)
        evidence_time = outcome["first_strict_start_offset_h"].astype(float)
    else:
        path = ROOT / "restricted" / "V2_EICU_ROLLING_OUTCOME_V0_1.parquet"
        columns = [
            "icu_stay_key", "landmark_h", "horizon_h", "evidence_grade", "outcome_imv",
            "first_strict_offset_h", "first_anchor_offset_h", "first_treatment_offset_h",
            "competing_event_type", "death_offset_h", "icu_discharge_offset_h",
        ]
        outcome = pd.read_parquet(path, columns=columns)
        outcome["stay_key"] = outcome["icu_stay_key"].astype(str)
        evidence_time = pd.Series(
            np.select(
                [
                    outcome["evidence_grade"].eq("A"),
                    outcome["evidence_grade"].eq("B"),
                    outcome["evidence_grade"].eq("C"),
                ],
                [
                    outcome["first_strict_offset_h"],
                    outcome["first_anchor_offset_h"],
                    outcome["first_treatment_offset_h"],
                ],
                default=np.nan,
            ),
            index=outcome.index,
            dtype=float,
        )
    outcome = outcome.loc[outcome["horizon_h"].eq(24) & outcome["outcome_imv"].isin([0, 1])].copy()
    evidence_time = evidence_time.loc[outcome.index]
    death_in = outcome["death_offset_h"].gt(outcome["landmark_h"]) & outcome["death_offset_h"].le(
        outcome["landmark_h"] + 24
    )
    discharge_in = outcome["icu_discharge_offset_h"].gt(outcome["landmark_h"]) & outcome[
        "icu_discharge_offset_h"
    ].le(outcome["landmark_h"] + 24)
    death_earlier = death_in & (evidence_time.isna() | outcome["death_offset_h"].lt(evidence_time))
    discharge_earlier = discharge_in & (
        evidence_time.isna() | outcome["icu_discharge_offset_h"].lt(evidence_time)
    )
    same = death_earlier & discharge_earlier & outcome["death_offset_h"].sub(
        outcome["icu_discharge_offset_h"]
    ).abs().lt(1 / 60)
    outcome["competing_event_type_corrected"] = np.select(
        [same, death_earlier, discharge_earlier],
        ["both_same_time", "death", "ICU discharge"],
        default="none",
    )
    expected_n, expected_events = EXPECTED_PRIMARY[database]
    if len(outcome) != expected_n or int(outcome["outcome_imv"].sum()) != expected_events:
        raise ValueError(f"Primary outcome reconciliation failed for {database}")
    if outcome.duplicated(["stay_key", "landmark_h"]).any():
        raise ValueError(f"Outcome keys are not unique for {database}")
    return outcome[
        [
            "stay_key", "landmark_h", "outcome_imv", "evidence_grade",
            "competing_event_type", "competing_event_type_corrected",
        ]
    ]


def load_predictions(database: str) -> pd.DataFrame:
    keys = [
        "database", "patient_key", "stay_key", "hospital_id", "landmark_h", "outer_fold",
        "outcome_imv",
    ]
    merged = None
    for model in (M1, M2):
        frame = pd.read_parquet(OOF_DIR / f"{database}_{model}_OOF_V0_1.parquet")
        frame = frame[keys + ["probability"]].rename(columns={"probability": model})
        merged = frame if merged is None else merged.merge(frame, on=keys, how="inner", validate="one_to_one")
    expected_n, expected_events = EXPECTED_PRIMARY[database]
    if len(merged) != expected_n or int(merged["outcome_imv"].sum()) != expected_events:
        raise ValueError(f"M1/M2 OOF reconciliation failed for {database}")
    outcome = load_outcome(database).rename(columns={"outcome_imv": "outcome_imv_source"})
    merged = merged.merge(outcome, on=["stay_key", "landmark_h"], how="inner", validate="one_to_one")
    if len(merged) != expected_n or not merged["outcome_imv"].eq(merged["outcome_imv_source"]).all():
        raise ValueError(f"OOF/outcome label mismatch for {database}")
    return merged.drop(columns="outcome_imv_source")


def scenario_frames(frame: pd.DataFrame):
    event = frame["competing_event_type_corrected"]
    include = {
        "PRIMARY_COMPETING_AS_OBSERVED_NONE": pd.Series(True, index=frame.index),
        "DEATH_CENSORED": ~event.isin(["death", "both_same_time"]),
        "DISCHARGE_CENSORED": ~event.eq("ICU discharge"),
        "ANY_COMPETING_CENSORED": event.eq("none"),
    }
    for scenario in SCENARIOS:
        subset = frame.loc[include[scenario]].copy()
        if subset.empty or subset["outcome_imv"].nunique() != 2:
            raise ValueError(f"Scenario is not binary-estimable: {scenario}")
        yield scenario, subset


def evaluate_scenario(database: str, scenario: str, frame: pd.DataFrame, replicates: int):
    y = frame["outcome_imv"].to_numpy(int)
    probabilities = {model: frame[model].to_numpy(float) for model in (M1, M2)}
    estimates = {model: metric_set(y, probability) for model, probability in probabilities.items()}
    group_column = "patient_key" if database == "mimic" else "hospital_id"
    n_groups = int(frame[group_column].nunique())
    counts = frame["competing_event_type_corrected"].value_counts()
    point_rows = []
    for metric in estimates[M1]:
        point_rows.append({
            "database": database, "scenario": scenario, "reference": M1, "candidate": M2,
            "metric": metric, "reference_estimate": estimates[M1][metric],
            "candidate_estimate": estimates[M2][metric],
            "candidate_minus_reference": estimates[M2][metric] - estimates[M1][metric],
            "cluster": group_column, "n_groups": n_groups,
            "n_stays": int(frame["stay_key"].nunique()), "n_landmarks": len(frame),
            "events": int(y.sum()), "prevalence": float(y.mean()),
            "corrected_none": int(counts.get("none", 0)),
            "corrected_discharge": int(counts.get("ICU discharge", 0)),
            "corrected_death": int(counts.get("death", 0)),
            "corrected_both_same_time": int(counts.get("both_same_time", 0)),
        })

    codes, groups = pd.factorize(frame[group_column], sort=True)
    rng = np.random.default_rng(SEED + SCENARIOS.index(scenario) * 10000)
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
                "database": database, "scenario": scenario, "replicate": replicate,
                "metric": metric, "reference_estimate": sampled_metrics[M1][metric],
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
    frame = load_predictions(database)
    audit = pd.crosstab(
        frame["competing_event_type"], frame["competing_event_type_corrected"], dropna=False
    ).rename_axis(index="stored", columns="corrected").stack(future_stack=True).rename("n").reset_index()
    audit.insert(0, "database", database)
    audit.to_csv(RESULT_DIR / f"{database}_COMPETING_EVENT_RECONSTRUCTION_AUDIT_V0_1.csv", index=False)
    points, bootstraps, calibrations, utilities = [], [], [], []
    for scenario, subset in scenario_frames(frame):
        point, bootstrap, calibration, utility = evaluate_scenario(database, scenario, subset, replicates)
        points.append(point); bootstraps.append(bootstrap)
        calibrations.append(calibration); utilities.append(utility)
    bootstrap = pd.concat(bootstraps, ignore_index=True)
    bootstrap_path = RESTRICTED_DIR / f"{database}_COMPETING_M2_VS_M1_BOOTSTRAP_V0_1.parquet"
    bootstrap.to_parquet(bootstrap_path, index=False)
    os.chmod(bootstrap_path, 0o600)
    pd.concat(points, ignore_index=True).to_csv(
        RESULT_DIR / f"{database}_COMPETING_M2_VS_M1_BOOTSTRAP_CI_V0_1.csv", index=False
    )
    pd.concat(calibrations, ignore_index=True).to_csv(
        RESULT_DIR / f"{database}_COMPETING_RAW_CALIBRATION_V0_1.csv", index=False
    )
    pd.concat(utilities, ignore_index=True).to_csv(
        RESULT_DIR / f"{database}_COMPETING_M2_DCA_ALERT_BURDEN_V0_1.csv", index=False
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", choices=["mimic", "eicu"], required=True)
    parser.add_argument("--bootstrap", type=int, default=1000)
    args = parser.parse_args()
    if args.bootstrap <= 0:
        raise ValueError("Bootstrap replicate count must be positive")
    verify_manifest()
    RESTRICTED_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(RESTRICTED_DIR, 0o700)
    run(args.database, args.bootstrap)


if __name__ == "__main__":
    main()
