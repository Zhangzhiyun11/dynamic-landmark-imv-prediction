#!/usr/bin/env python3
"""Prespecified DCA and alert burden for raw and cross-fit transport probabilities."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import platform
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow
import scipy
import sklearn


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "06_models" / "V2_T5_TRANSPORT_UTILITY_MANIFEST_V0_1.json"
INPUT_DIR = ROOT / "restricted" / "t5_transport_postevaluation"
RESULT_DIR = ROOT / "07_results" / "t5_transport"
MODELS = ("M0_LIU_ADAPTED", "M1_SNAPSHOT_CAT", "M2_DYNAMIC_CAT")
PROBABILITY_COLUMNS = {
    "raw": "raw_probability",
    "intercept_only": "crossfit_intercept_only_probability",
    "intercept_slope": "crossfit_intercept_slope_probability",
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
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "pyarrow": pyarrow.__version__,
        "scipy": scipy.__version__,
        "scikit_learn": sklearn.__version__,
    }


def verify_manifest() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    expected = "FROZEN_AFTER_RECALIBRATION_BEFORE_FIRST_TRANSPORT_UTILITY_RESULT"
    if manifest["status"] != expected:
        raise RuntimeError("Transport utility manifest status is not frozen")
    if runtime_versions() != manifest["runtime"]:
        raise RuntimeError("Transport utility runtime differs from manifest")
    for relative, expected_hash in manifest["sha256"].items():
        observed = sha256(ROOT / relative)
        if observed != expected_hash:
            raise RuntimeError(f"SHA-256 mismatch for {relative}: {observed} != {expected_hash}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=["rolling", "fixed"], required=True)
    args = parser.parse_args()
    verify_manifest()
    base = importlib.import_module("run_v2_t4_calibration_utility_v0_1")
    rows = []
    for model_id in MODELS:
        path = INPUT_DIR / f"{args.scope}_MIMIC_TO_EICU_{model_id}_CROSSFIT_RECALIBRATED_V0_1.parquet"
        frame = pd.read_parquet(path)
        required = {
            "database", "stay_key", "landmark_h", "outcome_imv", *PROBABILITY_COLUMNS.values()
        }
        if not required.issubset(frame.columns):
            raise ValueError(f"Missing required columns in {path}")
        for update_type, probability_column in PROBABILITY_COLUMNS.items():
            work = frame[["stay_key", "landmark_h", "outcome_imv", probability_column]].rename(
                columns={probability_column: "probability"}
            )
            utility = base.utility_rows(work, "eicu", model_id)
            for row in utility:
                row.update({
                    "scope": args.scope,
                    "development_database": "mimic",
                    "transport_database": "eicu",
                    "update_type": update_type,
                })
            rows.extend(utility)
    output = pd.DataFrame(rows)
    output.to_csv(
        RESULT_DIR / f"{args.scope}_MIMIC_TO_EICU_DCA_ALERT_BURDEN_V0_1.csv", index=False
    )

    comparison = output.loc[output["model_id"].isin(MODELS)].pivot(
        index=["scope", "update_type", "threshold"],
        columns="model_id",
        values="net_benefit",
    ).reset_index()
    comparison["M2_minus_M1_net_benefit"] = (
        comparison["M2_DYNAMIC_CAT"] - comparison["M1_SNAPSHOT_CAT"]
    )
    comparison["M2_minus_M0_net_benefit"] = (
        comparison["M2_DYNAMIC_CAT"] - comparison["M0_LIU_ADAPTED"]
    )
    comparison.to_csv(
        RESULT_DIR / f"{args.scope}_MIMIC_TO_EICU_DCA_NET_BENEFIT_COMPARISON_V0_1.csv",
        index=False,
    )


if __name__ == "__main__":
    main()
