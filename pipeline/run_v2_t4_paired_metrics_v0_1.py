#!/usr/bin/env python3
"""Paired M2-M1 held-out metrics and cluster-bootstrap confidence intervals."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score


ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT_DIR = ROOT / "restricted" / "t4_checkpoints"
RESULT_DIR = ROOT / "07_results" / "t4"
SEED = 20260909


def metric_set(y: np.ndarray, p: np.ndarray, weight: np.ndarray | None = None) -> dict[str, float]:
    return {
        "auprc": float(average_precision_score(y, p, sample_weight=weight)),
        "auroc": float(roc_auc_score(y, p, sample_weight=weight)),
        "brier": float(np.average((y - p) ** 2, weights=weight)),
        "logloss": float(log_loss(y, p, sample_weight=weight, labels=[0, 1])),
    }


def load_pair(database: str) -> pd.DataFrame:
    m1 = pd.read_parquet(CHECKPOINT_DIR / f"{database}_M1_SNAPSHOT_CAT_OOF_V0_1.parquet")
    m2 = pd.read_parquet(CHECKPOINT_DIR / f"{database}_M2_DYNAMIC_CAT_OOF_V0_1.parquet")
    keys = ["database", "patient_key", "stay_key", "hospital_id", "landmark_h", "outer_fold", "outcome_imv"]
    pair = m1[keys + ["probability"]].rename(columns={"probability": "m1_probability"}).merge(
        m2[keys + ["probability"]].rename(columns={"probability": "m2_probability"}),
        on=keys, how="inner", validate="one_to_one",
    )
    if len(pair) != len(m1) or len(pair) != len(m2):
        raise ValueError("M1/M2 prediction rows do not reconcile")
    return pair


def point_rows(pair: pd.DataFrame, database: str) -> list[dict]:
    rows: list[dict] = []
    for evaluation, subset in [("overall", pair)] + [
        (f"outer_fold_{int(fold)}", frame) for fold, frame in pair.groupby("outer_fold", sort=True)
    ]:
        y = subset["outcome_imv"].to_numpy(int)
        m1 = metric_set(y, subset["m1_probability"].to_numpy(float))
        m2 = metric_set(y, subset["m2_probability"].to_numpy(float))
        for metric in m1:
            rows.append({
                "database": database,
                "evaluation": evaluation,
                "metric": metric,
                "m1_estimate": m1[metric],
                "m2_estimate": m2[metric],
                "m2_minus_m1": m2[metric] - m1[metric],
                "n_landmarks": len(subset),
                "events": int(y.sum()),
                "prevalence": float(y.mean()),
            })
    return rows


def bootstrap(pair: pd.DataFrame, database: str, n_bootstrap: int) -> pd.DataFrame:
    group_column = "patient_key" if database == "mimic" else "hospital_id"
    group_codes, groups = pd.factorize(pair[group_column], sort=True)
    n_groups = len(groups)
    y = pair["outcome_imv"].to_numpy(int)
    p1 = pair["m1_probability"].to_numpy(float)
    p2 = pair["m2_probability"].to_numpy(float)
    rng = np.random.default_rng(SEED)
    rows = []
    for replicate in range(1, n_bootstrap + 1):
        sampled = rng.integers(0, n_groups, size=n_groups)
        group_weight = np.bincount(sampled, minlength=n_groups).astype(float)
        weight = group_weight[group_codes]
        if np.sum(weight[y == 1]) == 0 or np.sum(weight[y == 0]) == 0:
            continue
        m1 = metric_set(y, p1, weight)
        m2 = metric_set(y, p2, weight)
        for metric in m1:
            rows.append({
                "database": database,
                "replicate": replicate,
                "metric": metric,
                "m1_estimate": m1[metric],
                "m2_estimate": m2[metric],
                "m2_minus_m1": m2[metric] - m1[metric],
            })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", choices=["mimic", "eicu"], required=True)
    parser.add_argument("--bootstrap", type=int, default=1000)
    args = parser.parse_args()
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    pair = load_pair(args.database)
    point = pd.DataFrame(point_rows(pair, args.database))
    point.to_csv(RESULT_DIR / f"{args.database}_M2_VS_M1_PAIRED_POINT_V0_1.csv", index=False)

    if args.bootstrap > 0:
        reps = bootstrap(pair, args.database, args.bootstrap)
        reps_path = CHECKPOINT_DIR / f"{args.database}_M2_VS_M1_CLUSTER_BOOTSTRAP_V0_1.parquet"
        reps.to_parquet(reps_path, index=False)
        os.chmod(reps_path, 0o600)
        ci = reps.groupby("metric", as_index=False).agg(
            m1_ci_low=("m1_estimate", lambda x: x.quantile(0.025)),
            m1_ci_high=("m1_estimate", lambda x: x.quantile(0.975)),
            m2_ci_low=("m2_estimate", lambda x: x.quantile(0.025)),
            m2_ci_high=("m2_estimate", lambda x: x.quantile(0.975)),
            difference_ci_low=("m2_minus_m1", lambda x: x.quantile(0.025)),
            difference_ci_high=("m2_minus_m1", lambda x: x.quantile(0.975)),
            bootstrap_replicates=("replicate", "nunique"),
        )
        overall = point.loc[point["evaluation"].eq("overall")]
        ci = overall.merge(ci, on="metric", how="left", validate="one_to_one")
        ci.to_csv(RESULT_DIR / f"{args.database}_M2_VS_M1_CLUSTER_BOOTSTRAP_CI_V0_1.csv", index=False)


if __name__ == "__main__":
    main()
