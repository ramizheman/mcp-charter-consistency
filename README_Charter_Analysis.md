# Charter Consistency / Recorder Bias — Data Quality Pipeline

This folder contains the main code for assessing **charter consistency** and **recorder bias** in forced/unforced error classification. The pipeline compares each charter’s forced % by pattern and surface to the population consensus and produces RCI (Recorder Consistency Index), match reliability, and star-schema CSVs for reporting.

---

## Core pipeline

| File | Role |
|------|------|
| **export_error_sequences_csvs.py** | Pulls error points from Neo4j, builds (surface × pattern × is_tiebreak) with **two-shots-before-error** (error shot not in pattern), computes population forced % (min 50 pts), recorder bias, RCI, match reliability, and writes the star-schema CSVs (recorder_dim, match_dim, recorder_bias_by_pattern_surface, flagged_points, match_reliability, etc.). |
---

## Analysis / variance

| File | Role |
|------|------|
| **recorder_variance_pointlevel.py** | Point-level analysis: one row per error point, runs **logistic regression** (pattern + surface ± recorder) to see how much forced/unforced variance is explained by recorder (e.g. pseudo-R²). Uses the same Neo4j query structure and normalizer. |
| **recorder_variance_explained.py** | Short script that explains or summarizes the variance methodology (narrative for the assessment). Reads the exported recorder_bias CSV and reports how much variance in forced % is explained by recorder identity (linear regression on aggregate rows). |

---

## Related

- **PATTERN_LEGEND.md** — Meaning of pattern codes (phase, shot kind, direction, serve target, etc.) in the exported CSVs.

Requires Neo4j with the match/point/shot graph, and `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` in `.env` or environment.
