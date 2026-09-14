#!/usr/bin/env python3
"""Generic paired OOF comparison with patient/hospital cluster bootstrap."""

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


def metrics(y, probability, weight=None):
    return {
        "auprc": float(average_precision_score(y, probability, sample_weight=weight)),
        "auroc": float(roc_auc_score(y, probability, sample_weight=weight)),
        "brier": float(np.average((y - probability) ** 2, weights=weight)),
        "logloss": float(log_loss(y, probability, sample_weight=weight, labels=[0, 1])),
    }


def load(database: str, reference: str, candidate: str) -> pd.DataFrame:
    keys = ["database", "patient_key", "stay_key", "hospital_id", "landmark_h", "outer_fold", "outcome_imv"]
    left = pd.read_parquet(CHECKPOINT_DIR / f"{database}_{reference}_OOF_V0_1.parquet")
    right = pd.read_parquet(CHECKPOINT_DIR / f"{database}_{candidate}_OOF_V0_1.parquet")
    pair = left[keys + ["probability"]].rename(columns={"probability": "reference_probability"}).merge(
        right[keys + ["probability"]].rename(columns={"probability": "candidate_probability"}),
        on=keys, how="inner", validate="one_to_one",
    )
    if len(pair) != len(left) or len(pair) != len(right):
        raise ValueError("Paired rows do not reconcile")
    return pair


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", choices=["mimic", "eicu"], required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--bootstrap", type=int, default=1000)
    args = parser.parse_args()
    pair = load(args.database, args.reference, args.candidate)
    y = pair["outcome_imv"].to_numpy(int)
    p0 = pair["reference_probability"].to_numpy(float)
    p1 = pair["candidate_probability"].to_numpy(float)
    point0, point1 = metrics(y, p0), metrics(y, p1)
    point = pd.DataFrame([{
        "database": args.database, "reference": args.reference, "candidate": args.candidate,
        "metric": metric, "reference_estimate": point0[metric], "candidate_estimate": point1[metric],
        "candidate_minus_reference": point1[metric] - point0[metric],
        "n_landmarks": len(pair), "events": int(y.sum()), "prevalence": float(y.mean()),
    } for metric in point0])

    stem = f"{args.database}_{args.candidate}_VS_{args.reference}"
    point.to_csv(RESULT_DIR / f"{stem}_POINT_V0_1.csv", index=False)
    if args.bootstrap <= 0:
        return

    group_column = "patient_key" if args.database == "mimic" else "hospital_id"
    codes, groups = pd.factorize(pair[group_column], sort=True)
    n_groups = len(groups)
    rng = np.random.default_rng(SEED)
    records = []
    for replicate in range(1, args.bootstrap + 1):
        sampled = rng.integers(0, n_groups, size=n_groups)
        group_weights = np.bincount(sampled, minlength=n_groups).astype(float)
        weight = group_weights[codes]
        if np.sum(weight[y == 1]) == 0 or np.sum(weight[y == 0]) == 0:
            continue
        ref, cand = metrics(y, p0, weight), metrics(y, p1, weight)
        for metric in ref:
            records.append({
                "replicate": replicate, "metric": metric,
                "reference_estimate": ref[metric], "candidate_estimate": cand[metric],
                "candidate_minus_reference": cand[metric] - ref[metric],
            })
    reps = pd.DataFrame(records)
    rep_path = CHECKPOINT_DIR / f"{stem}_CLUSTER_BOOTSTRAP_V0_1.parquet"
    reps.to_parquet(rep_path, index=False)
    os.chmod(rep_path, 0o600)
    ci = reps.groupby("metric", as_index=False).agg(
        reference_ci_low=("reference_estimate", lambda x: x.quantile(0.025)),
        reference_ci_high=("reference_estimate", lambda x: x.quantile(0.975)),
        candidate_ci_low=("candidate_estimate", lambda x: x.quantile(0.025)),
        candidate_ci_high=("candidate_estimate", lambda x: x.quantile(0.975)),
        difference_ci_low=("candidate_minus_reference", lambda x: x.quantile(0.025)),
        difference_ci_high=("candidate_minus_reference", lambda x: x.quantile(0.975)),
        bootstrap_replicates=("replicate", "nunique"),
    )
    point.merge(ci, on="metric", validate="one_to_one").to_csv(
        RESULT_DIR / f"{stem}_CLUSTER_BOOTSTRAP_CI_V0_1.csv", index=False
    )


if __name__ == "__main__":
    main()
