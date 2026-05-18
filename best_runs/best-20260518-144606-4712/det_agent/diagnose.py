"""Post-run diagnosis: load an eval_runs/*.json file and identify what hurt.

Usage:
  python -m agents.det_agent.diagnose                # latest run
  python -m agents.det_agent.diagnose <path/to.json> # specific run
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


RUNS_DIR = Path("eval_runs")


def load_latest() -> dict:
    files = sorted(RUNS_DIR.glob("run-*.json"))
    if not files:
        raise FileNotFoundError("No runs in eval_runs/")
    return json.loads(files[-1].read_text()), files[-1]


def diagnose(data: dict) -> None:
    results = data["results"]
    print("\n" + "=" * 80)
    print("  DIAGNOSIS")
    print("=" * 80)

    # Sort by score ascending — worst games first
    worst = sorted(results, key=lambda r: r.get("score", 0))[:6]
    for r in worst:
        score = r.get("score", 0)
        prof = r.get("profit", 0)
        walk = r.get("walk_pen", 0)
        rep = r.get("rep_pen", 0)
        sat = r.get("sat_pen", 0)
        waste = r.get("waste_pen", 0)
        cash = r.get("cash", 0)
        status = r.get("status", "?")
        scen, seed = r.get("scenario", "?"), r.get("seed", 0)
        print(f"\n  [{scen} s={seed}]  score={score:+.0f}  status={status}")
        print(f"    profit={prof:+.0f}  walk={walk:.0f}  rep={rep:.0f}  sat={sat:.0f}  waste={waste:.0f}  cash_end={cash:.0f}")

        days_log = r.get("days_log") or []
        if not days_log:
            continue

        # Days with most walkouts (band Some/Many)
        bad_walkout_days = [d for d in days_log if d.get("walk") in ("Some", "Many")]
        if bad_walkout_days:
            samples = ", ".join(f"d{d['d']}({d['walk']})" for d in bad_walkout_days[:5])
            print(f"    walkout days: {samples}")

        # Days with low covers (<50)
        low_demand_days = [d for d in days_log if (d.get("cov") or 0) < 50]
        if low_demand_days:
            samples = ", ".join(f"d{d['d']}(c{d['cov']})" for d in low_demand_days[:5])
            print(f"    low-demand days: {samples}")

        # Reputation drops
        rep_seq = [d.get("rep") for d in days_log]
        rep_changes = []
        prev = None
        for d in days_log:
            cur = d.get("rep")
            if prev and cur != prev:
                rep_changes.append(f"d{d['d']}:{prev}→{cur}")
            prev = cur
        if rep_changes:
            print(f"    rep transitions: {' '.join(rep_changes)}")

        # Rejections
        rejs = [(d["d"], d["rej_msgs"]) for d in days_log if d.get("rej_msgs")]
        if rejs:
            print(f"    rejections: {rejs[:3]}")

        # Cash trajectory low points
        cash_trajectory = [(d["d"], d["cash"]) for d in days_log if d.get("cash") is not None]
        if cash_trajectory:
            mn = min(cash_trajectory, key=lambda x: x[1])
            print(f"    min cash: d{mn[0]} → {mn[1]:.0f}")

    print("\n" + "=" * 80)
    print("  AGGREGATE FAILURE MODES")
    print("=" * 80)
    total_walk = sum(r.get("walk_pen", 0) for r in results)
    total_rep = sum(r.get("rep_pen", 0) for r in results)
    total_sat = sum(r.get("sat_pen", 0) for r in results)
    total_waste = sum(r.get("waste_pen", 0) for r in results)
    total_profit = sum(r.get("profit", 0) for r in results)
    n = len(results)
    print(f"  avg profit:          {total_profit/n:+,.0f}")
    print(f"  avg walkout penalty: {total_walk/n:,.0f}")
    print(f"  avg rep penalty:     {total_rep/n:,.0f}")
    print(f"  avg sat penalty:     {total_sat/n:,.0f}")
    print(f"  avg waste penalty:   {total_waste/n:,.0f}")
    print()


def main():
    if len(sys.argv) >= 2:
        path = Path(sys.argv[1])
        data = json.loads(path.read_text())
    else:
        data, path = load_latest()
    print(f"Diagnosing: {path}")
    diagnose(data)


if __name__ == "__main__":
    main()
