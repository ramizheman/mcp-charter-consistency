#!/usr/bin/env python3
"""
Point-level variance in forced/unforced classification explained by recorder identity.
Queries Neo4j for individual error points (not aggregates), then runs logistic regression.

Each row = one error point with:
  - forced: 1=FORCED_ERROR, 0=UNFORCED_ERROR
  - pattern: (shot N-2, shot N-1) in same format as recorder_bias CSVs
  - surface: hard/clay/grass/unknown
  - recorder: charted_by

Usage:
  python recorder_variance_pointlevel.py
  Set NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD in .env or environment.
"""
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(SCRIPT_DIR / ".env")
    load_dotenv()
except ImportError:
    pass

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import log_loss

try:
    from match_audit_validator import (
        _normalize_neo4j_shot_to_key,
        _normalize_surface,
    )
except ImportError:
    _normalize_neo4j_shot_to_key = None
    _normalize_surface = None


def pseudo_r2(model, X, y):
    """McFadden pseudo-R² for logistic regression."""
    log_loss_model = log_loss(y, model.predict_proba(X))
    base_rate = y.mean()
    null_probs = np.full((len(y), 2), [1 - base_rate, base_rate])
    log_loss_null = log_loss(y, null_probs)
    return 1 - (log_loss_model / log_loss_null)


def main():
    if _normalize_neo4j_shot_to_key is None or _normalize_surface is None:
        print("Could not import match_audit_validator (_normalize_neo4j_shot_to_key, _normalize_surface).", file=sys.stderr)
        return 1

    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")
    if not password:
        print("NEO4J_PASSWORD not set.", file=sys.stderr)
        return 1

    try:
        from neo4j import GraphDatabase
    except ImportError:
        print("pip install neo4j", file=sys.stderr)
        return 1

    print("Querying Neo4j for point-level error data...")
    cypher = """
    MATCH (pt:Point)-[:IN_MATCH]->(m:Match)
    MATCH (pt)-[:HAS_SHOT]->(s1:Shot), (pt)-[:HAS_SHOT]->(s2:Shot)
    WHERE pt.outcome IN ['FORCED ERROR', 'UNFORCED ERROR', 'FORCED_ERROR', 'UNFORCED_ERROR']
      AND pt.total_shots >= 3
      AND s1.shot_number = pt.total_shots - 1
      AND s2.shot_number = pt.total_shots - 2
    OPTIONAL MATCH (pl1:Player)-[:HIT]->(s1)
    OPTIONAL MATCH (pl2:Player)-[:HIT]->(s2)
    RETURN pt.outcome AS outcome,
           s1.shot_number AS n1, s1.shot_type AS t1, s1.contact_type AS c1, s1.direction AS d1,
           s2.shot_number AS n2, s2.shot_type AS t2, s2.contact_type AS c2, s2.direction AS d2,
           m.surface AS surface, pt.serve_target AS serve_target,
           pl1.handedness AS h1, pl2.handedness AS h2,
           COALESCE(m.charted_by, 'None') AS charted_by
    """
    rows = []
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        driver.verify_connectivity()
        with driver.session() as session:
            result = session.run(cypher)
            for rec in result:
                outcome = (rec.get("outcome") or "").upper().replace(" ", "_")
                if outcome == "FORCED_ERROR":
                    forced = 1
                elif outcome == "UNFORCED_ERROR":
                    forced = 0
                else:
                    continue
                serve_target = rec.get("serve_target") or ""
                n1, n2 = rec.get("n1") or 0, rec.get("n2") or 0
                h1, h2 = rec.get("h1") or "", rec.get("h2") or ""
                comp_n1 = _normalize_neo4j_shot_to_key(
                    n1, rec.get("t1") or "", rec.get("c1") or "", rec.get("d1") or "",
                    serve_target if n1 == 1 else None, h1,
                )
                comp_n2 = _normalize_neo4j_shot_to_key(
                    n2, rec.get("t2") or "", rec.get("c2") or "", rec.get("d2") or "",
                    serve_target if n2 == 1 else None, h2,
                )
                pattern = f"({comp_n2[0]},{comp_n2[1]}) -> ({comp_n1[0]},{comp_n1[1]})"
                surface = _normalize_surface(rec.get("surface"))
                recorder = (rec.get("charted_by") or "None").strip() or "None"
                rows.append({
                    "forced": forced,
                    "pattern": pattern,
                    "surface": surface,
                    "recorder": recorder,
                })
        driver.close()
    except Exception as e:
        print(f"Neo4j query failed: {e}", file=sys.stderr)
        return 1

    print(f"Loaded {len(rows):,} error points.")
    df = pd.DataFrame(rows)
    print(f"Recorders: {df['recorder'].nunique()}")
    print(f"Patterns:  {df['pattern'].nunique()}")
    print(f"Surfaces:  {df['surface'].nunique()}")
    print(f"Forced rate: {df['forced'].mean():.1%}")
    print()

    le_pattern = LabelEncoder()
    le_surface = LabelEncoder()
    le_recorder = LabelEncoder()
    df["pattern_enc"] = le_pattern.fit_transform(df["pattern"])
    df["surface_enc"] = le_surface.fit_transform(df["surface"])
    df["recorder_enc"] = le_recorder.fit_transform(df["recorder"])

    y = df["forced"].values

    print("Fitting Model 1: pattern + surface only...")
    X_base = df[["pattern_enc", "surface_enc"]].values
    m1 = LogisticRegression(max_iter=1000, solver="lbfgs")
    m1.fit(X_base, y)
    pr2_base = pseudo_r2(m1, X_base, y)

    print("Fitting Model 2: pattern + surface + recorder...")
    X_full = df[["pattern_enc", "surface_enc", "recorder_enc"]].values
    m2 = LogisticRegression(max_iter=1000, solver="lbfgs")
    m2.fit(X_full, y)
    pr2_full = pseudo_r2(m2, X_full, y)

    delta = pr2_full - pr2_base
    pct = delta * 100

    print()
    print(f"McFadden pseudo-R² (pattern + surface only): {pr2_base:.4f}")
    print(f"McFadden pseudo-R² (adding recorder):        {pr2_full:.4f}")
    print(f"Additional variance explained by recorder:   {delta:.4f} ({pct:.2f}%)")
    print()
    print(
        f'Email sentence: "Recorder identity explains {pct:.1f}% of variance in '
        f'forced/unforced classification after controlling for shot pattern and surface."'
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
