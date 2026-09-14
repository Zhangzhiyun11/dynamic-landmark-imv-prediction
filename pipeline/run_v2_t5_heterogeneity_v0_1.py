#!/usr/bin/env python3
"""Locked fold and subgroup heterogeneity summaries for V2 raw predictions."""

from __future__ import annotations

import hashlib
import json
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
MANIFEST_PATH = ROOT / "06_models" / "V2_T5_HETEROGENEITY_MANIFEST_V0_1.json"
PREDICTOR_PATH = ROOT / "restricted" / "V2_PREDICTOR_MATRIX_V0_1.parquet"
RESULT_DIR = ROOT / "07_results" / "t5_heterogeneity"
MODELS = ("M0_LIU_ADAPTED", "M1_SNAPSHOT_CAT", "M2_DYNAMIC_CAT")
DATABASE_LABELS = {"mimic": "MIMIC-IV v3.1", "eicu": "eICU-CRD v2.0"}
MIN_ROWS = 1000
MIN_EVENTS = 50
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
    if manifest["status"] != "FROZEN_BEFORE_FIRST_STRATIFIED_PERFORMANCE_RESULT":
        raise RuntimeError("Heterogeneity manifest status is not frozen")
    if runtime_versions() != manifest["runtime"]:
        raise RuntimeError("Heterogeneity runtime differs from manifest")
    for relative, expected in manifest["sha256"].items():
        observed = sha256(ROOT / relative)
        if observed != expected:
            raise RuntimeError(f"SHA-256 mismatch for {relative}: {observed} != {expected}")


def prediction_path(scope: str, evaluation: str, database: str, model_id: str) -> Path:
    if evaluation == "nested_oof":
        folder = "t4_checkpoints" if scope == "rolling" else "t5_fixed_checkpoints"
        return ROOT / "restricted" / folder / f"{database}_{model_id}_OOF_V0_1.parquet"
    if database != "eicu":
        raise ValueError("Transport evaluation is only available for eICU target")
    return (
        ROOT / "restricted" / "t5_transport"
        / f"{scope}_MIMIC_TO_EICU_{model_id}_PREDICTIONS_V0_1.parquet"
    )


def load_predictions(scope: str, evaluation: str, database: str) -> pd.DataFrame:
    keys = [
        "database", "patient_key", "stay_key", "hospital_id", "landmark_h",
        "outer_fold", "outcome_imv",
    ]
    merged = None
    for model_id in MODELS:
        frame = pd.read_parquet(prediction_path(scope, evaluation, database, model_id))
        part = frame[keys + ["probability"]].rename(columns={"probability": model_id})
        if merged is None:
            merged = part
        else:
            merged = merged.merge(part, on=keys, how="inner", validate="one_to_one")
    assert merged is not None
    if merged[list(MODELS)].isna().any().any():
        raise ValueError("Missing model probabilities after paired merge")
    return merged


def attach_metadata(frame: pd.DataFrame, scope: str, database: str) -> pd.DataFrame:
    metadata_columns = [
        "stay_key", "landmark_h", "icu_type", "anchor_year_group",
        "oxygen_interface_supported",
    ]
    filters = [("database", "=", DATABASE_LABELS[database])]
    if scope == "fixed":
        filters.append(("landmark_h", "=", 24))
    metadata = pd.read_parquet(PREDICTOR_PATH, columns=metadata_columns, filters=filters)
    if metadata.duplicated(["stay_key", "landmark_h"]).any():
        raise ValueError("Metadata keys are not unique")
    output = frame.merge(metadata, on=["stay_key", "landmark_h"], how="left", validate="one_to_one")
    if output["icu_type"].isna().all():
        raise ValueError("Metadata merge failed")
    output["icu_type"] = output["icu_type"].fillna("__MISSING__").astype(str)
    output["anchor_year_group"] = output["anchor_year_group"].fillna("__MISSING__").astype(str)
    output["interface_stratum"] = output["oxygen_interface_supported"].map(
        {1: "SUPPORTED", 0: "UNSUPPORTED"}
    ).fillna("__MISSING__")
    if database == "eicu":
        volume = output.groupby("hospital_id", as_index=False).size().rename(columns={"size": "rows"})
        volume["volume_stratum"] = pd.qcut(
            volume["rows"].rank(method="first"), 3, labels=["LOW", "MID", "HIGH"]
        ).astype(str)
        output = output.merge(
            volume[["hospital_id", "volume_stratum"]], on="hospital_id", how="left", validate="many_to_one"
        )
    else:
        output["volume_stratum"] = "NOT_APPLICABLE"
    return output


def estimate(y: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    p = np.clip(probability, EPSILON, 1 - EPSILON)
    lp = logit(p)
    citl = brentq(lambda value: float(np.sum(y - expit(lp + value))), -30, 30)
    joint = LogisticRegression(C=np.inf, solver="lbfgs", max_iter=1000)
    joint.fit(lp.reshape(-1, 1), y)
    return {
        "auprc": float(average_precision_score(y, p)),
        "auroc": float(roc_auc_score(y, p)),
        "brier": float(np.mean((y - p) ** 2)),
        "logloss": float(log_loss(y, p, labels=[0, 1])),
        "oe_ratio": float(y.mean() / p.mean()),
        "calibration_in_the_large": float(citl),
        "calibration_slope": float(joint.coef_[0, 0]),
    }


def summarize_stratum(
    frame: pd.DataFrame, scope: str, evaluation: str, database: str,
    stratifier: str, level: str,
) -> list[dict]:
    y = frame["outcome_imv"].to_numpy(int)
    base = {
        "scope": scope,
        "evaluation": evaluation,
        "database": database,
        "stratifier": stratifier,
        "level": str(level),
        "n_landmarks": len(frame),
        "events": int(y.sum()),
        "prevalence": float(y.mean()) if len(y) else np.nan,
    }
    estimable = (
        len(frame) >= MIN_ROWS and int(y.sum()) >= MIN_EVENTS
        and int((y == 0).sum()) > 0 and int((y == 1).sum()) > 0
    )
    if not estimable:
        return [{**base, "model_id": model, "status": "NOT_ESTIMABLE_BY_LOCKED_RULE"} for model in MODELS]
    model_metrics = {
        model: estimate(y, frame[model].to_numpy(float)) for model in MODELS
    }
    rows = []
    for model in MODELS:
        rows.append({
            **base,
            "model_id": model,
            "status": "ESTIMABLE",
            **model_metrics[model],
            "delta_auprc_vs_m1": model_metrics[model]["auprc"] - model_metrics["M1_SNAPSHOT_CAT"]["auprc"],
            "delta_auroc_vs_m1": model_metrics[model]["auroc"] - model_metrics["M1_SNAPSHOT_CAT"]["auroc"],
            "delta_auprc_vs_m0": model_metrics[model]["auprc"] - model_metrics["M0_LIU_ADAPTED"]["auprc"],
            "delta_auroc_vs_m0": model_metrics[model]["auroc"] - model_metrics["M0_LIU_ADAPTED"]["auroc"],
        })
    return rows


def run_analysis(scope: str, evaluation: str, database: str) -> None:
    frame = attach_metadata(load_predictions(scope, evaluation, database), scope, database)
    stratifiers = ["outer_fold", "icu_type"]
    if database == "mimic":
        stratifiers.append("anchor_year_group")
    else:
        stratifiers.extend(["interface_stratum", "volume_stratum"])
    rows = []
    for stratifier in stratifiers:
        for level, group in frame.groupby(stratifier, dropna=False, sort=True):
            rows.extend(summarize_stratum(group, scope, evaluation, database, stratifier, str(level)))
    pd.DataFrame(rows).to_csv(
        RESULT_DIR / f"{scope}_{evaluation}_{database}_STRATIFIED_PERFORMANCE_V0_1.csv",
        index=False,
    )


def main() -> None:
    verify_manifest()
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    for scope in ("rolling", "fixed"):
        for database in ("mimic", "eicu"):
            run_analysis(scope, "nested_oof", database)
        run_analysis(scope, "transport_raw", "eicu")


if __name__ == "__main__":
    main()
