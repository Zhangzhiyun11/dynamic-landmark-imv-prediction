#!/usr/bin/env python3
"""Create or verify the immutable Study B/V2 analytic results lock."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "08_lock" / "V2_FINAL_RESULTS_LOCK_V0_1.json"
INCLUDE_DIRS = (
    "00_governance",
    "01_specs",
    "03_pipeline",
    "04_qc",
    "05_tests",
    "06_models",
    "07_results",
    "08_lock",
    "restricted",
)
EXCLUDE_NAMES = {".DS_Store", "V2_FINAL_RESULTS_LOCK_V0_1.json"}
EXCLUDE_PARTS = {".venv", "__pycache__"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def selected_files() -> list[Path]:
    files = []
    for directory in INCLUDE_DIRS:
        for path in (ROOT / directory).rglob("*"):
            if not path.is_file() or path.name in EXCLUDE_NAMES:
                continue
            if any(part in EXCLUDE_PARTS for part in path.parts):
                continue
            files.append(path)
    return sorted(set(files), key=lambda path: str(path.relative_to(ROOT)))


def create() -> None:
    entries = {}
    restricted_files = 0
    restricted_bytes = 0
    for path in selected_files():
        relative = str(path.relative_to(ROOT))
        size = path.stat().st_size
        is_restricted = relative.startswith("restricted/")
        if is_restricted:
            restricted_files += 1
            restricted_bytes += size
        entries[relative] = {
            "sha256": sha256(path),
            "bytes": size,
            "restricted": is_restricted,
        }
    payload = {
        "lock_version": "V0.1",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "LOCKED_AFTER_T4_T6_RESULTS_BEFORE_MANUSCRIPT",
        "scope": "Study B/V2 analytic source, implementation, manifests, QC, aggregate results, maps, and restricted artifacts",
        "self_excluded": str(LOCK_PATH.relative_to(ROOT)),
        "file_count": len(entries),
        "restricted_file_count": restricted_files,
        "total_bytes": sum(item["bytes"] for item in entries.values()),
        "restricted_bytes": restricted_bytes,
        "files": entries,
    }
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(LOCK_PATH, 0o444)
    print(json.dumps({key: payload[key] for key in ["status", "file_count", "restricted_file_count", "total_bytes"]}, indent=2))


def verify() -> None:
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if payload["status"] != "LOCKED_AFTER_T4_T6_RESULTS_BEFORE_MANUSCRIPT":
        raise RuntimeError("Unexpected final results lock status")
    errors = []
    for relative, expected in payload["files"].items():
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"missing:{relative}")
            continue
        if path.stat().st_size != expected["bytes"]:
            errors.append(f"size:{relative}")
            continue
        if sha256(path) != expected["sha256"]:
            errors.append(f"sha256:{relative}")
    if errors:
        raise RuntimeError("Final results lock verification failed: " + ", ".join(errors[:20]))
    if len(payload["files"]) != payload["file_count"]:
        raise RuntimeError("Final results lock file_count mismatch")
    print(json.dumps({"status": "VERIFIED", "file_count": payload["file_count"], "errors": 0}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["create", "verify"])
    args = parser.parse_args()
    if args.mode == "create":
        if LOCK_PATH.exists():
            raise FileExistsError(f"Refusing to overwrite existing lock: {LOCK_PATH}")
        create()
    else:
        verify()


if __name__ == "__main__":
    main()
