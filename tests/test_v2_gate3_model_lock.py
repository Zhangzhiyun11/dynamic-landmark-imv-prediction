#!/usr/bin/env python3
"""Outcome-blind Gate 3 lock assertions."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "06_models" / "V2_GATE3_IMPLEMENTATION_MANIFEST_V0_1.json"
REGISTRY = ROOT / "01_specs" / "V2_EXACT_FEATURE_REGISTRY_V0_1.csv"
OUTPUT = ROOT / "04_qc" / "V2_GATE3_MODEL_LOCK_ASSERTIONS_V0_1.csv"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    with REGISTRY.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    memberships = {
        "M0_LIU_ADAPTED": "m0_liu_adapted",
        "M1_SNAPSHOT_CAT": "m1_snapshot",
        "M2_DYNAMIC_CAT": "m2_dynamic",
        "M3_DYNAMIC_EN": "m3_dynamic_en",
        "M2_ABG_EXTENSION": "m2_abg_extension",
    }
    sets = {
        model: {row["feature"] for row in rows if int(row[column]) == 1}
        for model, column in memberships.items()
    }
    assertions: list[dict[str, object]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        assertions.append({"assertion": name, "passed": str(bool(passed)).lower(), "detail": detail})

    add(
        "manifest_frozen_status",
        manifest["status"] == "FROZEN_BEFORE_FIRST_V2_OUTCOME_MERGE_OR_MODEL_RESULT",
        manifest["status"],
    )
    hash_failures = [
        relative for relative, expected in manifest["sha256"].items()
        if sha256(ROOT / relative) != expected
    ]
    add("all_manifest_hashes_match", not hash_failures, ",".join(hash_failures) or "9/9 match")
    for model, expected in manifest["feature_counts"].items():
        add(f"{model}_feature_count", len(sets[model]) == expected, f"{len(sets[model])} features")

    expected_m0 = {"gcs_latest", "bmi", "pao2_liu_24h_latest", "rr_latest", "preicu_los_hours"}
    add("m0_exact_five_features", sets["M0_LIU_ADAPTED"] == expected_m0, ",".join(sorted(sets["M0_LIU_ADAPTED"])))
    add("m1_strict_subset_m2", sets["M1_SNAPSHOT_CAT"] < sets["M2_DYNAMIC_CAT"],
        f"M1={len(sets['M1_SNAPSHOT_CAT'])};M2={len(sets['M2_DYNAMIC_CAT'])}")
    add("m2_m3_same_information", sets["M2_DYNAMIC_CAT"] == sets["M3_DYNAMIC_EN"], "exact set equality")
    add("m2_strict_subset_abg", sets["M2_DYNAMIC_CAT"] < sets["M2_ABG_EXTENSION"],
        f"M2={len(sets['M2_DYNAMIC_CAT'])};ABG={len(sets['M2_ABG_EXTENSION'])}")

    prohibited = re.compile(
        r"outcome|event|death|mortality|patient_key|stay_key|hospital_id|outer_fold|"
        r"_offset|documentation_count|measurement_count|interface"
    )
    bad = sorted({feature for model_set in sets.values() for feature in model_set if prohibited.search(feature)})
    add("prohibited_and_process_variables_zero", not bad, ",".join(bad) or "none")

    for filename in (
        "V2_T3_FEATURE_ASSERTIONS_V0_3.csv",
        "V2_T3_TIER_B_ASSERTIONS_V0_1.csv",
        "V2_T3_LAB_ASSERTIONS_V0_1.csv",
        "V2_T3_PREDICTOR_MATRIX_ASSERTIONS_V0_1.csv",
        "V2_T3_FOLD_ASSERTIONS_V0_1.csv",
    ):
        with (ROOT / "04_qc" / filename).open(newline="", encoding="utf-8") as handle:
            qc = list(csv.DictReader(handle))
        failed = [row["assertion"] for row in qc if row["passed"].lower() != "true"]
        add(f"upstream_{filename}_all_pass", not failed, ",".join(failed) or f"{len(qc)}/{len(qc)}")

    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["assertion", "passed", "detail"])
        writer.writeheader()
        writer.writerows(assertions)
    failed = [row for row in assertions if row["passed"] != "true"]
    if failed:
        raise SystemExit(f"Gate 3 model-lock assertions failed: {failed}")
    print(f"PASS: {len(assertions)}/{len(assertions)} Gate 3 model-lock assertions")


if __name__ == "__main__":
    main()
