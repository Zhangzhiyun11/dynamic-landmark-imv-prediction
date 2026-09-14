#!/usr/bin/env python3
"""Paired cluster-bootstrap comparisons for the fixed t24-to-t96 analysis."""

from __future__ import annotations

import sys
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE_DIR))
import run_v2_t4_generic_paired_comparison_v0_1 as base  # noqa: E402


base.CHECKPOINT_DIR = base.ROOT / "restricted" / "t5_fixed_checkpoints"
base.RESULT_DIR = base.ROOT / "07_results" / "t5_fixed"


if __name__ == "__main__":
    base.main()
