#!/usr/bin/env python3
"""M3 V0.2: exact frozen grid with warm C paths and inner checkpoints."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression


MODULE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE_DIR))
import run_v2_t4_m3_elastic_net_v0_1 as base  # noqa: E402


ROOT = base.ROOT
MANIFEST_PATH = ROOT / "06_models" / "V2_T4_M3_ELASTIC_NET_MANIFEST_V0_2.json"
OUTPUT_DIR = ROOT / "restricted" / "t4_m3_checkpoints_v0_2"
RESULT_DIR = ROOT / "07_results" / "t4_m3"


def verify_manifest() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    expected = "FROZEN_AFTER_V0_1_RUNTIME_ABORT_BEFORE_FIRST_M3_RESULT"
    if manifest["status"] != expected:
        raise RuntimeError("M3 V0.2 manifest status is not frozen")
    if base.runtime_versions() != manifest["runtime"]:
        raise RuntimeError("M3 V0.2 runtime differs from manifest")
    for relative, expected_hash in manifest["sha256"].items():
        observed = base.sha256(ROOT / relative)
        if observed != expected_hash:
            raise RuntimeError(f"SHA-256 mismatch for {relative}: {observed} != {expected_hash}")
    return manifest


def fit_warm_path(
    train_matrix, y_train: np.ndarray, valid_matrix, y_valid: np.ndarray, seed: int
) -> list[dict]:
    rows = []
    for l1_ratio in base.L1_RATIOS:
        model = LogisticRegression(
            solver="saga",
            penalty="elasticnet",
            l1_ratio=float(l1_ratio),
            C=float(base.CS[0]),
            max_iter=3000,
            tol=1e-4,
            class_weight=None,
            random_state=seed,
            n_jobs=1,
            warm_start=True,
        )
        for c_value in base.CS:
            model.set_params(C=float(c_value))
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", category=ConvergenceWarning)
                model.fit(train_matrix, y_train)
            warned = any(issubclass(item.category, ConvergenceWarning) for item in caught)
            probability = model.predict_proba(valid_matrix)[:, 1]
            metric = base.metrics(y_valid, probability)
            rows.append({
                "config_id": f"L1R{int(round(l1_ratio * 100)):03d}_C{str(c_value).replace('.', '')}",
                "l1_ratio": float(l1_ratio),
                "C": float(c_value),
                **metric,
                "n_iter": int(model.n_iter_[0]),
                "convergence_warning": int(warned),
                "nonzero_coefficients": int(np.count_nonzero(model.coef_)),
            })
    if len(rows) != 25:
        raise ValueError("Warm path did not evaluate all 25 configurations")
    return rows


def run(database: str, manifest: dict) -> None:
    module = importlib.import_module("run_v2_t4_nested_catboost_v0_3")
    base_manifest = module.load_and_verify_manifest()
    data, features, categorical = module.load_dataset(database, "M2_DYNAMIC_CAT", base_manifest)
    if len(features) != 140 or len(categorical) != 3:
        raise ValueError("M3 information set differs from frozen M2 set")
    group_column = "patient_key" if database == "mimic" else "hospital_id"
    grid = {item["config_id"]: item for item in base.config_grid()}
    seed = int(manifest["primary_seed"])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(OUTPUT_DIR, 0o700)
    prediction_parts = []
    tuning_parts = []
    coefficient_parts = []

    for outer_fold in sorted(data["outer_fold"].astype(int).unique()):
        prediction_checkpoint = OUTPUT_DIR / f"{database}_{base.MODEL_ID}_outer{outer_fold}_predictions.parquet"
        tuning_checkpoint = OUTPUT_DIR / f"{database}_{base.MODEL_ID}_outer{outer_fold}_tuning.csv"
        coefficient_checkpoint = OUTPUT_DIR / f"{database}_{base.MODEL_ID}_outer{outer_fold}_coefficients.csv"
        if prediction_checkpoint.exists() and tuning_checkpoint.exists() and coefficient_checkpoint.exists():
            prediction_parts.append(pd.read_parquet(prediction_checkpoint))
            tuning_parts.append(pd.read_csv(tuning_checkpoint))
            coefficient_parts.append(pd.read_csv(coefficient_checkpoint))
            continue

        outer_train = data.loc[data["outer_fold"].ne(outer_fold)].copy()
        outer_test = data.loc[data["outer_fold"].eq(outer_fold)].copy()
        if set(outer_train[group_column].astype(str)) & set(outer_test[group_column].astype(str)):
            raise ValueError(f"Outer group leakage in {database} fold {outer_fold}")
        outer_train["inner_fold"] = outer_train[group_column].map(
            lambda value: base.stable_inner_fold(value, seed)
        )
        fold_events = outer_train.groupby("inner_fold")["outcome_imv"].agg(["sum", "count"])
        if len(fold_events) != 3 or (fold_events["sum"] == 0).any():
            raise ValueError(f"Inner folds not estimable in {database} fold {outer_fold}")

        inner_frames = []
        for inner_fold in range(3):
            inner_checkpoint = OUTPUT_DIR / f"{database}_{base.MODEL_ID}_outer{outer_fold}_inner{inner_fold}_grid.csv"
            if inner_checkpoint.exists():
                inner_frame = pd.read_csv(inner_checkpoint)
                if len(inner_frame) != 25:
                    raise ValueError(f"Incomplete inner checkpoint: {inner_checkpoint}")
                inner_frames.append(inner_frame)
                continue
            inner_train = outer_train.loc[outer_train["inner_fold"].ne(inner_fold)]
            inner_valid = outer_train.loc[outer_train["inner_fold"].eq(inner_fold)]
            preprocessor, train_matrix = base.fit_preprocessor(inner_train, features, categorical)
            valid_matrix = base.transform(inner_valid, preprocessor)
            rows = fit_warm_path(
                train_matrix,
                inner_train["outcome_imv"].to_numpy(int),
                valid_matrix,
                inner_valid["outcome_imv"].to_numpy(int),
                seed + inner_fold,
            )
            inner_frame = pd.DataFrame(rows)
            inner_frame.insert(0, "inner_fold", inner_fold)
            inner_frame.insert(0, "outer_fold", int(outer_fold))
            inner_frame.insert(0, "model_id", base.MODEL_ID)
            inner_frame.insert(0, "database", database)
            inner_frame.to_csv(inner_checkpoint, index=False)
            inner_frames.append(inner_frame)

        inner_results = pd.concat(inner_frames, ignore_index=True)
        if len(inner_results) != 75:
            raise ValueError("Inner results do not contain 3 x 25 configurations")
        tuning_frame = inner_results.groupby(
            ["database", "model_id", "outer_fold", "config_id", "l1_ratio", "C"],
            as_index=False,
        ).agg(
            mean_auprc=("auprc", "mean"),
            mean_auroc=("auroc", "mean"),
            mean_logloss=("logloss", "mean"),
            max_n_iter=("n_iter", "max"),
            convergence_warning_fits=("convergence_warning", "sum"),
            median_nonzero_coefficients=("nonzero_coefficients", "median"),
        )
        winner = tuning_frame.sort_values(
            ["mean_auprc", "mean_logloss", "l1_ratio", "C", "config_id"],
            ascending=[False, True, False, True, True],
        ).iloc[0]
        tuning_frame["selected"] = tuning_frame["config_id"].eq(winner["config_id"]).astype(int)
        tuning_frame.to_csv(tuning_checkpoint, index=False)

        selected_config = grid[str(winner["config_id"])]
        preprocessor, train_matrix = base.fit_preprocessor(outer_train, features, categorical)
        test_matrix = base.transform(outer_test, preprocessor)
        final_model, warned = base.fit_logistic(
            train_matrix,
            outer_train["outcome_imv"].to_numpy(int),
            selected_config,
            seed,
        )
        probability = final_model.predict_proba(test_matrix)[:, 1]
        output = outer_test[
            ["database", "patient_key", "stay_key", "hospital_id", "landmark_h", "outer_fold", "outcome_imv"]
        ].copy()
        output["model_id"] = base.MODEL_ID
        output["probability"] = probability
        output["selected_config_id"] = str(winner["config_id"])
        output["final_n_iter"] = int(final_model.n_iter_[0])
        output["final_convergence_warning"] = int(warned)
        output.to_parquet(prediction_checkpoint, index=False)
        os.chmod(prediction_checkpoint, 0o600)
        coefficient_frame = pd.DataFrame({
            "database": database,
            "model_id": base.MODEL_ID,
            "outer_fold": int(outer_fold),
            "selected_config_id": str(winner["config_id"]),
            "feature": preprocessor.feature_names,
            "coefficient": final_model.coef_[0],
        })
        coefficient_frame.to_csv(coefficient_checkpoint, index=False)
        prediction_parts.append(output)
        tuning_parts.append(tuning_frame)
        coefficient_parts.append(coefficient_frame)

    predictions = pd.concat(prediction_parts, ignore_index=True).sort_values(
        ["outer_fold", "stay_key", "landmark_h"]
    )
    if len(predictions) != len(data) or predictions.duplicated(["stay_key", "landmark_h"]).any():
        raise ValueError("M3 V0.2 OOF reconciliation failed")
    prediction_path = OUTPUT_DIR / f"{database}_{base.MODEL_ID}_OOF_V0_2.parquet"
    predictions.to_parquet(prediction_path, index=False)
    os.chmod(prediction_path, 0o600)
    y = predictions["outcome_imv"].to_numpy(int)
    probability = predictions["probability"].to_numpy(float)
    pd.DataFrame([{
        "database": database, "model_id": base.MODEL_ID,
        "n_landmarks": len(predictions), "events": int(y.sum()), "prevalence": float(y.mean()),
        **base.metrics(y, probability),
    }]).to_csv(RESULT_DIR / f"{database}_{base.MODEL_ID}_OOF_METRICS_V0_2.csv", index=False)
    pd.concat(tuning_parts, ignore_index=True).to_csv(
        RESULT_DIR / f"{database}_{base.MODEL_ID}_TUNING_V0_2.csv", index=False
    )
    pd.concat(coefficient_parts, ignore_index=True).to_csv(
        RESULT_DIR / f"{database}_{base.MODEL_ID}_COEFFICIENTS_V0_2.csv", index=False
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", choices=["mimic", "eicu"], required=True)
    args = parser.parse_args()
    manifest = verify_manifest()
    run(args.database, manifest)


if __name__ == "__main__":
    main()
