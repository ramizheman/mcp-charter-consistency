#!/usr/bin/env python3
"""
Run Neo4j error-sequences pipeline and export star-schema CSVs for Power BI.

Dimension tables (one row per entity):
  recorder_dim.csv — one row per recorder; global RCI only; no is_tiebreak
  match_dim.csv    — one row per match; global match_reliability_score only; no is_tiebreak

Fact tables (multiple rows; filter by is_tiebreak slicer in Power BI):
  recorder_experience.csv            — three rows per recorder (True/False/All)
  recorder_summary.csv              — three rows per recorder (True/False/All)
  recorder_bias_by_pattern_surface.csv — one row per recorder×surface×pattern×is_tiebreak
  flagged_points.csv                — one row per flagged point
  match_reliability.csv             — two rows per match (True/False only, no All)

Tiebreak is computed in Python from game_score == "6-6" only (Cypher does not return is_tiebreak).
Requires NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD in .env or environment.
"""

import csv
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(SCRIPT_DIR / ".env")
    load_dotenv()
except ImportError:
    pass

try:
    from match_audit_validator import _normalize_neo4j_shot_to_key, _normalize_surface
except ImportError:
    def _normalize_surface(raw: str) -> str:
        s = (raw or "").lower().strip()
        if not s:
            return "unknown"
        if "hard" in s:
            return "hard"
        if "clay" in s:
            return "clay"
        if "grass" in s:
            return "grass"
        return s or "unknown"

    def _normalize_neo4j_shot_to_key(*args, **kwargs):
        # Minimal fallback: return (phase, key) for one shot
        return ("RALLY", "GS")


def run(
    out_dir: str | None = None,
    min_n_pop: int = 50,
    min_n_rec_suspicious: int = 20,
    suspicious_threshold_pp: float = 25,
    only_csv: str | None = None,
) -> int:
    if out_dir is None:
        out_dir = str(SCRIPT_DIR / "Power BI")
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

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    cypher_points = """
    MATCH (pt:Point)-[:IN_MATCH]->(m:Match)
    MATCH (pt)-[:HAS_SHOT]->(s1:Shot), (pt)-[:HAS_SHOT]->(s2:Shot)
    WHERE pt.outcome IN ['FORCED ERROR', 'UNFORCED ERROR', 'FORCED_ERROR', 'UNFORCED_ERROR']
      AND pt.total_shots >= 3
      AND s1.shot_number = pt.total_shots - 1
      AND s2.shot_number = pt.total_shots - 2
    OPTIONAL MATCH (pl1:Player)-[:HIT]->(s1)
    OPTIONAL MATCH (pl2:Player)-[:HIT]->(s2)
    RETURN m.match_id AS match_id, pt.point_number AS point_number, pt.set_number AS set_number,
           pt.outcome AS outcome,
           s1.shot_number AS n1, s1.shot_type AS t1, s1.contact_type AS c1, s1.direction AS d1,
           s1.intent AS i1, s1.shot_modifier AS m1,
           s2.shot_number AS n2, s2.shot_type AS t2, s2.contact_type AS c2, s2.direction AS d2,
           s2.intent AS i2, s2.shot_modifier AS m2,
           m.surface AS surface, pt.serve_target AS serve_target,
           pl1.handedness AS h1, pl2.handedness AS h2,
           COALESCE(m.charted_by, 'None') AS charted_by,
           pt.game_score AS game_score
    """
    counts = {}
    rec_counts = {}
    point_rows = []
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        driver.verify_connectivity()
        with driver.session() as session:
            result = session.run(cypher_points)
            for rec in result:
                outcome = (rec.get("outcome") or "").upper().replace(" ", "_")
                forced = 1 if outcome == "FORCED_ERROR" else 0
                unforced = 1 if outcome == "UNFORCED_ERROR" else 0
                serve_target = rec.get("serve_target") or ""
                n1, n2 = rec.get("n1") or 0, rec.get("n2") or 0
                h1, h2 = rec.get("h1") or "", rec.get("h2") or ""
                comp_n1 = _normalize_neo4j_shot_to_key(
                    n1, rec.get("t1") or "", rec.get("c1") or "", rec.get("d1") or "",
                    serve_target if n1 == 1 else None, h1,
                    intent=rec.get("i1") or None, shot_modifier=rec.get("m1") or None,
                )
                comp_n2 = _normalize_neo4j_shot_to_key(
                    n2, rec.get("t2") or "", rec.get("c2") or "", rec.get("d2") or "",
                    serve_target if n2 == 1 else None, h2,
                    intent=rec.get("i2") or None, shot_modifier=rec.get("m2") or None,
                )
                surface = _normalize_surface(rec.get("surface"))
                game_score = (rec.get("game_score") or "").strip()
                is_tiebreak = game_score == "6-6"
                key = (surface, (comp_n1, comp_n2), is_tiebreak)
                if key not in counts:
                    counts[key] = {"forced": 0, "unforced": 0}
                counts[key]["forced"] += forced
                counts[key]["unforced"] += unforced
                recorder = (rec.get("charted_by") or "None").strip().lower() or "none"
                rkey = (recorder, surface, (comp_n1, comp_n2), is_tiebreak)
                if rkey not in rec_counts:
                    rec_counts[rkey] = {"forced": 0, "unforced": 0}
                rec_counts[rkey]["forced"] += forced
                rec_counts[rkey]["unforced"] += unforced
                point_rows.append({
                    "match_id": rec.get("match_id") or "",
                    "point_number": rec.get("point_number"),
                    "set_number": rec.get("set_number"),
                    "outcome": outcome,
                    "forced": forced,
                    "surface": surface,
                    "pattern": (comp_n1, comp_n2),
                    "recorder": recorder,
                    "is_tiebreak": is_tiebreak,
                })
        driver.close()
    except Exception as e:
        print(f"Neo4j query failed: {e}", file=sys.stderr)
        return 1

    # Build recorder × is_tiebreak × outcome counts
    recorder_outcome_counts = defaultdict(int)
    for pt in point_rows:
        outcome = "FORCED" if pt["forced"] == 1 else "UNFORCED"
        recorder_outcome_counts[(pt["recorder"], pt["is_tiebreak"], outcome)] += 1

    pop_summary_rows = []
    for (recorder, is_tiebreak, outcome), count in sorted(recorder_outcome_counts.items()):
        pop_summary_rows.append([recorder, "True" if is_tiebreak else "False", outcome, count])

    if only_csv == "population_summary":
        export_ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        with open(out_path / f"population_summary_{export_ts}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
            w.writerow(["recorder", "is_tiebreak", "outcome", "count"])
            for r in pop_summary_rows:
                w.writerow(r)
        print(f"Wrote {out_path / f'population_summary_{export_ts}.csv'}", file=sys.stderr)
        return 0

    pop_forced_pct = {}
    for (surface, pattern, is_tiebreak), v in counts.items():
        total = v["forced"] + v["unforced"]
        if total >= min_n_pop:
            pop_forced_pct[(surface, pattern, is_tiebreak)] = (v["forced"] / total * 100) if total else 0

    bias_rows = []
    for (recorder, surface, pattern, is_tiebreak), v in rec_counts.items():
        total_rec = v["forced"] + v["unforced"]
        pop_key = (surface, pattern, is_tiebreak)
        if pop_key not in pop_forced_pct:
            continue
        rec_forced_pct = (v["forced"] / total_rec * 100) if total_rec else 0
        pop_pct = pop_forced_pct[pop_key]
        bias_pp = rec_forced_pct - pop_pct
        comp_n1, comp_n2 = pattern
        seq_desc = f"({comp_n2[0]},{comp_n2[1]}) -> ({comp_n1[0]},{comp_n1[1]})"
        bias_rows.append((recorder, surface, seq_desc, total_rec, rec_forced_pct, pop_pct, bias_pp, is_tiebreak))

    recorder_weighted_sum = {}
    recorder_total_n = {}
    for recorder, surface, seq_desc, n, rec_forced_pct, pop_pct, bias_pp, is_tiebreak in bias_rows:
        w = n * abs(bias_pp)
        key = (recorder, is_tiebreak)
        recorder_weighted_sum[key] = recorder_weighted_sum.get(key, 0) + w
        recorder_total_n[key] = recorder_total_n.get(key, 0) + n
    rci_list = []
    for (recorder, is_tiebreak) in recorder_total_n:
        total_n = recorder_total_n[(recorder, is_tiebreak)]
        if total_n <= 0:
            continue
        rci = recorder_weighted_sum[(recorder, is_tiebreak)] / total_n
        rci_list.append((recorder, is_tiebreak, rci, total_n))
    rci_list.sort(key=lambda x: x[2])
    # Bug 2 fix: compute mean/SD/threshold separately for regular vs tiebreak so threshold is meaningful
    regular_rci = [r for r in rci_list if r[1] is False]
    tiebreak_rci = [r for r in rci_list if r[1] is True]

    def mean_std_threshold(lst):
        if not lst:
            return 0.0, 0.0, 0.0
        mean_r = sum(r[2] for r in lst) / len(lst)
        var_r = sum((r[2] - mean_r) ** 2 for r in lst) / len(lst)
        std_r = var_r ** 0.5
        return mean_r, std_r, mean_r + std_r

    mean_reg, std_reg, thresh_reg = mean_std_threshold(regular_rci)
    mean_tb, std_tb, thresh_tb = mean_std_threshold(tiebreak_rci)

    recorders_downgrade = set()
    for r in rci_list:
        if r[1] is False and regular_rci and r[2] > thresh_reg:
            recorders_downgrade.add((r[0], r[1]))
        elif r[1] is True and tiebreak_rci and r[2] > thresh_tb:
            recorders_downgrade.add((r[0], r[1]))
    rci_by_recorder = {(r[0], r[1]): r[2] for r in rci_list}

    # Global RCI (all points regardless of tiebreak) for is_tiebreak = "All" rows
    recorder_weighted_sum_global = {}
    recorder_total_n_global = {}
    for recorder, surface, seq_desc, n, rec_forced_pct, pop_pct, bias_pp, is_tiebreak in bias_rows:
        w = n * abs(bias_pp)
        recorder_weighted_sum_global[recorder] = recorder_weighted_sum_global.get(recorder, 0) + w
        recorder_total_n_global[recorder] = recorder_total_n_global.get(recorder, 0) + n
    rci_list_global = []
    for recorder in recorder_total_n_global:
        total_n = recorder_total_n_global[recorder]
        if total_n <= 0:
            continue
        rci_g = recorder_weighted_sum_global[recorder] / total_n
        rci_list_global.append((recorder, rci_g, total_n))
    rci_list_global.sort(key=lambda x: x[1])
    if rci_list_global:
        mean_g = sum(r[1] for r in rci_list_global) / len(rci_list_global)
        var_g = sum((r[1] - mean_g) ** 2 for r in rci_list_global) / len(rci_list_global)
        std_g = var_g ** 0.5
        thresh_g = mean_g + std_g
    else:
        mean_g = std_g = thresh_g = 0.0
    recorders_downgrade_global = {r[0] for r in rci_list_global if rci_list_global and r[1] > thresh_g}
    rci_by_recorder_global = {r[0]: r[1] for r in rci_list_global}

    suspicious_cells = []
    cell_n = {}
    cell_rec_pct = {}
    cell_pop_pct = {}
    for (recorder, surface, pattern, is_tiebreak), v in rec_counts.items():
        total_rec = v["forced"] + v["unforced"]
        if total_rec < min_n_rec_suspicious:
            continue
        pop_key = (surface, pattern, is_tiebreak)
        if pop_key not in pop_forced_pct:
            continue
        rec_pct = (v["forced"] / total_rec * 100) if total_rec else 0
        pop_pct = pop_forced_pct[pop_key]
        bias_pp = abs(rec_pct - pop_pct)
        if bias_pp > suspicious_threshold_pp:
            rkey = (recorder, surface, pattern, is_tiebreak)
            suspicious_cells.append(rkey)
            cell_n[rkey] = total_rec
            cell_rec_pct[rkey] = rec_pct
            cell_pop_pct[rkey] = pop_pct
    flagged_points = []
    for pt in point_rows:
        rkey = (pt["recorder"], pt["surface"], pt["pattern"], pt["is_tiebreak"])
        if rkey not in cell_pop_pct:
            continue
        comp_n1, comp_n2 = pt["pattern"]
        seq_desc = f"({comp_n2[0]},{comp_n2[1]}) -> ({comp_n1[0]},{comp_n1[1]})"
        flagged_points.append({
            "match_id": pt["match_id"],
            "point_number": pt["point_number"],
            "pattern": seq_desc,
            "surface": pt["surface"],
            "recorder": pt["recorder"],
            "population_forced_pct": cell_pop_pct[rkey],
            "recorder_forced_pct": cell_rec_pct[rkey],
            "coder_label": pt["outcome"],
            "flag_reason": f"recorder×pattern bias > {int(suspicious_threshold_pp)} pp",
            "is_tiebreak": pt["is_tiebreak"],
        })

    matches = []
    match_dim_rows = []
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as session:
            res = session.run(
                "MATCH (m:Match) RETURN m.match_id AS match_id, COALESCE(m.charted_by, 'None') AS charted_by, "
                "m.surface AS surface, m.date AS date, m.tour AS tour"
            )
            for rec in res:
                match_id = rec.get("match_id") or ""
                recorder = (rec.get("charted_by") or "None").strip().lower() or "none"
                surface = _normalize_surface(rec.get("surface"))
                date_val = rec.get("date")
                if hasattr(date_val, "isoformat"):
                    date_str = date_val.isoformat()[:10]
                else:
                    date_str = str(date_val)[:10] if date_val else ""
                tournament = (rec.get("tour") or "").strip() or ""
                score_global = 0.9 if recorder in recorders_downgrade_global else 1.0
                match_dim_rows.append([match_id, recorder, surface, tournament, date_str, score_global])
                for is_tiebreak in (True, False):
                    rci = rci_by_recorder.get((recorder, is_tiebreak))
                    if rci is None:
                        rci = ""
                    score = 0.9 if (recorder, is_tiebreak) in recorders_downgrade else 1.0
                    matches.append({
                        "match_id": match_id, "recorder": recorder, "surface": surface,
                        "tournament": tournament, "date": date_str,
                        "RCI": rci, "match_reliability_score": score,
                        "is_tiebreak": is_tiebreak,
                    })
        driver.close()
    except Exception as e:
        print(f"Neo4j match query failed: {e}", file=sys.stderr)

    # Recorder-level experience table: one row per (recorder, is_tiebreak) with is_tiebreak in (True, False, "All").
    # matches_coded is global (one count per match) for all three; RCI/tier per is_tiebreak. Filter is_tiebreak="All" in Power BI for scatter.
    rec_matches = []
    seen_match_rec = set()
    for m in matches:
        key = (m["match_id"], m["recorder"])
        if key in seen_match_rec:
            continue
        seen_match_rec.add(key)
        rec_matches.append((m["recorder"], m["surface"], m["date"]))
    by_recorder = {}
    for recorder, surface, date_str in rec_matches:
        if recorder not in by_recorder:
            by_recorder[recorder] = {"surfaces": [], "dates": []}
        by_recorder[recorder]["surfaces"].append(surface)
        by_recorder[recorder]["dates"].append(date_str)
    recorder_experience_rows = []
    for recorder in sorted(by_recorder.keys()):
        surfs = [s for s in by_recorder[recorder]["surfaces"] if s and s != "unknown"]
        dates = [d for d in by_recorder[recorder]["dates"] if d and len(d) >= 10]
        matches_coded = len(by_recorder[recorder]["surfaces"])
        primary_surface = Counter(surfs).most_common(1)[0][0] if surfs else "unknown"
        date_min = min(dates) if dates else ""
        date_max = max(dates) if dates else ""
        date_range = f"{date_min} to {date_max}" if date_min and date_max else (date_min or date_max or "")
        for is_tiebreak, tb_str in [(False, "False"), (True, "True")]:
            rci = rci_by_recorder.get((recorder, is_tiebreak))
            rci_str = f"{rci:.2f}" if rci is not None else ""
            tier = "downgrade" if (recorder, is_tiebreak) in recorders_downgrade else "normal"
            recorder_experience_rows.append([
                recorder, matches_coded, primary_surface, date_min, date_max, date_range, rci_str, tier, tb_str,
            ])
        rci_all = rci_by_recorder_global.get(recorder)
        rci_all_str = f"{rci_all:.2f}" if rci_all is not None else ""
        tier_all = "downgrade" if recorder in recorders_downgrade_global else "normal"
        recorder_experience_rows.append([
            recorder, matches_coded, primary_surface, date_min, date_max, date_range, rci_all_str, tier_all, "All",
        ])

    recorder_dim_rows = []
    for recorder in sorted(by_recorder.keys()):
        surfs = [s for s in by_recorder[recorder]["surfaces"] if s and s != "unknown"]
        dates = [d for d in by_recorder[recorder]["dates"] if d and len(d) >= 10]
        matches_coded = len(by_recorder[recorder]["surfaces"])
        primary_surface = Counter(surfs).most_common(1)[0][0] if surfs else "unknown"
        date_min = min(dates) if dates else ""
        date_max = max(dates) if dates else ""
        date_range = f"{date_min} to {date_max}" if date_min and date_max else (date_min or date_max or "")
        rci_str = f"{rci_by_recorder_global[recorder]:.2f}" if recorder in rci_by_recorder_global else ""
        tier = "downgrade" if recorder in recorders_downgrade_global else "normal"
        recorder_dim_rows.append([
            recorder, matches_coded, primary_surface, date_min, date_max, date_range, rci_str, tier,
        ])

    export_ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    def write_csv(path: Path, headers: list, rows: list, quoting=csv.QUOTE_MINIMAL):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, quoting=quoting)
            w.writerow(headers)
            for r in rows:
                w.writerow(r)

    write_csv(
        out_path / f"recorder_dim_{export_ts}.csv",
        ["recorder", "matches_coded", "primary_surface", "date_min", "date_max", "date_range", "RCI_score", "reliability_tier"],
        recorder_dim_rows,
    )
    print(f"Wrote {out_path / f'recorder_dim_{export_ts}.csv'}", file=sys.stderr)

    write_csv(
        out_path / f"match_dim_{export_ts}.csv",
        ["match_id", "recorder", "surface", "tournament", "date", "match_reliability_score"],
        match_dim_rows,
    )
    print(f"Wrote {out_path / f'match_dim_{export_ts}.csv'}", file=sys.stderr)

    write_csv(
        out_path / f"recorder_bias_by_pattern_surface_{export_ts}.csv",
        ["recorder", "surface", "pattern", "population_forced_pct", "recorder_forced_pct", "bias_pp", "n", "is_tiebreak"],
        [[rec, sur, pat, f"{pop:.2f}", f"{rec_pct:.2f}", f"{bias_pp:.2f}", n, "True" if tb else "False"] for rec, sur, pat, n, rec_pct, pop, bias_pp, tb in bias_rows],
    )
    print(f"Wrote {out_path / f'recorder_bias_by_pattern_surface_{export_ts}.csv'}", file=sys.stderr)

    write_csv(
        out_path / f"population_summary_{export_ts}.csv",
        ["recorder", "is_tiebreak", "outcome", "count"],
        pop_summary_rows,
    )
    print(f"Wrote {out_path / f'population_summary_{export_ts}.csv'}", file=sys.stderr)

    recorder_summary_rows = []
    for recorder, is_tiebreak, rci, total_n in rci_list:
        tier = "downgrade" if (recorder, is_tiebreak) in recorders_downgrade else "normal"
        tb_str = "True" if is_tiebreak else "False"
        recorder_summary_rows.append([recorder, total_n, f"{rci:.2f}", f"{rci:.2f}", tier, tb_str])
    for recorder, rci_g, total_n in rci_list_global:
        tier = "downgrade" if recorder in recorders_downgrade_global else "normal"
        recorder_summary_rows.append([recorder, total_n, f"{rci_g:.2f}", f"{rci_g:.2f}", tier, "All"])
    write_csv(
        out_path / f"recorder_summary_{export_ts}.csv",
        ["recorder", "total_points_coded", "mean_absolute_bias", "RCI_score", "reliability_tier", "is_tiebreak"],
        recorder_summary_rows,
    )
    print(f"Wrote {out_path / f'recorder_summary_{export_ts}.csv'}", file=sys.stderr)

    write_csv(
        out_path / f"recorder_experience_{export_ts}.csv",
        ["recorder", "matches_coded", "primary_surface", "date_min", "date_max", "date_range", "RCI_score", "reliability_tier", "is_tiebreak"],
        recorder_experience_rows,
    )
    print(f"Wrote {out_path / f'recorder_experience_{export_ts}.csv'}", file=sys.stderr)

    match_rows = [
        [m["match_id"], m["recorder"], m["surface"], m["tournament"], m["date"],
        m["RCI"] if m["RCI"] != "" else "", m["match_reliability_score"], "True" if m["is_tiebreak"] else "False"]
        for m in matches
    ]
    write_csv(
        out_path / f"match_reliability_{export_ts}.csv",
        ["match_id", "recorder", "surface", "tournament", "date", "RCI", "match_reliability_score", "is_tiebreak"],
        match_rows,
    )
    print(f"Wrote {out_path / f'match_reliability_{export_ts}.csv'}", file=sys.stderr)

    flag_rows = [
        [p["match_id"], p["point_number"], p["pattern"], p["surface"], p["recorder"],
        f"{p['population_forced_pct']:.2f}", f"{p['recorder_forced_pct']:.2f}", p["coder_label"], p["flag_reason"], "True" if p["is_tiebreak"] else "False"]
        for p in flagged_points
    ]
    write_csv(
        out_path / f"flagged_points_{export_ts}.csv",
        ["match_id", "point_number", "pattern", "surface", "recorder", "population_forced_pct", "recorder_forced_pct", "coder_label", "flag_reason", "is_tiebreak"],
        flag_rows,
    )
    print(f"Wrote {out_path / f'flagged_points_{export_ts}.csv'} ({len(flagged_points)} rows)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Export four error-sequences CSVs from Neo4j.")
    ap.add_argument("--out-dir", "-o", default=str(SCRIPT_DIR / "Power BI"), help="Output directory for CSVs (default: Tennis NL/Power BI)")
    ap.add_argument("--min-n-pop", type=int, default=50, help="Min population n per pattern (default 50)")
    ap.add_argument("--min-n-rec-suspicious", type=int, default=20, help="Min recorder n for flagged cells (default 20)")
    ap.add_argument("--suspicious-threshold-pp", type=float, default=25, help="Bias threshold pp for flagged cells (default 25)")
    ap.add_argument("--only", dest="only_csv", default=None, metavar="CSV", help="Export only this CSV (e.g. population_summary)")
    args = ap.parse_args()
    sys.exit(run(
        out_dir=args.out_dir,
        min_n_pop=args.min_n_pop,
        min_n_rec_suspicious=args.min_n_rec_suspicious,
        suspicious_threshold_pp=args.suspicious_threshold_pp,
        only_csv=args.only_csv,
    ))
