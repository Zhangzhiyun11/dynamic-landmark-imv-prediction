#!/usr/bin/env python3
"""Raw OOF calibration, decision curves, and prespecified alert burden."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.special import expit, logit
from sklearn.linear_model import LogisticRegression


ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT_DIR = ROOT / "restricted" / "t4_checkpoints"
RESULT_DIR = ROOT / "07_results" / "t4"
THRESHOLDS = (0.005, 0.01, 0.02, 0.03, 0.05)
MODELS = ("M0_LIU_ADAPTED", "M1_SNAPSHOT_CAT", "M2_DYNAMIC_CAT")


def load_predictions(database: str, model_id: str) -> pd.DataFrame:
    return pd.read_parquet(CHECKPOINT_DIR / f"{database}_{model_id}_OOF_V0_1.parquet")


def calibration_summary(frame: pd.DataFrame, database: str, model_id: str) -> dict:
    y = frame["outcome_imv"].to_numpy(int)
    p = np.clip(frame["probability"].to_numpy(float), 1e-6, 1 - 1e-6)
    lp = logit(p)
    intercept = brentq(lambda value: float(np.sum(y - expit(lp + value))), -20, 20)
    model = LogisticRegression(penalty=None, solver="lbfgs", max_iter=1000)
    model.fit(lp.reshape(-1, 1), y)
    return {
        "database": database,
        "model_id": model_id,
        "n_landmarks": len(y),
        "events": int(y.sum()),
        "prevalence": float(y.mean()),
        "mean_predicted_risk": float(p.mean()),
        "oe_ratio": float(y.mean() / p.mean()),
        "calibration_in_the_large": float(intercept),
        "calibration_intercept_joint": float(model.intercept_[0]),
        "calibration_slope": float(model.coef_[0, 0]),
    }


def calibration_bins(frame: pd.DataFrame, database: str, model_id: str) -> pd.DataFrame:
    work = frame[["outcome_imv", "probability"]].copy()
    work["risk_bin"] = pd.qcut(work["probability"], 10, labels=False, duplicates="drop") + 1
    result = work.groupby("risk_bin", as_index=False).agg(
        n=("outcome_imv", "size"),
        events=("outcome_imv", "sum"),
        observed_risk=("outcome_imv", "mean"),
        mean_predicted_risk=("probability", "mean"),
        min_predicted_risk=("probability", "min"),
        max_predicted_risk=("probability", "max"),
    )
    result.insert(0, "model_id", model_id)
    result.insert(0, "database", database)
    return result


def alert_episodes(frame: pd.DataFrame, alert: np.ndarray) -> int:
    work = frame[["stay_key", "landmark_h"]].copy()
    work["alert"] = alert
    work = work.loc[work["alert"]].sort_values(["stay_key", "landmark_h"])
    if work.empty:
        return 0
    gap = work.groupby("stay_key")["landmark_h"].diff()
    return int((gap.isna() | gap.gt(8)).sum())


def utility_rows(frame: pd.DataFrame, database: str, model_id: str) -> list[dict]:
    y = frame["outcome_imv"].to_numpy(int)
    p = frame["probability"].to_numpy(float)
    n = len(y)
    n_stays = frame["stay_key"].nunique()
    rows = []
    for threshold in THRESHOLDS:
        alert = p >= threshold
        tp = int(np.sum(alert & (y == 1)))
        fp = int(np.sum(alert & (y == 0)))
        fn = int(np.sum(~alert & (y == 1)))
        tn = int(np.sum(~alert & (y == 0)))
        alerted_stays = frame.loc[alert, "stay_key"].nunique()
        episodes = alert_episodes(frame, alert)
        rows.append({
            "database": database,
            "model_id": model_id,
            "threshold": threshold,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "sensitivity": tp / (tp + fn),
            "specificity": tn / (tn + fp),
            "ppv": tp / (tp + fp) if tp + fp else np.nan,
            "npv": tn / (tn + fn) if tn + fn else np.nan,
            "alerts_per_100_landmarks": 100 * (tp + fp) / n,
            "alerted_stays_per_100": 100 * alerted_stays / n_stays,
            "episodes_per_100_stays": 100 * episodes / n_stays,
            "alerts_per_detected_event": (tp + fp) / tp if tp else np.nan,
            "net_benefit": tp / n - fp / n * threshold / (1 - threshold),
            "treat_all_net_benefit": y.mean() - (1 - y.mean()) * threshold / (1 - threshold),
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", choices=["mimic", "eicu"], required=True)
    parser.add_argument("--models", nargs="+", choices=list(MODELS), default=list(MODELS))
    args = parser.parse_args()
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    summaries, bins, utilities = [], [], []
    for model_id in args.models:
        frame = load_predictions(args.database, model_id)
        summaries.append(calibration_summary(frame, args.database, model_id))
        bins.append(calibration_bins(frame, args.database, model_id))
        utilities.extend(utility_rows(frame, args.database, model_id))
    pd.DataFrame(summaries).to_csv(
        RESULT_DIR / f"{args.database}_RAW_CALIBRATION_SUMMARY_V0_1.csv", index=False
    )
    pd.concat(bins, ignore_index=True).to_csv(
        RESULT_DIR / f"{args.database}_RAW_CALIBRATION_BINS_V0_1.csv", index=False
    )
    pd.DataFrame(utilities).to_csv(
        RESULT_DIR / f"{args.database}_DCA_ALERT_BURDEN_V0_1.csv", index=False
    )


if __name__ == "__main__":
    main()
