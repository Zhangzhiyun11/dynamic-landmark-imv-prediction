#!/usr/bin/env python3
"""Frozen nested CatBoost runner for V2 M0/M1/M2.

The script verifies the Gate 3 manifest before reading outcomes. It writes only
restricted row-level predictions/checkpoints and aggregate QC/results.
"""

from __future__ import annotations

import argparse
import hashlib
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
from catboost import CatBoostClassifier, Pool
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "06_models" / "V2_GATE3_IMPLEMENTATION_MANIFEST_V0_1.json"
PREDICTOR_PATH = ROOT / "restricted" / "V2_PREDICTOR_MATRIX_V0_1.parquet"
REGISTRY_PATH = ROOT / "01_specs" / "V2_EXACT_FEATURE_REGISTRY_V0_1.csv"
CHECKPOINT_DIR = ROOT / "restricted" / "t4_checkpoints"
RESULT_DIR = ROOT / "07_results" / "t4"

DATABASE_LABELS = {"mimic": "MIMIC-IV v3.1", "eicu": "eICU-CRD v2.0"}
MODEL_COLUMNS = {
    "M0_LIU_ADAPTED": "m0_liu_adapted",
    "M1_SNAPSHOT_CAT": "m1_snapshot",
    "M2_DYNAMIC_CAT": "m2_dynamic",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
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


def load_and_verify_manifest() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest["status"] != "FROZEN_BEFORE_FIRST_V2_OUTCOME_MERGE_OR_MODEL_RESULT":
        raise RuntimeError("Gate 3 manifest status is not frozen")
    observed_runtime = runtime_versions()
    if observed_runtime != manifest["runtime"]:
        raise RuntimeError(f"Runtime mismatch: {observed_runtime} != {manifest['runtime']}")
    for relative, expected in manifest["sha256"].items():
        observed = sha256(ROOT / relative)
        if observed != expected:
            raise RuntimeError(f"SHA-256 mismatch for {relative}: {observed} != {expected}")
    return manifest


def feature_spec(model_id: str) -> tuple[list[str], list[str]]:
    registry = pd.read_csv(REGISTRY_PATH)
    membership = MODEL_COLUMNS[model_id]
    selected = registry.loc[registry[membership].eq(1)].copy()
    features = selected["feature"].tolist()
    categorical = selected.loc[selected["feature_type"].eq("categorical"), "feature"].tolist()
    return features, categorical


def load_dataset(database: str, model_id: str, manifest: dict) -> tuple[pd.DataFrame, list[str], list[str]]:
    label = DATABASE_LABELS[database]
    features, categorical = feature_spec(model_id)
    id_columns = ["database", "patient_key", "stay_key", "hospital_id", "landmark_h", "outer_fold"]
    predictors = pd.read_parquet(
        PREDICTOR_PATH, columns=id_columns + features,
        filters=[("database", "=", label)],
    )
    if predictors.duplicated(["stay_key", "landmark_h"]).any():
        raise ValueError("Predictor keys are not unique")

    if database == "mimic":
        outcome_path = ROOT / "restricted" / "V2_MIMIC_ROLLING_OUTCOME_V0_1.parquet"
        outcome = pd.read_parquet(outcome_path, columns=["stay_id", "landmark_h", "outcome_imv"])
        outcome["stay_key"] = outcome["stay_id"].astype(str)
        expected_rows, expected_events = 232316, 5765
    else:
        outcome_path = ROOT / "restricted" / "V2_EICU_ROLLING_OUTCOME_V0_1.parquet"
        outcome = pd.read_parquet(outcome_path, columns=["icu_stay_key", "landmark_h", "outcome_imv"])
        outcome["stay_key"] = outcome["icu_stay_key"].astype(str)
        expected_rows, expected_events = 568182, 4384
    outcome = outcome.loc[outcome["outcome_imv"].isin([0, 1]), ["stay_key", "landmark_h", "outcome_imv"]]
    data = predictors.merge(outcome, on=["stay_key", "landmark_h"], how="inner", validate="one_to_one")
    if len(data) != expected_rows or int(data["outcome_imv"].sum()) != expected_events:
        raise ValueError(
            f"Outcome reconciliation failed for {database}: rows={len(data)}, events={data['outcome_imv'].sum()}"
        )
    if set(data["outer_fold"].astype(int)) != set(manifest["outer_folds"][database]):
        raise ValueError("Outer fold labels differ from manifest")
    for feature in categorical:
        data[feature] = data[feature].fillna("__MISSING__").astype(str)
    return data, features, categorical


def stable_inner_fold(value: object, seed: int, n_folds: int = 3) -> int:
    text = f"{seed}|{value}".encode("utf-8")
    return int(hashlib.sha256(text).hexdigest()[:16], 16) % n_folds


def metrics(y: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    return {
        "auprc": float(average_precision_score(y, probability)),
        "auroc": float(roc_auc_score(y, probability)),
        "logloss": float(log_loss(y, probability, labels=[0, 1])),
        "brier": float(np.mean((y - probability) ** 2)),
    }


def fit_one(
    train: pd.DataFrame,
    valid: pd.DataFrame | None,
    features: list[str],
    categorical: list[str],
    params: dict,
    iterations: int,
    seed: int,
) -> CatBoostClassifier:
    cat_indices = [features.index(name) for name in categorical]
    train_pool = Pool(train[features], train["outcome_imv"], cat_features=cat_indices, feature_names=features)
    model = CatBoostClassifier(
        iterations=iterations,
        depth=params["depth"],
        learning_rate=params["learning_rate"],
        random_seed=seed,
        random_strength=1,
        l2_leaf_reg=10,
        rsm=0.8,
        bootstrap_type="Bayesian",
        bagging_temperature=1,
        loss_function="Logloss",
        eval_metric="PRAUC",
        thread_count=4,
        allow_writing_files=False,
        verbose=False,
    )
    if valid is None:
        model.fit(train_pool)
    else:
        valid_pool = Pool(valid[features], valid["outcome_imv"], cat_features=cat_indices, feature_names=features)
        model.fit(train_pool, eval_set=valid_pool, early_stopping_rounds=75, use_best_model=True)
    return model


def run_model(database: str, model_id: str, manifest: dict) -> None:
    data, features, categorical = load_dataset(database, model_id, manifest)
    group_column = "patient_key" if database == "mimic" else "hospital_id"
    seed = int(manifest["primary_seed"])
    grid = manifest["catboost_grid"]
    expected_features = manifest["feature_counts"][model_id]
    if len(features) != expected_features:
        raise ValueError(f"Feature count mismatch for {model_id}: {len(features)} != {expected_features}")

    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CHECKPOINT_DIR, 0o700)
    prediction_parts: list[pd.DataFrame] = []
    tuning_rows: list[dict] = []

    for outer_fold in sorted(data["outer_fold"].astype(int).unique()):
        checkpoint = CHECKPOINT_DIR / f"{database}_{model_id}_outer{outer_fold}_predictions.parquet"
        tuning_checkpoint = CHECKPOINT_DIR / f"{database}_{model_id}_outer{outer_fold}_tuning.csv"
        if checkpoint.exists() and tuning_checkpoint.exists():
            prediction_parts.append(pd.read_parquet(checkpoint))
            tuning_rows.extend(pd.read_csv(tuning_checkpoint).to_dict("records"))
            continue

        outer_train = data.loc[data["outer_fold"].ne(outer_fold)].copy()
        outer_test = data.loc[data["outer_fold"].eq(outer_fold)].copy()
        overlap = set(outer_train[group_column].astype(str)) & set(outer_test[group_column].astype(str))
        if overlap:
            raise ValueError(f"Outer group leakage detected: {database} fold {outer_fold}")
        outer_train["inner_fold"] = outer_train[group_column].map(lambda value: stable_inner_fold(value, seed))
        fold_events = outer_train.groupby("inner_fold")["outcome_imv"].agg(["sum", "count"])
        if len(fold_events) != 3 or (fold_events["sum"] == 0).any() or (fold_events["sum"] == fold_events["count"]).any():
            raise ValueError(f"Inner fold not estimable: {database} outer {outer_fold}\n{fold_events}")

        config_summaries: list[dict] = []
        best_iterations: dict[str, list[int]] = {}
        for config in grid:
            fold_metric_rows: list[dict] = []
            config_id = config["config_id"]
            best_iterations[config_id] = []
            for inner_fold in range(3):
                inner_train = outer_train.loc[outer_train["inner_fold"].ne(inner_fold)]
                inner_valid = outer_train.loc[outer_train["inner_fold"].eq(inner_fold)]
                model = fit_one(inner_train, inner_valid, features, categorical, config, 700, seed + inner_fold)
                best_iteration = model.get_best_iteration()
                if best_iteration is None or best_iteration < 0:
                    best_iteration = 699
                best_iterations[config_id].append(int(best_iteration) + 1)
                probability = model.predict_proba(inner_valid[features])[:, 1]
                fold_metric_rows.append(metrics(inner_valid["outcome_imv"].to_numpy(int), probability))
            summary = {
                "database": database,
                "model_id": model_id,
                "outer_fold": int(outer_fold),
                "config_id": config_id,
                "depth": config["depth"],
                "learning_rate": config["learning_rate"],
                "mean_auprc": float(np.mean([row["auprc"] for row in fold_metric_rows])),
                "mean_auroc": float(np.mean([row["auroc"] for row in fold_metric_rows])),
                "mean_logloss": float(np.mean([row["logloss"] for row in fold_metric_rows])),
                "median_best_iteration": int(np.median(best_iterations[config_id])),
            }
            config_summaries.append(summary)

        winner = sorted(
            config_summaries,
            key=lambda row: (-row["mean_auprc"], row["mean_logloss"], row["depth"], row["config_id"]),
        )[0]
        for row in config_summaries:
            row["selected"] = int(row["config_id"] == winner["config_id"])
        tuning_rows.extend(config_summaries)
        pd.DataFrame(config_summaries).to_csv(tuning_checkpoint, index=False)

        selected_config = next(item for item in grid if item["config_id"] == winner["config_id"])
        iterations = min(700, max(50, int(winner["median_best_iteration"])))
        final_model = fit_one(outer_train, None, features, categorical, selected_config, iterations, seed)
        probability = final_model.predict_proba(outer_test[features])[:, 1]
        output = outer_test[
            ["database", "patient_key", "stay_key", "hospital_id", "landmark_h", "outer_fold", "outcome_imv"]
        ].copy()
        output["model_id"] = model_id
        output["probability"] = probability
        output["selected_config_id"] = winner["config_id"]
        output["refit_iterations"] = iterations
        output.to_parquet(checkpoint, index=False)
        os.chmod(checkpoint, 0o600)
        prediction_parts.append(output)

    predictions = pd.concat(prediction_parts, ignore_index=True).sort_values(
        ["outer_fold", "stay_key", "landmark_h"]
    )
    if len(predictions) != len(data) or predictions.duplicated(["stay_key", "landmark_h"]).any():
        raise ValueError("Held-out prediction reconciliation failed")
    prediction_path = CHECKPOINT_DIR / f"{database}_{model_id}_OOF_V0_1.parquet"
    predictions.to_parquet(prediction_path, index=False)
    os.chmod(prediction_path, 0o600)

    overall = metrics(predictions["outcome_imv"].to_numpy(int), predictions["probability"].to_numpy(float))
    metric_rows = [
        {"database": database, "model_id": model_id, "evaluation": "nested_oof", "metric": key, "estimate": value}
        for key, value in overall.items()
    ]
    pd.DataFrame(metric_rows).to_csv(RESULT_DIR / f"{database}_{model_id}_OOF_METRICS_V0_1.csv", index=False)
    pd.DataFrame(tuning_rows).to_csv(RESULT_DIR / f"{database}_{model_id}_TUNING_V0_1.csv", index=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", choices=["mimic", "eicu"], required=True)
    parser.add_argument("--models", nargs="+", choices=list(MODEL_COLUMNS), required=True)
    args = parser.parse_args()
    manifest = load_and_verify_manifest()
    for model_id in args.models:
        run_model(args.database, model_id, manifest)


if __name__ == "__main__":
    main()
