"""Re-print the summary for an existing run_id, or list all runs.

    python -m agents.auto_research.summary                  # list all run_ids
    python -m agents.auto_research.summary <run_id>         # short summary
    python -m agents.auto_research.summary <run_id> --full  # per-game rows
    python -m agents.auto_research.summary --best           # current best.json
"""

from __future__ import annotations

import argparse
import json
import sys

from .benchmark import RUNS_DIR, list_runs, load_manifest, RunResult, print_summary


def _result_from_manifest(d: dict) -> RunResult:
    return RunResult(**d)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("run_id", nargs="?", help="Run ID; if omitted, list all runs.")
    p.add_argument("--full", action="store_true", help="Print per-game rows.")
    p.add_argument("--best", action="store_true", help="Print contents of best.json.")
    args = p.parse_args()

    if args.best:
        best = RUNS_DIR / "best.json"
        if not best.exists():
            print("No best.json — run `python -m agents.auto_research.tune` first.")
            sys.exit(1)
        with open(best) as f:
            data = json.load(f)
        print(json.dumps(data, indent=2, default=str))
        return

    if not args.run_id:
        runs = list_runs()
        if not runs:
            print("No runs found in", RUNS_DIR)
            return
        print(f"Available runs ({len(runs)}):")
        for r in runs[-30:]:
            try:
                m = load_manifest(r)
                print(f"  {r}  robust={m['robust_score']:>10.0f}  mean={m['mean_score']:>10.0f}  "
                      f"bk={m['bankruptcies']}  label={m.get('label','')!r}")
            except Exception as e:
                print(f"  {r}  (could not load: {e!r})")
        return

    manifest = load_manifest(args.run_id)
    res = _result_from_manifest(manifest)
    print_summary(res, full=args.full)


if __name__ == "__main__":
    main()
