"""Multi-variant parallel runner.

Spawns N variant subprocesses, each running its own 12-game eval with
config env vars set differently. Aggregates results, identifies the best
variant, snapshots it.

Usage:
  python -m agents.det_agent.run_variants
  python -m agents.det_agent.run_variants --variants v2a,v2b,v2c
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import champion

VARIANTS: dict[str, dict[str, str]] = {
    # v3 sweep — built on tight_orders winner + new DOW priors in constants.py
    "tight":         {"DET_OVER_ORDER": "1.15", "DET_BUFFER_FRESH": "2",
                      "DET_BUFFER_DRY": "4"},
    "tight_hot":     {"DET_OVER_ORDER": "1.15", "DET_BUFFER_FRESH": "2",
                      "DET_BUFFER_DRY": "4",
                      "DET_BASE_PRICE_SHIFT": "0.08", "DET_PREMIUM_EXTRA": "0.06"},
    "tight_mkt":     {"DET_OVER_ORDER": "1.15", "DET_BUFFER_FRESH": "2",
                      "DET_BUFFER_DRY": "4",
                      "DET_MARKETING_MODE": "heavy"},
    "tight_hot_mkt": {"DET_OVER_ORDER": "1.15", "DET_BUFFER_FRESH": "2",
                      "DET_BUFFER_DRY": "4",
                      "DET_BASE_PRICE_SHIFT": "0.08", "DET_PREMIUM_EXTRA": "0.06",
                      "DET_MARKETING_MODE": "heavy"},
    "ultra_tight":   {"DET_OVER_ORDER": "1.10", "DET_BUFFER_FRESH": "2",
                      "DET_BUFFER_DRY": "3"},
    "tight_lean":    {"DET_OVER_ORDER": "1.15", "DET_BUFFER_FRESH": "2",
                      "DET_BUFFER_DRY": "4",
                      "DET_STAFF_DELTA": "-1"},
    "tight_hot_lean": {"DET_OVER_ORDER": "1.15", "DET_BUFFER_FRESH": "2",
                      "DET_BUFFER_DRY": "4",
                      "DET_BASE_PRICE_SHIFT": "0.08", "DET_PREMIUM_EXTRA": "0.06",
                      "DET_STAFF_DELTA": "-1"},
    "tight_warm":    {"DET_OVER_ORDER": "1.15", "DET_BUFFER_FRESH": "2",
                      "DET_BUFFER_DRY": "4",
                      "DET_BASE_PRICE_SHIFT": "0.07", "DET_PREMIUM_EXTRA": "0.05"},
}

EVAL_RUNS_DIR = Path("eval_runs")


def run_variant(name: str, env_overrides: dict[str, str], scenarios: str, seeds: str,
                url: str, no_llm: bool) -> dict:
    """Run a single variant in a subprocess. Returns {name, avg, ...}."""
    env = os.environ.copy()
    env.update(env_overrides)
    env["DET_VARIANT"] = name
    if no_llm:
        env["DET_AGENT_LLM"] = "0"
    cmd = [
        sys.executable, "-m", "agents.det_agent.eval_parallel",
        "--scenarios", scenarios,
        "--seeds", seeds,
        "--url", url,
        "--team-prefix", f"det-{name}",
        "--parallel", "6",
        "--label", name,
        "--no-capture",
    ]
    if no_llm:
        cmd.append("--no-llm")
    started = time.time()
    out_file = EVAL_RUNS_DIR / f"variant-{name}.log"
    EVAL_RUNS_DIR.mkdir(exist_ok=True)
    print(f"[{name}] starting — overrides={env_overrides}")
    with out_file.open("w") as f:
        p = subprocess.run(cmd, env=env, stdout=f, stderr=subprocess.STDOUT)
    elapsed = time.time() - started
    # Find latest saved run with this label
    runs = sorted(EVAL_RUNS_DIR.glob(f"run-*-{name}.json"))
    if not runs:
        return {"name": name, "error": "no run file found", "elapsed_s": elapsed}
    run_path = runs[-1]
    data = json.loads(run_path.read_text())
    scores = [r.get("score", 0) for r in data["results"]]
    bk = sum(1 for r in data["results"] if r.get("status") == "bankrupt")
    avg = sum(scores) / len(scores) if scores else 0
    per_scen: dict[str, float] = {}
    for r in data["results"]:
        per_scen.setdefault(r["scenario"], []).append(r.get("score", 0))
    per_scen_avg = {k: sum(v)/len(v) for k, v in per_scen.items()}
    print(f"[{name}] done in {elapsed:.0f}s — avg={avg:+,.0f} bankruptcies={bk}")
    return {
        "name": name,
        "avg": avg,
        "bankruptcies": bk,
        "per_scen": per_scen_avg,
        "run_path": str(run_path),
        "elapsed_s": elapsed,
        "overrides": env_overrides,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--variants", default="",
                   help="Comma-separated variant names; default all.")
    p.add_argument("--scenarios", default="baseline,supply_crisis,tourist_season,renovation")
    p.add_argument("--seeds", default="42,88,123")
    p.add_argument("--url", default=os.getenv("RESTBENCH_URL", "http://52.48.183.209:8001"))
    p.add_argument("--parallel", type=int, default=4,
                   help="Number of variants to run concurrently.")
    p.add_argument("--no-llm", action="store_true")
    args = p.parse_args()

    if args.variants:
        names = [n.strip() for n in args.variants.split(",") if n.strip()]
    else:
        names = list(VARIANTS.keys())

    todo = [(n, VARIANTS.get(n, {})) for n in names]

    print(f"Running {len(todo)} variants ({args.parallel} parallel)")
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {
            pool.submit(run_variant, n, env, args.scenarios, args.seeds, args.url, args.no_llm): n
            for n, env in todo
        }
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)

    print("\n" + "=" * 80)
    print("  VARIANT RANKINGS")
    print("=" * 80)
    results.sort(key=lambda r: -r.get("avg", float("-inf")))
    for r in results:
        ps = r.get("per_scen", {})
        ps_str = " ".join(f"{k[:3]}:{v:+.0f}" for k, v in ps.items())
        print(f"  {r['name']:<18} avg={r.get('avg', 0):+8.0f}  bk={r.get('bankruptcies',0)}  "
              f"({ps_str})")

    # Snapshot the winner if it beats the current best
    if results and "avg" in results[0]:
        winner = results[0]
        from pathlib import Path
        run_path = Path(winner["run_path"])
        data = json.loads(run_path.read_text())
        data["label"] = winner["name"]
        meta = champion.maybe_snapshot(run_path, data, Path("agents/det_agent"))
        if meta:
            print(f"\n  ⭐ NEW CHAMPION: {winner['name']} → avg {meta['avg_score']:+.0f}")
            print(f"     snapshot: {meta['snapshot_dir']}")
        else:
            cur_best = champion.current_best_score()
            print(f"\n  No new champion (current best: {cur_best:+.0f})")


if __name__ == "__main__":
    main()
