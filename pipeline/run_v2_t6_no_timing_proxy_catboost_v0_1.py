#!/usr/bin/env python3
"""Fold-matched M2 sensitivity excluding oxygen_time_since_latest_h."""

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
MANIFEST_PATH = ROOT / "06_models" / "V2_T6_NO_TIMING_PROXY_MANIFEST_V0_1.json"
OUTPUT_DIR = ROOT / "restricted" / "t6_no_timing_proxy"
RESULT_DIR = ROOT / "07_results" / "t6_sensitivity"
MODEL_ID = "M2_NO_TIMING_PROXY_CAT"
REMOVED_FEATURE = "oxygen_time_since_latest_h"
SCOPE_MODULES = {
    "rolling": "run_v2_t4_nested_catboost_v0_3",
    "fixed": "run_v2_t5_fixed_24_96_catboost_v0_1",
}
PRIMARY_DIRS = {
    "rolling": ROOT / "restricted" / "t4_checkpoints",
    "fixed": ROOT / "restricted" / "t5_fixed_checkpoints",
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
    if manifest["status"] != "FROZEN_BEFORE_FIRST_NO_TIMING_PROXY_MODEL_FIT_OR_RESULT":
        raise RuntimeError("No-timing-proxy manifest status is not frozen")
    if runtime_versions() != manifest["runtime"]:
        raise RuntimeError("No-timing-proxy runtime differs from manifest")
    for relative, expected in manifest["sha256"].items():
        observed = sha256(ROOT / relative)
        if observed != expected:
            raise RuntimeError(f"SHA-256 mismatch for {relative}: {observed} != {expected}")


def metrics(y: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    return {
        "auprc": float(average_precision_score(y, probability)),
        "auroc": float(roc_auc_score(y, probability)),
        "brier": float(np.mean((y - probability) ** 2)),
        "logloss": float(log_loss(y, probability, labels=[0, 1])),
    }


def run(scope: str, database: str) -> None:
    module = importlib.import_module(SCOPE_MODULES[scope])
    base_manifest = module.load_and_verify_manifest()
    data, primary_features, primary_categorical = module.load_dataset(
        database, "M2_DYNAMIC_CAT", base_manifest
    )
    if REMOVED_FEATURE not in primary_features or len(primary_features) != 140:
        raise ValueError("Frozen M2 feature specification differs from expected")
    features = [feature for feature in primary_features if feature != REMOVED_FEATURE]
    categorical = [feature for feature in primary_categorical if feature != REMOVED_FEATURE]
    if len(features) != 139:
        raise ValueError("No-timing-proxy feature count is not 139")

    primary_path = PRIMARY_DIRS[scope] / f"{database}_M2_DYNAMIC_CAT_OOF_V0_1.parquet"
    primary = pd.read_parquet(
        primary_path,
        columns=["stay_key", "landmark_h", "outer_fold", "selected_config_id", "refit_iterations"],
    )
    selected = primary[["outer_fold", "selected_config_id", "refit_iterations"]].drop_duplicates()
    if selected["outer_fold"].duplicated().any():
        raise ValueError("Primary M2 has multiple refit choices within an outer fold")
    selected = selected.set_index("outer_fold")
    grid = {item["config_id"]: item for item in base_manifest["catboost_grid"]}
    seed = int(base_manifest["primary_seed"])
    group_column = "patient_key" if database == "mimic" else "hospital_id"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(OUTPUT_DIR, 0o700)
    parts = []
    fold_rows = []
    for fold in sorted(data["outer_fold"].astype(int).unique()):
        checkpoint = OUTPUT_DIR / f"{scope}_{database}_{MODEL_ID}_outer{fold}_predictions.parquet"
        if checkpoint.exists():
            output = pd.read_parquet(checkpoint)
            parts.append(output)
            fold_rows.append({
                "scope": scope, "database": database, "outer_fold": fold,
                "status": "REUSED_LOCKED_CHECKPOINT",
                "selected_config_id": output["selected_config_id"].iloc[0],
                "refit_iterations": int(output["refit_iterations"].iloc[0]),
                "n_test": len(output), "events_test": int(output["outcome_imv"].sum()),
            })
            continue
        train = data.loc[data["outer_fold"].ne(fold)]
        test = data.loc[data["outer_fold"].eq(fold)]
        if set(train[group_column].astype(str)) & set(test[group_column].astype(str)):
            raise ValueError(f"Outer group leakage in {scope} {database} fold {fold}")
        config_id = str(selected.loc[fold, "selected_config_id"])
        iterations = int(selected.loc[fold, "refit_iterations"])
        fitted = module.base.fit_one(
            train, None, features, categorical, grid[config_id], iterations, seed
        )
        probability = fitted.predict_proba(test[features])[:, 1]
        output = test[
            ["database", "patient_key", "stay_key", "hospital_id", "landmark_h", "outer_fold", "outcome_imv"]
        ].copy()
        output["model_id"] = MODEL_ID
        output["probability"] = probability
        output["selected_config_id"] = config_id
        output["refit_iterations"] = iterations
        output["removed_feature"] = REMOVED_FEATURE
        output.to_parquet(checkpoint, index=False)
        os.chmod(checkpoint, 0o600)
        parts.append(output)
        fold_rows.append({
            "scope": scope, "database": database, "outer_fold": fold,
            "status": "FIT_AND_SAVED",
            "selected_config_id": config_id, "refit_iterations": iterations,
            "n_test": len(output), "events_test": int(output["outcome_imv"].sum()),
        })

    predictions = pd.concat(parts, ignore_index=True).sort_values(
        ["outer_fold", "stay_key", "landmark_h"]
    )
    if len(predictions) != len(data) or predictions.duplicated(["stay_key", "landmark_h"]).any():
        raise ValueError("No-timing-proxy OOF reconciliation failed")
    final_path = OUTPUT_DIR / f"{scope}_{database}_{MODEL_ID}_OOF_V0_1.parquet"
    predictions.to_parquet(final_path, index=False)
    os.chmod(final_path, 0o600)
    y = predictions["outcome_imv"].to_numpy(int)
    probability = predictions["probability"].to_numpy(float)
    pd.DataFrame([{
        "scope": scope, "database": database, "model_id": MODEL_ID,
        "removed_feature": REMOVED_FEATURE, "feature_count": len(features),
        "n_landmarks": len(predictions), "events": int(y.sum()), "prevalence": float(y.mean()),
        **metrics(y, probability),
    }]).to_csv(
        RESULT_DIR / f"{scope}_{database}_{MODEL_ID}_OOF_METRICS_V0_1.csv", index=False
    )
    pd.DataFrame(fold_rows).to_csv(
        RESULT_DIR / f"{scope}_{database}_{MODEL_ID}_FOLD_REFIT_AUDIT_V0_1.csv", index=False
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=list(SCOPE_MODULES), required=True)
    parser.add_argument("--database", choices=["mimic", "eicu"], required=True)
    args = parser.parse_args()
    verify_manifest()
    run(args.scope, args.database)


if __name__ == "__main__":
    main()
