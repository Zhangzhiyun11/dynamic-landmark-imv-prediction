#!/usr/bin/env python3
"""Frozen nested Elastic Net runner for M3 using the M2 information set."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import platform
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow
import scipy
import sklearn
from scipy import sparse
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score
from sklearn.preprocessing import OneHotEncoder


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "06_models" / "V2_T4_M3_ELASTIC_NET_MANIFEST_V0_1.json"
OUTPUT_DIR = ROOT / "restricted" / "t4_m3_checkpoints"
RESULT_DIR = ROOT / "07_results" / "t4_m3"
MODEL_ID = "M3_DYNAMIC_EN"
SEED = 20260909
L1_RATIOS = (0.0, 0.25, 0.5, 0.75, 1.0)
CS = (0.01, 0.03, 0.1, 0.3, 1.0)


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


def verify_manifest() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest["status"] != "FROZEN_BEFORE_FIRST_M3_OUTCOME_BASED_FIT_OR_RESULT":
        raise RuntimeError("M3 manifest status is not frozen")
    if runtime_versions() != manifest["runtime"]:
        raise RuntimeError("M3 runtime differs from manifest")
    for relative, expected in manifest["sha256"].items():
        observed = sha256(ROOT / relative)
        if observed != expected:
            raise RuntimeError(f"SHA-256 mismatch for {relative}: {observed} != {expected}")
    return manifest


def stable_inner_fold(value: object, seed: int, n_folds: int = 3) -> int:
    text = f"{seed}|{value}".encode("utf-8")
    return int(hashlib.sha256(text).hexdigest()[:16], 16) % n_folds


def metrics(y: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    return {
        "auprc": float(average_precision_score(y, probability)),
        "auroc": float(roc_auc_score(y, probability)),
        "brier": float(np.mean((y - probability) ** 2)),
        "logloss": float(log_loss(y, probability, labels=[0, 1])),
    }


@dataclass
class Preprocessor:
    continuous: list[str]
    categorical: list[str]
    medians: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    means: np.ndarray
    scales: np.ndarray
    category_sets: dict[str, set[str]]
    encoder: OneHotEncoder | None
    feature_names: list[str]


def continuous_array(frame: pd.DataFrame, columns: list[str]) -> np.ndarray:
    array = frame[columns].to_numpy(dtype=np.float32, na_value=np.nan, copy=True)
    array[~np.isfinite(array)] = np.nan
    return array


def categorical_frame(
    frame: pd.DataFrame, columns: list[str], category_sets: dict[str, set[str]] | None = None
) -> pd.DataFrame:
    output = pd.DataFrame(index=frame.index)
    for column in columns:
        values = frame[column].fillna("__MISSING__").astype(str)
        if category_sets is not None:
            values = values.where(values.isin(category_sets[column]), "__OTHER__")
        output[column] = values
    return output


def fit_preprocessor(
    frame: pd.DataFrame, features: list[str], categorical: list[str]
) -> tuple[Preprocessor, sparse.csr_matrix]:
    continuous = [feature for feature in features if feature not in categorical]
    array = continuous_array(frame, continuous)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        medians = np.nanmedian(array, axis=0)
        lower = np.nanquantile(array, 0.005, axis=0)
        upper = np.nanquantile(array, 0.995, axis=0)
    medians = np.nan_to_num(medians, nan=0.0).astype(np.float32)
    lower = np.where(np.isfinite(lower), lower, medians).astype(np.float32)
    upper = np.where(np.isfinite(upper), upper, medians).astype(np.float32)
    missing = np.where(np.isnan(array))
    array[missing] = medians[missing[1]]
    np.clip(array, lower, upper, out=array)
    means = array.mean(axis=0, dtype=np.float64).astype(np.float32)
    scales = array.std(axis=0, dtype=np.float64).astype(np.float32)
    scales[~np.isfinite(scales) | (scales == 0)] = 1.0
    array -= means
    array /= scales
    continuous_matrix = sparse.csr_matrix(array, dtype=np.float32)

    category_sets: dict[str, set[str]] = {}
    categories = []
    for column in categorical:
        observed = sorted(set(frame[column].fillna("__MISSING__").astype(str)))
        if "__OTHER__" not in observed:
            observed.append("__OTHER__")
        category_sets[column] = set(observed)
        categories.append(np.asarray(observed, dtype=object))
    encoder = None
    categorical_matrix = sparse.csr_matrix((len(frame), 0), dtype=np.float32)
    encoded_names: list[str] = []
    if categorical:
        encoder = OneHotEncoder(
            categories=categories, handle_unknown="error", sparse_output=True, dtype=np.float32
        )
        prepared = categorical_frame(frame, categorical, category_sets)
        categorical_matrix = encoder.fit_transform(prepared)
        encoded_names = encoder.get_feature_names_out(categorical).tolist()
    matrix = sparse.hstack([continuous_matrix, categorical_matrix], format="csr", dtype=np.float32)
    preprocessor = Preprocessor(
        continuous=continuous,
        categorical=categorical,
        medians=medians,
        lower=lower,
        upper=upper,
        means=means,
        scales=scales,
        category_sets=category_sets,
        encoder=encoder,
        feature_names=continuous + encoded_names,
    )
    return preprocessor, matrix


def transform(frame: pd.DataFrame, preprocessor: Preprocessor) -> sparse.csr_matrix:
    array = continuous_array(frame, preprocessor.continuous)
    missing = np.where(np.isnan(array))
    array[missing] = preprocessor.medians[missing[1]]
    np.clip(array, preprocessor.lower, preprocessor.upper, out=array)
    array -= preprocessor.means
    array /= preprocessor.scales
    continuous_matrix = sparse.csr_matrix(array, dtype=np.float32)
    categorical_matrix = sparse.csr_matrix((len(frame), 0), dtype=np.float32)
    if preprocessor.categorical:
        prepared = categorical_frame(
            frame, preprocessor.categorical, preprocessor.category_sets
        )
        categorical_matrix = preprocessor.encoder.transform(prepared)
    return sparse.hstack(
        [continuous_matrix, categorical_matrix], format="csr", dtype=np.float32
    )


def config_grid() -> list[dict]:
    rows = []
    for l1_ratio in L1_RATIOS:
        for c_value in CS:
            rows.append({
                "config_id": f"L1R{int(round(l1_ratio * 100)):03d}_C{str(c_value).replace('.', '')}",
                "l1_ratio": l1_ratio,
                "C": c_value,
            })
    return rows


def fit_logistic(
    matrix: sparse.csr_matrix, y: np.ndarray, config: dict, seed: int
) -> tuple[LogisticRegression, bool]:
    model = LogisticRegression(
        solver="saga",
        penalty="elasticnet",
        l1_ratio=float(config["l1_ratio"]),
        C=float(config["C"]),
        max_iter=3000,
        tol=1e-4,
        class_weight=None,
        random_state=seed,
        n_jobs=1,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", category=ConvergenceWarning)
        model.fit(matrix, y)
    warned = any(issubclass(item.category, ConvergenceWarning) for item in caught)
    return model, warned


def run(database: str, manifest: dict) -> None:
    module = importlib.import_module("run_v2_t4_nested_catboost_v0_3")
    base_manifest = module.load_and_verify_manifest()
    data, features, categorical = module.load_dataset(database, "M2_DYNAMIC_CAT", base_manifest)
    if len(features) != 140 or len(categorical) != 3:
        raise ValueError("M3 feature information set differs from frozen M2 set")
    group_column = "patient_key" if database == "mimic" else "hospital_id"
    grid = config_grid()
    if len(grid) != 25:
        raise ValueError("Elastic Net grid is not 25 configurations")
    seed = int(manifest["primary_seed"])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(OUTPUT_DIR, 0o700)
    prediction_parts = []
    tuning_parts = []
    coefficient_parts = []

    for outer_fold in sorted(data["outer_fold"].astype(int).unique()):
        prediction_checkpoint = OUTPUT_DIR / f"{database}_{MODEL_ID}_outer{outer_fold}_predictions.parquet"
        tuning_checkpoint = OUTPUT_DIR / f"{database}_{MODEL_ID}_outer{outer_fold}_tuning.csv"
        coefficient_checkpoint = OUTPUT_DIR / f"{database}_{MODEL_ID}_outer{outer_fold}_coefficients.csv"
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
            lambda value: stable_inner_fold(value, seed)
        )
        fold_events = outer_train.groupby("inner_fold")["outcome_imv"].agg(["sum", "count"])
        if len(fold_events) != 3 or (fold_events["sum"] == 0).any():
            raise ValueError(f"Inner folds not estimable in {database} fold {outer_fold}")

        config_results = {
            config["config_id"]: {"config": config, "metrics": [], "n_iter": [], "warnings": [], "nonzero": []}
            for config in grid
        }
        for inner_fold in range(3):
            inner_train = outer_train.loc[outer_train["inner_fold"].ne(inner_fold)]
            inner_valid = outer_train.loc[outer_train["inner_fold"].eq(inner_fold)]
            preprocessor, train_matrix = fit_preprocessor(inner_train, features, categorical)
            valid_matrix = transform(inner_valid, preprocessor)
            y_train = inner_train["outcome_imv"].to_numpy(int)
            y_valid = inner_valid["outcome_imv"].to_numpy(int)
            for config in grid:
                model, warned = fit_logistic(train_matrix, y_train, config, seed + inner_fold)
                probability = model.predict_proba(valid_matrix)[:, 1]
                result = config_results[config["config_id"]]
                result["metrics"].append(metrics(y_valid, probability))
                result["n_iter"].append(int(model.n_iter_[0]))
                result["warnings"].append(int(warned))
                result["nonzero"].append(int(np.count_nonzero(model.coef_)))

        tuning_rows = []
        for config_id, result in config_results.items():
            tuning_rows.append({
                "database": database,
                "model_id": MODEL_ID,
                "outer_fold": int(outer_fold),
                "config_id": config_id,
                "l1_ratio": result["config"]["l1_ratio"],
                "C": result["config"]["C"],
                "mean_auprc": float(np.mean([item["auprc"] for item in result["metrics"]])),
                "mean_auroc": float(np.mean([item["auroc"] for item in result["metrics"]])),
                "mean_logloss": float(np.mean([item["logloss"] for item in result["metrics"]])),
                "max_n_iter": int(max(result["n_iter"])),
                "convergence_warning_fits": int(sum(result["warnings"])),
                "median_nonzero_coefficients": int(np.median(result["nonzero"])),
            })
        winner = sorted(
            tuning_rows,
            key=lambda row: (
                -row["mean_auprc"], row["mean_logloss"], -row["l1_ratio"], row["C"], row["config_id"]
            ),
        )[0]
        for row in tuning_rows:
            row["selected"] = int(row["config_id"] == winner["config_id"])
        tuning_frame = pd.DataFrame(tuning_rows)
        tuning_frame.to_csv(tuning_checkpoint, index=False)

        selected_config = next(item for item in grid if item["config_id"] == winner["config_id"])
        preprocessor, train_matrix = fit_preprocessor(outer_train, features, categorical)
        test_matrix = transform(outer_test, preprocessor)
        final_model, warned = fit_logistic(
            train_matrix, outer_train["outcome_imv"].to_numpy(int), selected_config, seed
        )
        probability = final_model.predict_proba(test_matrix)[:, 1]
        output = outer_test[
            ["database", "patient_key", "stay_key", "hospital_id", "landmark_h", "outer_fold", "outcome_imv"]
        ].copy()
        output["model_id"] = MODEL_ID
        output["probability"] = probability
        output["selected_config_id"] = winner["config_id"]
        output["final_n_iter"] = int(final_model.n_iter_[0])
        output["final_convergence_warning"] = int(warned)
        output.to_parquet(prediction_checkpoint, index=False)
        os.chmod(prediction_checkpoint, 0o600)

        coefficient_frame = pd.DataFrame({
            "database": database,
            "model_id": MODEL_ID,
            "outer_fold": int(outer_fold),
            "selected_config_id": winner["config_id"],
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
        raise ValueError("M3 OOF reconciliation failed")
    prediction_path = OUTPUT_DIR / f"{database}_{MODEL_ID}_OOF_V0_1.parquet"
    predictions.to_parquet(prediction_path, index=False)
    os.chmod(prediction_path, 0o600)
    y = predictions["outcome_imv"].to_numpy(int)
    probability = predictions["probability"].to_numpy(float)
    pd.DataFrame([{
        "database": database, "model_id": MODEL_ID,
        "n_landmarks": len(predictions), "events": int(y.sum()), "prevalence": float(y.mean()),
        **metrics(y, probability),
    }]).to_csv(RESULT_DIR / f"{database}_{MODEL_ID}_OOF_METRICS_V0_1.csv", index=False)
    pd.concat(tuning_parts, ignore_index=True).to_csv(
        RESULT_DIR / f"{database}_{MODEL_ID}_TUNING_V0_1.csv", index=False
    )
    pd.concat(coefficient_parts, ignore_index=True).to_csv(
        RESULT_DIR / f"{database}_{MODEL_ID}_COEFFICIENTS_V0_1.csv", index=False
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", choices=["mimic", "eicu"], required=True)
    args = parser.parse_args()
    manifest = verify_manifest()
    run(args.database, manifest)


if __name__ == "__main__":
    main()
