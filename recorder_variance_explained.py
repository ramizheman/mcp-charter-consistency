#!/usr/bin/env python3
"""
Variance in forced/unforced classification explained by recorder identity
after controlling for shot pattern and surface. Uses recorder_bias_by_pattern_surface.csv.
"""
import sys
from pathlib import Path

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import LabelEncoder

SCRIPT_DIR = Path(__file__).resolve().parent
CSV_PATH = SCRIPT_DIR / "recorder_bias_by_pattern_surface.csv"


def main():
    if not CSV_PATH.exists():
        print(f"File not found: {CSV_PATH}", file=sys.stderr)
        return 1

    df = pd.read_csv(CSV_PATH)
    # pattern may be quoted and contain commas
    df["pattern"] = df["pattern"].astype(str).str.strip()

    le_recorder = LabelEncoder()
    le_pattern = LabelEncoder()
    le_surface = LabelEncoder()

    df["recorder_enc"] = le_recorder.fit_transform(df["recorder"].astype(str))
    df["pattern_enc"] = le_pattern.fit_transform(df["pattern"])
    df["surface_enc"] = le_surface.fit_transform(df["surface"].astype(str))

    y = df["recorder_forced_pct"] / 100.0

    # Model 1: pattern + surface only (baseline)
    X_base = df[["pattern_enc", "surface_enc"]]
    m1 = LinearRegression().fit(X_base, y)
    r2_base = m1.score(X_base, y)

    # Model 2: pattern + surface + recorder
    X_full = df[["pattern_enc", "surface_enc", "recorder_enc"]]
    m2 = LinearRegression().fit(X_full, y)
    r2_full = m2.score(X_full, y)

    delta_r2 = r2_full - r2_base
    pct = delta_r2 * 100

    print(f"R² pattern+surface only: {r2_base:.4f}")
    print(f"R² adding recorder:      {r2_full:.4f}")
    print(f"Variance explained by recorder identity: {delta_r2:.4f} ({pct:.2f}%)")
    print()
    print(
        "Email sentence: \"Recorder identity explains {:.2f}% of variance in "
        "forced/unforced classification after controlling for shot pattern and surface.\"".format(
            pct
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
