#!/usr/bin/env python3
"""Deterministic group-level folds for V2; standard library only."""

from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESTRICTED = ROOT / "restricted"
QC = ROOT / "04_qc"


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def greedy_assign(
    rows: list[dict[str, str]],
    n_folds: int,
    id_field: str,
    event_field: str,
    row_field: str,
    secondary_event_field: str,
) -> list[dict[str, str | int]]:
    states = [dict(event=0, secondary=0, rows=0, groups=0) for _ in range(n_folds)]
    ordered = sorted(
        rows,
        key=lambda r: (
            -int(r[event_field]),
            -int(r[secondary_event_field]),
            -int(r[row_field]),
            str(r[id_field]),
        ),
    )
    assigned: list[dict[str, str | int]] = []
    for row in ordered:
        has_event = int(row[event_field]) > 0
        fold_index = min(
            range(n_folds),
            key=(lambda i: (
                states[i]["event"], states[i]["secondary"],
                states[i]["rows"], states[i]["groups"], i
            )) if has_event else (lambda i: (
                states[i]["rows"], states[i]["groups"],
                states[i]["event"], states[i]["secondary"], i
            )),
        )
        state = states[fold_index]
        state["event"] += int(row[event_field])
        state["secondary"] += int(row[secondary_event_field])
        state["rows"] += int(row[row_field])
        state["groups"] += 1
        assigned.append({**row, "outer_fold": fold_index + 1})
    return sorted(assigned, key=lambda r: str(r[id_field]))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows: list[dict], n_folds: int, group_label: str) -> list[dict[str, int | str]]:
    output = []
    for fold in range(1, n_folds + 1):
        selected = [r for r in rows if int(r["outer_fold"]) == fold]
        output.append(
            {
                "database": group_label,
                "outer_fold": fold,
                "groups": len(selected),
                "patients": sum(int(r.get("patients", 1)) for r in selected),
                "landmarks": sum(int(r.get("landmarks", 0)) for r in selected),
                "event_rows": sum(int(r["event_rows"]) for r in selected),
                "unknown_rows": sum(int(r["unknown_rows"]) for r in selected),
                "event_stays": sum(int(r.get("event_stays", r.get("event_stay", 0))) for r in selected),
            }
        )
    return output


def main() -> None:
    eicu_rows = read_rows(RESTRICTED / "V2_EICU_HOSPITAL_FOLD_INPUT_V0_1.csv")
    eicu = greedy_assign(
        eicu_rows, 10, "hospital_id", "event_rows", "landmarks", "event_stays"
    )
    write_csv(
        RESTRICTED / "V2_EICU_HOSPITAL_OUTER_FOLDS_V0_1.csv",
        eicu,
        ["hospital_id", "patients", "landmarks", "event_rows", "unknown_rows", "event_stays", "outer_fold"],
    )

    mimic_rows = read_rows(RESTRICTED / "V2_MIMIC_PATIENT_FOLD_INPUT_V0_1.csv")
    for row in mimic_rows:
        row["patients"] = "1"
        row["event_stays"] = row["event_stay"]
        row["temporal_role"] = (
            "temporal_development" if row["anchor_year_group"] in
            {"2008 - 2010", "2011 - 2013", "2014 - 2016"} else "temporal_validation"
        )
    mimic = greedy_assign(
        mimic_rows, 5, "subject_id", "event_rows", "landmarks", "event_stays"
    )
    write_csv(
        RESTRICTED / "V2_MIMIC_PATIENT_OUTER_FOLDS_V0_1.csv",
        mimic,
        ["subject_id", "anchor_year_group", "temporal_role", "landmarks", "event_rows", "unknown_rows", "event_stay", "outer_fold"],
    )

    summary = summarize(eicu, 10, "eICU") + summarize(mimic, 5, "MIMIC-IV")
    write_csv(
        QC / "V2_T3_OUTER_FOLD_SUMMARY_V0_1.csv",
        summary,
        ["database", "outer_fold", "groups", "patients", "landmarks", "event_rows", "unknown_rows", "event_stays"],
    )


if __name__ == "__main__":
    main()
